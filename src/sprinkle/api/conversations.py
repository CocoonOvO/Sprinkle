"""Conversation API endpoints - database-backed implementation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user, get_db_session
from sprinkle.models import Conversation, ConversationMember, ConversationType, MemberRole
from sprinkle.storage.database import SessionLocal

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class ConversationResponse(BaseModel):
    """Conversation response schema."""
    id: str
    type: str
    name: str
    owner_id: str
    metadata: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime
    member_count: int = 0

    model_config = {"from_attributes": True}


class CreateConversationRequest(BaseModel):
    """Create conversation request schema."""
    type: str = Field(..., pattern="^(direct|group)$")
    name: str | None = Field(None, max_length=255)
    member_ids: List[str] = []
    metadata: Dict[str, Any] = {}


class UpdateConversationRequest(BaseModel):
    """Update conversation request schema."""
    name: str | None = Field(None, max_length=255)
    metadata: Dict[str, Any] | None = None


class ConversationListResponse(BaseModel):
    """Conversation list response schema."""
    items: List[ConversationResponse]
    total: int
    limit: int
    offset: int


# ============================================================================
# Stub In-Memory Stores (kept for backward compatibility with tests only)
# ============================================================================
# The API no longer uses these - all data goes through the database.
# Tests may still write to these stubs but the API will not read from them.

class ConversationStore:
    """Conversation data store (stub for test compatibility)."""
    def __init__(
        self,
        id: str,
        type: str,
        name: str,
        owner_id: str,
        metadata: Dict[str, Any] = None,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.id = id
        self.type = type
        self.name = name
        self.owner_id = owner_id
        self.metadata = metadata or {}
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)


class MemberStore:
    """Member data store (stub for test compatibility)."""
    def __init__(
        self,
        conversation_id: str,
        user_id: str,
        role: str = "member",
        nickname: str = None,
        joined_at: datetime = None,
        is_active: bool = True,
    ):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.role = role
        self.nickname = nickname
        self.joined_at = joined_at or datetime.now(timezone.utc)
        self.is_active = is_active


# Stub stores (not used by API anymore, but tests may write to them)
_conversations: Dict[str, ConversationStore] = {}
_members: Dict[tuple, MemberStore] = {}


def get_conversation_store() -> Dict[str, ConversationStore]:
    """Get conversations store (stub - not used by API, for test compatibility)."""
    return _conversations


def get_member_store() -> Dict[tuple, MemberStore]:
    """Get members store (stub - not used by API, for test compatibility)."""
    return _members


# ============================================================================
# Database Helpers
# ============================================================================

def _get_db():
    """Get database session (sync - for non-endpoint use)."""
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def _parse_extra_data(raw: Any) -> Dict[str, Any]:
    """Parse extra_data from database (JSON string or dict)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _get_member_count(db: Session, conversation_id: str) -> int:
    """Get active member count for a conversation."""
    return db.execute(
        select(func.count())
        .select_from(ConversationMember)
        .where(ConversationMember.conversation_id == conversation_id)
        .where(ConversationMember.is_active == True)
    ).scalar() or 0


def _build_conversation_response(conv: Conversation, db: Optional[Session] = None) -> ConversationResponse:
    """Build ConversationResponse from database model."""
    member_count = 0
    if db is not None:
        member_count = _get_member_count(db, conv.id)

    return ConversationResponse(
        id=conv.id,
        type=conv.type.value if hasattr(conv.type, 'value') else conv.type,
        name=conv.name,
        owner_id=conv.owner_id,
        metadata=_parse_extra_data(conv.extra_data),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        member_count=member_count,
    )


def _check_db_conversation_access(conversation_id: str, user_id: str, db: Session) -> Optional[Conversation]:
    """Check if user has access to the conversation in database."""
    conv = db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    ).scalar_one_or_none()

    if conv is None:
        return None

    member = db.execute(
        select(ConversationMember)
        .where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id
        )
    ).scalar_one_or_none()

    if member is None:
        return None

    return conv


def _check_db_admin_access(conversation_id: str, user_id: str, db: Session) -> Conversation:
    """Check admin/owner access in database. Returns conversation if allowed."""
    conv = _check_db_conversation_access(conversation_id, user_id, db)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    member = db.execute(
        select(ConversationMember)
        .where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id
        )
    ).scalar_one()

    if member.role not in (MemberRole.owner, MemberRole.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required",
        )

    return conv


def _check_db_owner_access(conversation_id: str, user_id: str, db: Session) -> None:
    """Check owner access in database."""
    conv = db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    ).scalar_one_or_none()

    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    if conv.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner permission required",
        )


# ============================================================================
# Store Management (for testing)
# ============================================================================

def clear_conversation_store() -> None:
    """Clear all conversation and member data (for testing).

    Clears database tables.
    """
    db = SessionLocal()
    try:
        db.query(ConversationMember).delete()
        db.query(Conversation).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List conversations",
)
async def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationListResponse:
    """List all conversations the current user is a member of."""
    user_id = current_user.user_id

    # Use sync session for this query (SQLAlchemy 2.0 supports async sessions
    # for select statements via db.execute())
    # For compatibility, we use the sync _get_db() here which uses the
    # same database as the async session (they share the same engine)
    db_sync = _get_db()
    try:
        subq = (
            select(ConversationMember.conversation_id)
            .where(ConversationMember.user_id == user_id)
        )
        result = db_sync.execute(
            select(Conversation)
            .where(Conversation.id.in_(subq))
        )
        db_convs = result.scalars().all()

        def make_aware(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        db_convs = sorted(db_convs, key=lambda x: make_aware(x.updated_at), reverse=True)

        total = len(db_convs)
        paginated = db_convs[offset:offset + limit]

        items = [_build_conversation_response(conv, db_sync) for conv in paginated]

        return ConversationListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )
    finally:
        db_sync.close()


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create conversation",
)
async def create_conversation(
    request: CreateConversationRequest,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Create a new conversation."""
    if request.type == "group" and not request.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group conversations require a name",
        )

    conv_id = str(uuid4())
    now = datetime.now(timezone.utc)

    db_sync = _get_db()
    try:
        db_conv = Conversation(
            id=conv_id,
            type=ConversationType(request.type),
            name=request.name or current_user.username,
            owner_id=current_user.user_id,
            extra_data=request.metadata if request.metadata else {},
            created_at=now,
            updated_at=now,
        )
        db_sync.add(db_conv)

        # Add owner as member
        db_owner_member = ConversationMember(
            conversation_id=conv_id,
            user_id=current_user.user_id,
            role=MemberRole.owner,
            joined_at=now,
            is_active=True,
        )
        db_sync.add(db_owner_member)

        # Add other members
        for member_id in request.member_ids:
            if member_id != current_user.user_id:
                db_member = ConversationMember(
                    conversation_id=conv_id,
                    user_id=member_id,
                    role=MemberRole.member,
                    invited_by=current_user.user_id,
                    joined_at=now,
                    is_active=True,
                )
                db_sync.add(db_member)

        db_sync.commit()
        db_sync.refresh(db_conv)

        return _build_conversation_response(db_conv, db_sync)
    except Exception:
        db_sync.rollback()
        raise
    finally:
        db_sync.close()


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get conversation",
)
async def get_conversation(
    conversation_id: str,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Get conversation details."""
    user_id = current_user.user_id

    db_sync = _get_db()
    try:
        conv = _check_db_conversation_access(conversation_id, user_id, db_sync)
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        return _build_conversation_response(conv, db_sync)
    finally:
        db_sync.close()


@router.put(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Update conversation",
)
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    """Update conversation details (name, metadata)."""
    user_id = current_user.user_id

    db_sync = _get_db()
    try:
        _check_db_admin_access(conversation_id, user_id, db_sync)

        conv = db_sync.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one()

        if request.name is not None:
            conv.name = request.name

        if request.metadata is not None:
            current_meta = _parse_extra_data(conv.extra_data)
            current_meta.update(request.metadata)
            conv.extra_data = json.dumps(current_meta)

        conv.updated_at = datetime.now(timezone.utc)

        db_sync.commit()
        db_sync.refresh(conv)

        return _build_conversation_response(conv, db_sync)
    except HTTPException:
        raise
    finally:
        db_sync.close()


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete conversation",
)
async def delete_conversation(
    conversation_id: str,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a conversation. Only the owner can delete."""
    user_id = current_user.user_id

    db_sync = _get_db()
    try:
        conv = db_sync.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()

        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        if conv.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Owner permission required",
            )

        # Delete members first
        db_sync.execute(
            ConversationMember.__table__.delete()
            .where(ConversationMember.conversation_id == conversation_id)
        )

        # Delete conversation
        db_sync.delete(conv)
        db_sync.commit()
    except HTTPException:
        raise
    finally:
        db_sync.close()


# ============================================================================
# Member API Endpoints
# ============================================================================

@router.post(
    "/{conversation_id}/members",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Add member",
)
async def add_conversation_member(
    conversation_id: str,
    request: Dict[str, Any],
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Add a member to a conversation."""
    user_id_to_add = request.get("user_id")
    role_str = request.get("role", "member")

    if not user_id_to_add:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required",
        )

    if role_str not in ("member", "admin"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role_str}. Must be one of: member, admin",
        )

    current_uid = current_user.user_id

    db_sync = _get_db()
    try:
        _check_db_admin_access(conversation_id, current_uid, db_sync)

        # Check if already a member
        existing = db_sync.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id_to_add
            )
        ).scalar_one_or_none()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is already a member",
            )

        now = datetime.now(timezone.utc)

        member = ConversationMember(
            conversation_id=conversation_id,
            user_id=user_id_to_add,
            role=MemberRole(role_str),
            invited_by=current_uid,
            joined_at=now,
            is_active=True,
        )
        db_sync.add(member)
        db_sync.commit()
        db_sync.refresh(member)

        return {
            "conversation_id": member.conversation_id,
            "user_id": member.user_id,
            "role": member.role.value if hasattr(member.role, 'value') else member.role,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        }
    except HTTPException:
        raise
    finally:
        db_sync.close()


@router.delete(
    "/{conversation_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove member",
)
async def remove_conversation_member(
    conversation_id: str,
    user_id: str,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a member from a conversation."""
    current_uid = current_user.user_id

    db_sync = _get_db()
    try:
        _check_db_admin_access(conversation_id, current_uid, db_sync)

        conv = db_sync.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()

        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        if conv.owner_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the owner",
            )

        member = db_sync.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id
            )
        ).scalar_one_or_none()

        if member is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found",
            )

        db_sync.delete(member)
        db_sync.commit()
    except HTTPException:
        raise
    finally:
        db_sync.close()


# ============================================================================
# Helper Functions (for backward compatibility with other modules)
# ============================================================================

def is_member(conversation_id: str, user_id: str) -> bool:
    """Check if user is a member of the conversation."""
    db = _get_db()
    try:
        member = db.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id
            )
        ).scalar_one_or_none()
        return member is not None
    finally:
        db.close()


def is_owner(conversation_id: str, user_id: str) -> bool:
    """Check if user is the owner of the conversation."""
    db = _get_db()
    try:
        conv = db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
        return conv is not None and conv.owner_id == user_id
    finally:
        db.close()


def is_admin(conversation_id: str, user_id: str) -> bool:
    """Check if user is admin or owner of the conversation."""
    db = _get_db()
    try:
        member = db.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id
            )
        ).scalar_one_or_none()
        if member is None:
            return False
        return member.role in (MemberRole.owner, MemberRole.admin)
    finally:
        db.close()


def get_member_role(conversation_id: str, user_id: str) -> Optional[str]:
    """Get member's role in conversation."""
    db = _get_db()
    try:
        member = db.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id
            )
        ).scalar_one_or_none()
        return member.role.value if member and hasattr(member.role, 'value') else None
    finally:
        db.close()


def check_conversation_access(conversation_id: str, user_id: str) -> None:
    """Check if user has access to the conversation."""
    db = _get_db()
    try:
        conv = db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        member = db.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of this conversation",
            )
    finally:
        db.close()


def check_admin_access(conversation_id: str, user_id: str) -> None:
    """Check if user has admin access to the conversation."""
    check_conversation_access(conversation_id, user_id)
    if not is_admin(conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required",
        )


def check_owner_access(conversation_id: str, user_id: str) -> None:
    """Check if user is the owner of the conversation."""
    db = _get_db()
    try:
        conv = db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        if not is_owner(conversation_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Owner permission required",
            )
    finally:
        db.close()
