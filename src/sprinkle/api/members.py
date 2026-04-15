"""Member API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user
from sprinkle.api.conversations import (
    _conversations,
    _members,
    check_conversation_access,
    check_admin_access,
    is_owner,
    is_member,
)

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

def get_member_or_404(conversation_id: str, user_id: str) -> MemberStore:
    """Get member or raise 404."""
    key = (conversation_id, user_id)
    if key not in _members:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
    return _members[key]


class MemberStore:
    """Member data store (imported from conversations)."""
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


# Re-export stores
def get_conversation_store():
    from sprinkle.api.conversations import _conversations
    return _conversations


def get_member_store():
    from sprinkle.api.conversations import _members
    return _members


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
    """List all members of a conversation.
    
    - **conversation_id**: Conversation UUID
    """
    # Check conversation access
    check_conversation_access(conversation_id, current_user.user_id)
    
    # Get active members
    members = [
        m for m in _members.values()
        if m.conversation_id == conversation_id and m.is_active
    ]
    
    items = [
        MemberResponse(
            user_id=m.user_id,
            conversation_id=m.conversation_id,
            role=m.role,
            nickname=m.nickname,
            joined_at=m.joined_at,
            is_active=m.is_active,
        )
        for m in members
    ]
    
    return MemberListResponse(items=items, total=len(items))


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
    
    - **conversation_id**: Conversation UUID
    - **user_id**: User ID to add
    - **role**: Member role (admin/member)
    """
    # Check admin access
    check_admin_access(conversation_id, current_user.user_id)
    
    # Check if conversation exists
    if conversation_id not in _conversations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    
    # Check if user is already a member
    key = (conversation_id, request.user_id)
    if key in _members and _members[key].is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member",
        )
    
    # Cannot add owner as a non-owner
    conv = _conversations[conversation_id]
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
    
    # Create member
    now = datetime.now(timezone.utc)
    member = MemberStore(
        conversation_id=conversation_id,
        user_id=request.user_id,
        role=request.role,
        nickname=request.nickname,
        joined_at=now,
        is_active=True,
    )
    _members[key] = member
    
    return MemberResponse(
        user_id=member.user_id,
        conversation_id=member.conversation_id,
        role=member.role,
        nickname=member.nickname,
        joined_at=member.joined_at,
        is_active=member.is_active,
    )


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
    
    - **conversation_id**: Conversation UUID
    - **user_id**: User ID to remove
    """
    # Check admin access
    check_admin_access(conversation_id, current_user.user_id)
    
    # Check if conversation exists
    if conversation_id not in _conversations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    
    # Cannot remove owner
    conv = _conversations[conversation_id]
    if user_id == conv.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove owner from conversation",
        )
    
    # Check if member exists
    key = (conversation_id, user_id)
    if key not in _members or not _members[key].is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
    
    # Soft delete (set is_active to False)
    _members[key].is_active = False


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
    
    - **conversation_id**: Conversation UUID
    - **user_id**: User ID to update
    - **role**: New role (admin/member)
    """
    # Check owner access
    check_owner_access(conversation_id, current_user.user_id)
    
    # Check if conversation exists
    if conversation_id not in _conversations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    
    # Cannot change owner's role
    conv = _conversations[conversation_id]
    if user_id == conv.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change owner's role",
        )
    
    # Check if member exists
    key = (conversation_id, user_id)
    if key not in _members or not _members[key].is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
    
    # Update role
    _members[key].role = request.role
    
    member = _members[key]
    return MemberResponse(
        user_id=member.user_id,
        conversation_id=member.conversation_id,
        role=member.role,
        nickname=member.nickname,
        joined_at=member.joined_at,
        is_active=member.is_active,
    )


def check_owner_access(conversation_id: str, user_id: str) -> None:
    """Check if user is the owner of the conversation."""
    if conversation_id not in _conversations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    
    conv = _conversations[conversation_id]
    if conv.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner permission required",
        )
