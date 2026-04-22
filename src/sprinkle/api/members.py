"""Member API endpoints - database-backed."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user
from sprinkle.api.conversations import (
    check_conversation_access,
    check_admin_access,
    is_owner,
    is_member,
    check_owner_access,
)
from sprinkle.models import Conversation, ConversationMember, MemberRole
from sprinkle.storage.database import SessionLocal

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class MemberResponse(BaseModel):
    """Member response schema."""
    user_id: str
    conversation_id: str
    role: str
    nickname: Optional[str] = None
    joined_at: datetime
    is_active: bool = True

    model_config = {"from_attributes": True}


class MemberListResponse(BaseModel):
    """Member list response schema."""
    items: List[MemberResponse]
    total: int


class AddMemberRequest(BaseModel):
    """Add member request schema."""
    user_id: str
    role: str = Field("member", pattern="^(admin|member)$")
    nickname: Optional[str] = None


# ============================================================================
# Helper Functions
# ============================================================================

def _get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def _get_member_or_404_db(conversation_id: str, user_id: str) -> ConversationMember:
    """Get member from database or raise 404."""
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found",
            )
        return member
    finally:
        db.close()


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "/{conversation_id}/members",
    response_model=MemberListResponse,
    summary="List members",
)
async def list_members(
    conversation_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> MemberListResponse:
    """List all members of a conversation."""
    # Check conversation access
    check_conversation_access(conversation_id, current_user.user_id)

    db = _get_db()
    try:
        members = db.execute(
            select(ConversationMember)
            .where(ConversationMember.conversation_id == conversation_id)
        ).scalars().all()

        items = [
            MemberResponse(
                user_id=m.user_id,
                conversation_id=m.conversation_id,
                role=m.role.value if hasattr(m.role, 'value') else m.role,
                nickname=m.nickname,
                joined_at=m.joined_at,
                is_active=True,
            )
            for m in members
        ]

        return MemberListResponse(items=items, total=len(items))
    finally:
        db.close()


@router.post(
    "/{conversation_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add member",
)
async def add_member(
    conversation_id: str,
    request: AddMemberRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> MemberResponse:
    """Add a member to a conversation.

    Only admin or owner can add members.
    """
    # Check admin access
    check_admin_access(conversation_id, current_user.user_id)

    db = _get_db()
    try:
        # Check if conversation exists
        conv = db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        # Cannot add owner as a non-owner
        if request.user_id == conv.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot add owner as a member",
            )

        # Cannot set role to owner
        if request.role == "owner":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot set role to owner",
            )

        # Check if user is already a member
        existing = db.execute(
            select(ConversationMember)
            .where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == request.user_id
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
            user_id=request.user_id,
            role=MemberRole(request.role),
            nickname=request.nickname,
            invited_by=current_user.user_id,
            joined_at=now,
            is_active=True,
        )
        db.add(member)
        db.commit()
        db.refresh(member)

        return MemberResponse(
            user_id=member.user_id,
            conversation_id=member.conversation_id,
            role=member.role.value if hasattr(member.role, 'value') else member.role,
            nickname=member.nickname,
            joined_at=member.joined_at,
            is_active=True,
        )
    except HTTPException:
        raise
    finally:
        db.close()


@router.delete(
    "/{conversation_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove member",
)
async def remove_member(
    conversation_id: str,
    user_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> None:
    """Remove a member from a conversation.

    Only admin or owner can remove members.
    Owner cannot be removed.
    """
    # Check admin access
    check_admin_access(conversation_id, current_user.user_id)

    db = _get_db()
    try:
        # Check if conversation exists
        conv = db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        # Cannot remove owner
        if user_id == conv.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove owner from conversation",
            )

        # Check if member exists
        member = db.execute(
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

        db.delete(member)
        db.commit()
    except HTTPException:
        raise
    finally:
        db.close()


class UpdateMemberRoleRequest(BaseModel):
    """Update member role request schema."""
    role: str = Field(..., pattern="^(admin|member)$")


@router.put(
    "/{conversation_id}/members/{user_id}",
    response_model=MemberResponse,
    summary="Update member role",
)
async def update_member(
    conversation_id: str,
    user_id: str,
    request: UpdateMemberRoleRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> MemberResponse:
    """Update a member's role in a conversation.

    Only owner can update roles.
    """
    # Check owner access
    check_owner_access(conversation_id, current_user.user_id)

    db = _get_db()
    try:
        # Check if conversation exists
        conv = db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        # Cannot change owner's role
        if user_id == conv.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change owner's role",
            )

        # Check if member exists
        member = db.execute(
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

        # Update role
        member.role = MemberRole(request.role)
        db.commit()
        db.refresh(member)

        return MemberResponse(
            user_id=member.user_id,
            conversation_id=member.conversation_id,
            role=member.role.value if hasattr(member.role, 'value') else member.role,
            nickname=member.nickname,
            joined_at=member.joined_at,
            is_active=True,
        )
    except HTTPException:
        raise
    finally:
        db.close()
