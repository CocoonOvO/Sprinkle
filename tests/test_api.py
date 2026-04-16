"""Tests for Message API (database-backed).

These tests verify that the message API correctly persists data
to and retrieves data from the database.
"""

from __future__ import annotations

import asyncio
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
)
from sprinkle.api.messages import clear_message_store
from sprinkle.kernel.auth import UserCredentials
from sprinkle.models import (
    Conversation, User, Message,
    ConversationType as DBConvType, UserType as DBUserType
)
from sprinkle.storage.database import get_sync_engine
from sqlalchemy.orm import sessionmaker
from sprinkle.config import get_settings


# ============================================================================
# Helper Functions
# ============================================================================

def _ensure_in_db(conversation_id: str, owner_id: str,
                  conv_type: str = "group", name: str = "Test"):
    """Ensure user and conversation exist in database (sync)."""
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        existing_user = session.get(User, owner_id)
        if existing_user is None:
            user = User(
                id=owner_id,
                username=owner_id,
                display_name=owner_id,
                user_type=DBUserType.human,
                extra_data="{}",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(user)
        
        existing_conv = session.get(Conversation, conversation_id)
        if existing_conv is None:
            conv = Conversation(
                id=conversation_id,
                type=DBConvType(conv_type),
                name=name,
                owner_id=owner_id,
                extra_data="{}",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(conv)
        
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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


def create_test_conversation(
    conv_store, member_store,
    conversation_id: str, owner_id: str,
    conv_type: str = "group", name: str = "Test Conversation",
):
    """Set up test conversation in in-memory store AND database."""
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


def verify_message_in_db(message_id: str, expected_content: str = None,
                          expected_deleted: bool = None) -> bool:
    """Verify message state in database directly (sync query)."""
    engine = get_sync_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        msg = session.get(Message, message_id)
        if msg is None:
            return False
        if expected_content is not None and msg.content != expected_content:
            return False
        if expected_deleted is not None and msg.is_deleted != expected_deleted:
            return False
        return True
    finally:
        session.close()


# ============================================================================
# Test Fixtures
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
def mock_agent_user():
    uid = f"agent_{uuid.uuid4().hex[:12]}"
    return UserCredentials(
        user_id=uid,
        username=uid,
        password_hash="test_hash",
        is_agent=True,
    )


# ============================================================================
# Message API Tests (Database-Backed) - Async with httpx
# ============================================================================

@pytest.mark.asyncio
async def test_send_message_persists_to_database(mock_current_user):
    """Test that sending a message persists data to the database."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
                json={"content": "Hello from database test!"},
            )
        
        assert response.status_code == 201, f"Got {response.status_code}: {response.text}"
        data = response.json()
        assert data["content"] == "Hello from database test!"
        assert data["sender_id"] == mock_current_user.user_id
        assert data["conversation_id"] == conv_id
        assert data["content_type"] == "text"
        assert data["is_deleted"] is False
        
        assert verify_message_in_db(data["id"], expected_content="Hello from database test!", expected_deleted=False)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_messages_from_database(mock_current_user):
    """Test that listing messages retrieves data from the database."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
            msg_ids = []
            for i in range(3):
                response = await client.post(
                    f"/api/v1/conversations/{conv_id}/messages",
                    json={"content": f"Message {i}"},
                )
                assert response.status_code == 201
                msg_ids.append(response.json()["id"])
            
            response = await client.get(f"/api/v1/conversations/{conv_id}/messages")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        
        for msg_id in msg_ids:
            assert verify_message_in_db(msg_id), f"Message {msg_id} not found in database"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_edit_message_updates_database(mock_current_user):
    """Test that editing a message updates the database."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
                json={"content": "Original content"},
            )
            assert send_resp.status_code == 201
            msg_id = send_resp.json()["id"]
            
            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Updated content"},
            )
        
        assert edit_resp.status_code == 200
        assert edit_resp.json()["content"] == "Updated content"
        assert verify_message_in_db(msg_id, expected_content="Updated content")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_message_soft_deletes_in_database(mock_current_user):
    """Test that deleting a message soft-deletes in the database."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
            assert send_resp.status_code == 201
            msg_id = send_resp.json()["id"]
            
            del_resp = await client.delete(f"/api/v1/messages/{msg_id}")
        
        assert del_resp.status_code == 204
        assert verify_message_in_db(msg_id, expected_deleted=True)
        
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            app.dependency_overrides[get_current_user] = override_current_user
            app.dependency_overrides[get_db_session] = override_get_db
            list_resp = await client.get(f"/api/v1/conversations/{conv_id}/messages")
        
        assert list_resp.status_code == 200
        items = [item for item in list_resp.json()["items"] if item["id"] == msg_id]
        assert len(items) == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_message_cannot_be_deleted_again(mock_current_user):
    """Test that a soft-deleted message returns 404 on second delete."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
                json={"content": "Delete me twice"},
            )
            msg_id = send_resp.json()["id"]
            
            await client.delete(f"/api/v1/messages/{msg_id}")
            del_resp2 = await client.delete(f"/api/v1/messages/{msg_id}")
        
        assert del_resp2.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_send_message_with_mentions(mock_current_user):
    """Test sending a message with mentions."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
                json={"content": "Hey @user!", "mentions": ["user_1", "user_2"]},
            )
        
        assert response.status_code == 201
        data = response.json()
        assert data["content"] == "Hey @user!"
        # Note: mentions are accepted in request but not stored in database
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_send_message_with_reply(mock_current_user):
    """Test sending a message as a reply to another message."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
            orig_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Original message"},
            )
            orig_id = orig_resp.json()["id"]
            
            reply_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Reply message", "reply_to": orig_id},
            )
        
        assert reply_resp.status_code == 201
        assert reply_resp.json()["reply_to"] == orig_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_send_message_with_reply_wrong_conversation(mock_current_user):
    """Test that replying to a message in a different conversation fails."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
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
        conv1 = f"conv_{uuid.uuid4().hex[:12]}"
        conv2 = f"conv_{uuid.uuid4().hex[:12]}"
        
        for conv_id in [conv1, conv2]:
            create_test_conversation(
                get_conversation_store(), get_member_store(),
                conv_id, mock_current_user.user_id,
            )
        
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            orig_resp = await client.post(
                f"/api/v1/conversations/{conv1}/messages",
                json={"content": "Original in conv1"},
            )
            orig_id = orig_resp.json()["id"]
            
            reply_resp = await client.post(
                f"/api/v1/conversations/{conv2}/messages",
                json={"content": "Reply in conv2", "reply_to": orig_id},
            )
        
        assert reply_resp.status_code == 400
        assert "different conversation" in reply_resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_cannot_edit_own_message():
    """Test that a regular agent cannot edit their own messages.
    
    The agent is set up as a regular member (not owner) of the conversation.
    They send a message (making them the sender), then try to edit it.
    Since agents cannot edit their own messages, this should return 403.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    agent_uid = f"agent_{uuid.uuid4().hex[:12]}"
    owner_uid = f"owner_{uuid.uuid4().hex[:12]}"
    agent_user = UserCredentials(
        user_id=agent_uid,
        username=agent_uid,
        password_hash="test_hash",
        is_agent=True,
    )
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async def override_current_user():
        return agent_user
    
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
        # Create conversation with owner (not agent)
        create_test_conversation(
            get_conversation_store(), get_member_store(),
            conv_id, owner_uid,
        )
        # Add agent as regular member (not owner/admin)
        get_member_store()[(conv_id, agent_uid)] = MemberStore(
            conversation_id=conv_id,
            user_id=agent_uid,
            role="member",
            joined_at=datetime.now(timezone.utc),
            is_active=True,
        )
        _ensure_agent_in_db(agent_uid, agent_uid)
        
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Agent message"},
            )
            msg_id = send_resp.json()["id"]
            
            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Edited by agent"},
            )
        
        assert edit_resp.status_code == 403, f"Expected 403, got {edit_resp.status_code}: {edit_resp.text}"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_edit_any_message(mock_current_user):
    """Test that an admin can edit any message in their conversation."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    
    admin_uid = f"admin_{uuid.uuid4().hex[:12]}"
    admin_user = UserCredentials(
        user_id=admin_uid,
        username=admin_uid,
        password_hash="test_hash",
        is_agent=True,
    )
    
    db_url = "postgresql+asyncpg://cream@localhost:5432/sprinkle_db"
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    owner_id = mock_current_user.user_id
    
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
        
        get_member_store()[(conv_id, admin_user.user_id)] = MemberStore(
            conversation_id=conv_id,
            user_id=admin_user.user_id,
            role="admin",
            joined_at=datetime.now(timezone.utc),
            is_active=True,
        )
        _ensure_agent_in_db(admin_user.user_id, admin_user.username)
        
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            send_resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Owner message"},
            )
            msg_id = send_resp.json()["id"]
            
            app.dependency_overrides[get_current_user] = lambda: admin_user
            edit_resp = await client.put(
                f"/api/v1/messages/{msg_id}",
                json={"content": "Edited by admin"},
            )
        
        assert edit_resp.status_code == 200
        assert edit_resp.json()["content"] == "Edited by admin"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
        clear_message_store()

        clear_conversation_store()
        await engine.dispose()
