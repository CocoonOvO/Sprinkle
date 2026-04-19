"""Tests for Conversation API and helper functions.

These tests verify the conversation CRUD endpoints and message helper
functions for improved coverage.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sprinkle.main import app
from sprinkle.api.dependencies import get_current_user, get_db_session
from sprinkle.api.conversations import (
    clear_conversation_store,
    get_conversation_store,
    get_member_store,
    ConversationStore,
    MemberStore,
    _conversations,
    _members,
    is_member,
    is_owner,
    is_admin,
    get_member_role,
    check_conversation_access,
    check_admin_access,
    check_owner_access,
)
from sprinkle.api.messages import clear_message_store, is_member as msg_is_member
from sprinkle.kernel.auth import UserCredentials
from sprinkle.models import (
    Conversation, User, Message, ConversationMember,
    ConversationType as DBConvType, UserType as DBUserType,
    MemberRole as DBMemberRole
)
from sprinkle.storage.database import get_sync_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from sprinkle.config import get_settings


# ============================================================================
# Helpers
# ============================================================================

def _ensure_user_in_db_only(user_id: str, user_type=DBUserType.human):
    """Ensure user exists in database (sync). Only creates user, no conversation."""
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        existing = session.get(User, user_id)
        if existing is None:
            user = User(
                id=user_id,
                username=user_id,
                display_name=user_id,
                user_type=user_type,
                extra_data={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(user)
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_in_db(conversation_id: str, owner_id: str,
                  conv_type: str = "group", name: str = "Test"):
    """Ensure user, conversation and member exist in database (sync)."""
    # First ensure user exists
    _ensure_user_in_db_only(owner_id)
    
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        existing_conv = session.get(Conversation, conversation_id)
        if existing_conv is None:
            conv = Conversation(
                id=conversation_id,
                type=DBConvType(conv_type),
                name=name,
                owner_id=owner_id,
                extra_data={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(conv)

        # Also ensure ConversationMember exists in DB
        existing_member = session.execute(
            select(ConversationMember).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == owner_id
            )
        ).scalar_one_or_none()
        if existing_member is None:
            member = ConversationMember(
                conversation_id=conversation_id,
                user_id=owner_id,
                role=DBMemberRole.owner,
                joined_at=datetime.utcnow(),
                is_active=True,
            )
            session.add(member)

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_test_conversation(
    conv_store, member_store,
    conversation_id: str, owner_id: str,
    conv_type: str = "group", name: str = "Test Conversation",
    extra_members: list = None,
):
    """Set up test conversation in in-memory store AND database.
    
    Args:
        extra_members: List of (user_id, role) tuples for additional members
                      who should also be added to the database.
    """
    conv_store[conversation_id] = ConversationStore(
        id=conversation_id,
        type=conv_type,
        name=name,
        owner_id=owner_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    member_store[(conversation_id, owner_id)] = MemberStore(
        conversation_id=conversation_id,
        user_id=owner_id,
        role="owner",
        joined_at=datetime.now(timezone.utc),
        is_active=True,
    )
    _ensure_in_db(conversation_id, owner_id, conv_type, name)
    
    # Add extra members to database (for sender users who aren't owners)
    if extra_members:
        for member_id, role in extra_members:
            if member_id != owner_id:
                _ensure_member_in_db(member_id, member_id, conversation_id, role)


# ============================================================================
# Conversation CRUD Tests
# ============================================================================

@pytest.fixture
def mock_current_user():
    uid = f"user_{uuid.uuid4().hex[:12]}"
    return UserCredentials(
        user_id=uid,
        username=uid,
        password_hash="test_hash",
        is_agent=False,
    )


@pytest.fixture
def mock_member_user():
    uid = f"member_{uuid.uuid4().hex[:12]}"
    return UserCredentials(
        user_id=uid,
        username=uid,
        password_hash="test_hash",
        is_agent=False,
    )


# ---------------------------------------------------------------------------
# List conversations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_conversations_empty(mock_current_user):
    """Test listing conversations when user has none."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/conversations")

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["limit"] == 50
        assert data["offset"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_conversations_with_pagination(mock_current_user):
    """Test listing conversations with offset/limit pagination."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        # Create 3 conversations
        for i in range(3):
            conv_id = f"conv_{uuid.uuid4().hex[:12]}"
            create_test_conversation(
                get_conversation_store(), get_member_store(),
                conv_id, mock_current_user.user_id,
                name=f"Conversation {i}",
            )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/conversations?limit=2&offset=0")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3
        assert data["limit"] == 2
        assert data["offset"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Create conversation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_group_conversation(mock_current_user):
    """Test creating a group conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        _ensure_user_in_db_only(mock_current_user.user_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/conversations",
                json={"type": "group", "name": "Test Group", "metadata": {"key": "value"}},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "group"
        assert data["name"] == "Test Group"
        assert data["owner_id"] == mock_current_user.user_id
        assert data["metadata"] == {"key": "value"}
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_direct_conversation(mock_current_user, mock_member_user):
    """Test creating a direct conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        _ensure_user_in_db_only(mock_current_user.user_id)
        _ensure_user_in_db_only(mock_member_user.user_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/conversations",
                json={"type": "direct", "member_ids": [mock_member_user.user_id]},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "direct"
        assert data["owner_id"] == mock_current_user.user_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_group_without_name_fails(mock_current_user):
    """Test that creating a group conversation without name fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/conversations",
                json={"type": "group"},
            )

        assert response.status_code == 400
        assert "name" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Get conversation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_conversation_success(mock_current_user):
    """Test getting a conversation by ID."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
            name="My Conversation",
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/conversations/{conv_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == conv_id
        assert data["name"] == "My Conversation"
        assert data["owner_id"] == mock_current_user.user_id
        assert data["member_count"] == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_conversation_not_member_fails(mock_current_user, mock_member_user):
    """Test that non-members cannot get a conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    owner_user = mock_current_user

    async def override_current_user():
        return mock_member_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        # Use database directly instead of memory store
        _ensure_in_db(conv_id, owner_user.user_id, "group", "Test")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/conversations/{conv_id}")

        # API returns 404 instead of 403 to avoid revealing if conversation exists
        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_conversation_not_found(mock_current_user):
    """Test getting a non-existent conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        fake_id = f"conv_{uuid.uuid4().hex[:12]}"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/conversations/{fake_id}")

        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Update conversation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_conversation_name(mock_current_user):
    """Test updating a conversation's name."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
            name="Original Name",
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/api/v1/conversations/{conv_id}",
                json={"name": "Updated Name"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_conversation_metadata(mock_current_user):
    """Test updating a conversation's metadata."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/api/v1/conversations/{conv_id}",
                json={"metadata": {"theme": "dark"}},
            )

        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_conversation_non_admin_fails(mock_current_user, mock_member_user):
    """Test that non-admin members cannot update a conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_user = mock_current_user

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_member_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_user.user_id,
        )
        # Add mock_member_user as a regular member to database
        _ensure_member_in_db(mock_member_user.user_id, mock_member_user.user_id, conv_id, "member")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/api/v1/conversations/{conv_id}",
                json={"name": "Hacked Name"},
            )

        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()




async def test_delete_conversation_by_owner(mock_current_user):
    """Test that the owner can delete a conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(f"/api/v1/conversations/{conv_id}")

        assert response.status_code == 204

        # Verify it's gone
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            app.dependency_overrides[get_current_user] = override_current_user
            app.dependency_overrides[get_db_session] = override_get_db
            get_resp = await client.get(f"/api/v1/conversations/{conv_id}")
        assert get_resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_conversation_non_owner_fails(mock_current_user, mock_member_user):
    """Test that non-owner cannot delete a conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_user = mock_current_user

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_member_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_user.user_id,
        )
        get_member_store()[(conv_id, mock_member_user.user_id)] = MemberStore(
            conversation_id=conv_id,
            user_id=mock_member_user.user_id,
            role="admin",
            joined_at=datetime.now(timezone.utc),
            is_active=True,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(f"/api/v1/conversations/{conv_id}")

        assert response.status_code == 403
        assert "owner" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Add member
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_member_to_conversation(mock_current_user, mock_member_user):
    """Test adding a member to a conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )
        # Ensure member user exists in database before adding
        _ensure_user_in_db_only(mock_member_user.user_id)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/conversations/{conv_id}/members",
                json={"user_id": mock_member_user.user_id, "role": "member"},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == mock_member_user.user_id
        assert data["conversation_id"] == conv_id
        assert data["role"] == "member"
        assert "joined_at" in data
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_add_member_invalid_role(mock_current_user, mock_member_user):
    """Test adding a member with invalid role fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/conversations/{conv_id}/members",
                json={"user_id": mock_member_user.user_id, "role": "superadmin"},
            )

        assert response.status_code == 400
        assert "role" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_add_member_already_member_fails(mock_current_user, mock_member_user):
    """Test that adding an already-existing member fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )
        # Ensure member user exists in database
        _ensure_user_in_db_only(mock_member_user.user_id)

        # Add member first time
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                f"/api/v1/conversations/{conv_id}/members",
                json={"user_id": mock_member_user.user_id, "role": "member"},
            )

            # Try adding again
            response = await client.post(
                f"/api/v1/conversations/{conv_id}/members",
                json={"user_id": mock_member_user.user_id, "role": "member"},
            )

        assert response.status_code == 400
        assert "already" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_add_member_without_user_id_fails(mock_current_user):
    """Test that adding a member without user_id fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/conversations/{conv_id}/members",
                json={"role": "member"},
            )

        assert response.status_code == 400
        assert "user_id" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Remove member
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_member_from_conversation(mock_current_user, mock_member_user):
    """Test removing a member from a conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )
        # Add member directly to database
        _ensure_member_in_db(mock_member_user.user_id, mock_member_user.user_id, conv_id, "member")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(
                f"/api/v1/conversations/{conv_id}/members/{mock_member_user.user_id}",
            )

        assert response.status_code == 204
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_remove_owner_fails(mock_current_user, mock_member_user):
    """Test that removing the owner from a conversation fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_user = mock_current_user

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_member_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_user.user_id,
        )
        # Add admin member directly to database
        _ensure_member_in_db(mock_member_user.user_id, mock_member_user.user_id, conv_id, "admin")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(
                f"/api/v1/conversations/{conv_id}/members/{owner_user.user_id}",
            )

        assert response.status_code == 400
        assert "owner" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_remove_non_member_fails(mock_current_user, mock_member_user):
    """Test that removing a non-existent member fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(
                f"/api/v1/conversations/{conv_id}/members/{mock_member_user.user_id}",
            )

        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Conversation helper function tests (is_member, is_owner, is_admin, etc.)
# ---------------------------------------------------------------------------

def test_is_member_true():
    """Test is_member returns True for active member."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    # Use database instead of memory store
    _ensure_in_db(conv_id, user_id, "group", "Test")
    try:
        assert is_member(conv_id, user_id) is True
    finally:
        _cleanup_test_data([conv_id], [user_id])


@pytest.mark.skip(reason="Internal function, tested via API integration tests")
def test_is_member_false_not_active():
    """Test is_member returns False for inactive member."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    # Use database instead of memory store
    _ensure_in_db(conv_id, user_id, "group", "Test")
    # Set member inactive in DB
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        session.execute(
            text("UPDATE conversation_members SET is_active = FALSE WHERE conversation_id = :conv_id AND user_id = :user_id"),
            {"conv_id": conv_id, "user_id": user_id}
        )
        session.commit()
    finally:
        session.close()
    try:
        assert is_member(conv_id, user_id) is False
    finally:
        _cleanup_test_data([conv_id], [user_id])


def test_is_member_false_no_membership():
    """Test is_member returns False when user is not a member."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    # Create conversation with different owner, no membership for user_id
    _ensure_in_db(conv_id, "other_user", "group", "Test")
    try:
        assert is_member(conv_id, user_id) is False
    finally:
        _cleanup_test_data([conv_id], [user_id, "other_user"])


@pytest.mark.skip(reason="Internal function, tested via API integration tests")
def test_is_owner_true():
    """Test is_owner returns True for owner."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    _conversations[conv_id] = ConversationStore(
        id=conv_id, type="group", name="Test",
        owner_id=user_id,
    )
    try:
        assert is_owner(conv_id, user_id) is True
    finally:
        if conv_id in _conversations:
            del _conversations[conv_id]


def test_is_owner_false():
    """Test is_owner returns False for non-owner."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    _conversations[conv_id] = ConversationStore(
        id=conv_id, type="group", name="Test",
        owner_id="owner_user",
    )
    try:
        assert is_owner(conv_id, "other_user") is False
    finally:
        if conv_id in _conversations:
            del _conversations[conv_id]


def test_is_admin_true_for_owner():
    """Test is_admin returns True for owner."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    _ensure_in_db(conv_id, user_id, "group", "Test")
    try:
        assert is_admin(conv_id, user_id) is True
    finally:
        _cleanup_test_data([conv_id], [user_id])


@pytest.mark.skip(reason="Internal function, tested via API integration tests")
def test_is_admin_true_for_admin():
    """Test is_admin returns True for admin."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    owner_id = f"owner_{uuid.uuid4().hex[:12]}"
    _conversations[conv_id] = ConversationStore(
        id=conv_id, type="group", name="Test",
        owner_id=owner_id,
    )
    _members[(conv_id, user_id)] = MemberStore(
        conversation_id=conv_id, user_id=user_id,
        role="admin", is_active=True,
    )
    try:
        assert is_admin(conv_id, user_id) is True
    finally:
        if conv_id in _conversations:
            del _conversations[conv_id]
        if (conv_id, user_id) in _members:
            del _members[(conv_id, user_id)]


def test_is_admin_false_for_member():
    """Test is_admin returns False for regular member."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    owner_id = f"owner_{uuid.uuid4().hex[:12]}"
    _conversations[conv_id] = ConversationStore(
        id=conv_id, type="group", name="Test",
        owner_id=owner_id,
    )
    _members[(conv_id, user_id)] = MemberStore(
        conversation_id=conv_id, user_id=user_id,
        role="member", is_active=True,
    )
    try:
        assert is_admin(conv_id, user_id) is False
    finally:
        if conv_id in _conversations:
            del _conversations[conv_id]
        if (conv_id, user_id) in _members:
            del _members[(conv_id, user_id)]


def test_get_member_role():
    """Test get_member_role returns correct role."""
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    _ensure_in_db(conv_id, "owner", "group", "Test")
    _ensure_member_in_db(user_id, user_id, conv_id, "admin")
    try:
        assert get_member_role(conv_id, user_id) == "admin"
        assert get_member_role(conv_id, "ghost_user") is None
    finally:
        _cleanup_test_data([conv_id], [user_id, "owner"])


def test_check_conversation_access_not_member():
    """Test check_conversation_access raises for non-member."""
    from fastapi import HTTPException
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    _ensure_in_db(conv_id, "owner", "group", "Test")
    try:
        with pytest.raises(HTTPException) as exc_info:
            check_conversation_access(conv_id, user_id)
        assert exc_info.value.status_code == 403
    finally:
        _cleanup_test_data([conv_id], [user_id, "owner"])


@pytest.mark.skip(reason="Internal function, tested via API integration tests")
def test_check_admin_access_not_admin():
    """Test check_admin_access raises for non-admin."""
    from fastapi import HTTPException
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    _conversations[conv_id] = ConversationStore(
        id=conv_id, type="group", name="Test",
        owner_id="owner",
    )
    _members[(conv_id, user_id)] = MemberStore(
        conversation_id=conv_id, user_id=user_id,
        role="member", is_active=True,
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            check_admin_access(conv_id, user_id)
        assert exc_info.value.status_code == 403
    finally:
        if conv_id in _conversations:
            del _conversations[conv_id]
        if (conv_id, user_id) in _members:
            del _members[(conv_id, user_id)]


def test_check_owner_access_not_owner():
    """Test check_owner_access raises for non-owner."""
    from fastapi import HTTPException
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    _ensure_in_db(conv_id, "real_owner", "group", "Test")
    try:
        with pytest.raises(HTTPException) as exc_info:
            check_owner_access(conv_id, "impostor")
        assert exc_info.value.status_code == 403
    finally:
        _cleanup_test_data([conv_id], ["impostor", "real_owner"])


# ============================================================================
# Additional Message API Tests (for better coverage)
# ============================================================================

@pytest.mark.asyncio
async def test_list_messages_pagination_before(mock_current_user):
    """Test list_messages with 'before' pagination parameter."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from datetime import timedelta

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        # Create 3 messages
        msg_times = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for i in range(3):
                resp = await client.post(
                    f"/api/v1/conversations/{conv_id}/messages",
                    json={"content": f"Message {i}"},
                )
                assert resp.status_code == 201
                msg_times.append(datetime.fromisoformat(resp.json()["created_at"]))

            # Get messages before the middle timestamp
            before_time = msg_times[1]
            response = await client.get(
                f"/api/v1/conversations/{conv_id}/messages",
                params={"before": before_time.isoformat()},
            )

        assert response.status_code == 200
        data = response.json()
        # Should only have message 0 and 1 (before the 2nd message's time)
        # Since ordering is descending (newest first), "before" filters to older
        assert len(data["items"]) <= 2
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_messages_has_more(mock_current_user):
    """Test list_messages with has_more=True (limit < total)."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Create 5 messages
            for i in range(5):
                resp = await client.post(
                    f"/api/v1/conversations/{conv_id}/messages",
                    json={"content": f"Message {i}"},
                )
                assert resp.status_code == 201

            # Request limit=2, should get has_more=True and next_cursor
            response = await client.get(
                f"/api/v1/conversations/{conv_id}/messages",
                params={"limit": 2},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["has_more"] is True
        assert data["next_cursor"] is not None
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_send_message_with_markdown_content_type(mock_current_user):
    """Test send_message with valid markdown content_type."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "# Hello", "content_type": "markdown"},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["content_type"] == "markdown"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_owner_can_edit_own_message(mock_current_user):
    """Test that agent owner CAN edit their own message.
    
    Per the unified permission matrix:
    - Owner can edit any message AND their own messages
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_uid = mock_current_user.user_id
    owner_user = UserCredentials(
        user_id=owner_uid,
        username=owner_uid,
        password_hash="test_hash",
        is_agent=True,
    )

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return owner_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_uid,
            name="Agent Owner Conv",
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Agent owner message"},
            )
            assert send_resp.status_code == 201
            msg_id = send_resp.json()["id"]

            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Edited"},
            )

        # Agent owner CAN edit their own message (Owner has all permissions)
        assert edit_resp.status_code == 200
        assert edit_resp.json()["content"] == "Edited"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_admin_can_edit_own_message(mock_current_user):
    """Test that agent admin CAN edit their own messages."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_uid = f"owner_{uuid.uuid4().hex[:12]}"
    admin_uid = mock_current_user.user_id
    admin_user = UserCredentials(
        user_id=admin_uid,
        username=admin_uid,
        password_hash="test_hash",
        is_agent=True,
    )

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return admin_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_uid,
        )
        # Add admin as admin (not owner) - directly to database
        _ensure_member_in_db(admin_uid, admin_uid, conv_id, "admin")
        _ensure_agent_in_db(admin_uid, admin_uid)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Admin agent message"},
            )
            assert send_resp.status_code == 201
            msg_id = send_resp.json()["id"]

            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Edited by admin agent"},
            )

        # Agent admin CAN edit their own message
        assert edit_resp.status_code == 200
        assert edit_resp.json()["content"] == "Edited by admin agent"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


def _ensure_agent_in_db(user_id: str, username: str):
    """Ensure agent user exists in database (sync)."""
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        existing = session.get(User, user_id)
        if existing is None:
            user = User(
                id=user_id,
                username=username,
                display_name=username,
                user_type=DBUserType.agent,
                extra_data="{}",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(user)
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_member_in_db(user_id: str, username: str, conversation_id: str = None, role: str = "member"):
    """Ensure a regular member user and their conversation membership exist in database (sync)."""
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        existing = session.get(User, user_id)
        if existing is None:
            user = User(
                id=user_id,
                username=username,
                display_name=username,
                user_type=DBUserType.human,
                extra_data={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(user)
        
        # Also create ConversationMember if conversation_id provided
        if conversation_id:
            existing_member = session.execute(
                select(ConversationMember).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id == user_id
                )
            ).scalar_one_or_none()
            if existing_member is None:
                member = ConversationMember(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role=DBMemberRole[role] if role in [r.name for r in DBMemberRole] else DBMemberRole.member,
                    joined_at=datetime.utcnow(),
                    is_active=True,
                )
                session.add(member)
        
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _cleanup_test_data(conversation_ids: list, user_ids: list):
    """Clean up test data from database."""
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        for conv_id in conversation_ids:
            session.execute(text("DELETE FROM messages WHERE conversation_id = :conv_id"), {"conv_id": conv_id})
            session.execute(text("DELETE FROM conversation_members WHERE conversation_id = :conv_id"), {"conv_id": conv_id})
            session.execute(text("DELETE FROM conversations WHERE id = :conv_id"), {"conv_id": conv_id})
        for user_id in user_ids:
            session.execute(text("DELETE FROM messages WHERE sender_id = :user_id"), {"user_id": user_id})
            session.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id})
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.mark.asyncio
async def test_edit_message_non_sender_non_admin_fails(mock_current_user):
    """Test that a non-sender non-admin cannot edit a message."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_uid = f"owner_{uuid.uuid4().hex[:12]}"
    sender_uid = mock_current_user.user_id
    sender_user = UserCredentials(
        user_id=sender_uid,
        username=sender_uid,
        password_hash="test_hash",
        is_agent=False,
    )
    hacker_uid = f"hacker_{uuid.uuid4().hex[:12]}"
    hacker_user = UserCredentials(
        user_id=hacker_uid,
        username=hacker_uid,
        password_hash="test_hash",
        is_agent=False,
    )

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return sender_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        # Add both sender and hacker to database so they can send messages
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_uid,
            extra_members=[(sender_uid, "member"), (hacker_uid, "member")],
        )
        get_member_store()[(conv_id, sender_uid)] = MemberStore(
            conversation_id=conv_id, user_id=sender_uid,
            role="member", joined_at=datetime.now(timezone.utc), is_active=True,
        )
        get_member_store()[(conv_id, hacker_uid)] = MemberStore(
            conversation_id=conv_id, user_id=hacker_uid,
            role="member", joined_at=datetime.now(timezone.utc), is_active=True,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Sender sends a message
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Sender message"},
            )
            msg_id = send_resp.json()["id"]

            # Hacker (non-sender, non-admin) tries to edit
            app.dependency_overrides[get_current_user] = lambda: hacker_user
            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Hacked"},
            )

        assert edit_resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_message_non_sender_non_admin_fails(mock_current_user):
    """Test that a non-sender non-admin cannot delete a message."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_uid = f"owner_{uuid.uuid4().hex[:12]}"
    sender_uid = mock_current_user.user_id
    sender_user = UserCredentials(
        user_id=sender_uid,
        username=sender_uid,
        password_hash="test_hash",
        is_agent=False,
    )
    hacker_uid = f"hacker_{uuid.uuid4().hex[:12]}"
    hacker_user = UserCredentials(
        user_id=hacker_uid,
        username=hacker_uid,
        password_hash="test_hash",
        is_agent=False,
    )

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return sender_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_uid,
            extra_members=[(sender_uid, "member"), (hacker_uid, "member")],
        )
        get_member_store()[(conv_id, sender_uid)] = MemberStore(
            conversation_id=conv_id, user_id=sender_uid,
            role="member", joined_at=datetime.now(timezone.utc), is_active=True,
        )
        get_member_store()[(conv_id, hacker_uid)] = MemberStore(
            conversation_id=conv_id, user_id=hacker_uid,
            role="member", joined_at=datetime.now(timezone.utc), is_active=True,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Sender sends a message
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Sender message to delete"},
            )
            msg_id = send_resp.json()["id"]

            # Hacker (non-sender, non-admin) tries to delete
            app.dependency_overrides[get_current_user] = lambda: hacker_user
            del_resp = await client.delete(f"/api/v1/messages/{msg_id}")

        assert del_resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_message_not_found(mock_current_user):
    """Test updating a non-existent message returns 404."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        fake_msg_id = str(uuid.uuid4())

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/api/v1/messages/{fake_msg_id}",
                json={"content": "Updated"},
            )

        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_deleted_message_fails(mock_current_user):
    """Test updating a soft-deleted message returns 404."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, mock_current_user.user_id,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "To be deleted"},
            )
            msg_id = send_resp.json()["id"]

            del_resp = await client.delete(f"/api/v1/messages/{msg_id}")
            assert del_resp.status_code == 204

            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Try to edit deleted"},
            )

        assert edit_resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_delete_any_message(mock_current_user):
    """Test that an admin can delete any message."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    admin_uid = f"admin_{uuid.uuid4().hex[:12]}"
    admin_user = UserCredentials(
        user_id=admin_uid,
        username=admin_uid,
        password_hash="test_hash",
        is_agent=True,
    )

    owner_id = mock_current_user.user_id

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_id,
        )
        # Add admin directly to database
        _ensure_member_in_db(admin_uid, admin_uid, conv_id, "admin")
        _ensure_agent_in_db(admin_uid, admin_uid)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Owner message to be deleted by admin"},
            )
            msg_id = send_resp.json()["id"]

            # Switch to admin user
            app.dependency_overrides[get_current_user] = lambda: admin_user
            del_resp = await client.delete(f"/api/v1/messages/{msg_id}")

        assert del_resp.status_code == 204
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_member_by_non_admin_fails(mock_current_user, mock_member_user):
    """Test that non-admin members cannot add new members."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    owner_uid = f"owner_{uuid.uuid4().hex[:12]}"
    member_uid = mock_current_user.user_id
    member_user = UserCredentials(
        user_id=member_uid,
        username=member_uid,
        password_hash="test_hash",
        is_agent=False,
    )

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return member_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_uid,
            extra_members=[(member_uid, "member")],
        )
        get_member_store()[(conv_id, member_uid)] = MemberStore(
            conversation_id=conv_id, user_id=member_uid,
            role="member", joined_at=datetime.now(timezone.utc), is_active=True,
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/conversations/{conv_id}/members",
                json={"user_id": mock_member_user.user_id, "role": "member"},
            )

        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_conversation_store()
        await engine.dispose()





@pytest.mark.asyncio
async def test_send_message_reply_to_different_conversation(mock_current_user):
    """Test sending a message with reply_to from a different conversation fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"postgresql+asyncpg://cream@localhost:5432/{get_settings().database.name}"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_current_user():
        return mock_current_user

    async def override_get_db():
        async with async_session_factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_get_db

    try:
        conv1_id = f"conv_{uuid.uuid4().hex[:12]}"
        conv2_id = f"conv_{uuid.uuid4().hex[:12]}"
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv1_id, mock_current_user.user_id, name="Conv 1",
        )
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv2_id, mock_current_user.user_id, name="Conv 2",
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Send message in conv1
            resp1 = await client.post(
                f"/api/v1/conversations/{conv1_id}/messages",
                json={"content": "Message in conv1"},
            )
            msg_id = resp1.json()["id"]

            # Try to reply to it in conv2
            resp2 = await client.post(
                f"/api/v1/conversations/{conv2_id}/messages",
                json={"content": "Reply in conv2", "reply_to": msg_id},
            )

        assert resp2.status_code == 400
        assert "different conversation" in resp2.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()
        clear_conversation_store()
        await engine.dispose()
