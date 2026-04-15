"""Conversation API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user

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
# In-Memory Store
# ============================================================================

class ConversationStore:
    """Conversation data store."""
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
    """Member data store."""
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


# Stores
_conversations: Dict[str, ConversationStore] = {}
_members: Dict[Tuple[str, str], MemberStore] = {}  # (conversation_id, user_id) -> MemberStore


def get_conversation_store() -> Dict[str, ConversationStore]:
    """Get conversations store."""
    return _conversations


def get_member_store() -> Dict[Tuple[str, str], MemberStore]:
    """Get members store."""
    return _members


def clear_conversation_store() -> None:
    """Clear all conversation and member data (for testing)."""
    _conversations.clear()
    _members.clear()


# ============================================================================
# Helper Functions
# ============================================================================

def is_member(conversation_id: str, user_id: str) -> bool:
    """Check if user is a member of the conversation."""
    key = (conversation_id, user_id)
    member = _members.get(key)
    return member is not None and member.is_active


def is_owner(conversation_id: str, user_id: str) -> bool:
    """Check if user is the owner of the conversation."""
    conv = _conversations.get(conversation_id)
    return conv is not None and conv.owner_id == user_id


def is_admin(conversation_id: str, user_id: str) -> bool:
    """Check if user is admin or owner of the conversation."""
    key = (conversation_id, user_id)
    member = _members.get(key)
    if member is None:
        return False
    return member.role in ("owner", "admin")


def get_member_role(conversation_id: str, user_id: str) -> Optional[str]:
    """Get member's role in conversation."""
    key = (conversation_id, user_id)
    member = _members.get(key)
    return member.role if member else None


def check_conversation_access(conversation_id: str, user_id: str) -> None:
    """Check if user has access to the conversation.
    
    Raises:
        HTTPException: 404 if conversation not found
        HTTPException: 403 if user is not a member
    """
    if conversation_id not in _conversations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    
    if not is_member(conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this conversation",
        )


def check_admin_access(conversation_id: str, user_id: str) -> None:
    """Check if user has admin access to the conversation.
    
    Raises:
        HTTPException: 404 if conversation not found
        HTTPException: 403 if user is not admin or owner
    """
    check_conversation_access(conversation_id, user_id)
    
    if not is_admin(conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required",
        )


def check_owner_access(conversation_id: str, user_id: str) -> None:
    """Check if user is the owner of the conversation.
    
    Raises:
        HTTPException: 404 if conversation not found
        HTTPException: 403 if user is not the owner
    """
    if conversation_id not in _conversations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    
    if not is_owner(conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner permission required",
        )


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
) -> ConversationListResponse:
    """List all conversations the current user is a member of.
    
    - **limit**: Maximum number of results (1-100)
    - **offset**: Number of results to skip
    """
    # Get user's conversations
    user_convs = []
    for conv_id, conv in _conversations.items():
        if is_member(conv_id, current_user.user_id):
            # Count members
            member_count = sum(
                1 for m in _members.values()
                if m.conversation_id == conv_id and m.is_active
            )
            user_convs.append((conv, member_count))
    
    # Sort by updated_at descending
    user_convs.sort(key=lambda x: x[0].updated_at, reverse=True)
    
    # Apply pagination
    total = len(user_convs)
    paginated = user_convs[offset:offset + limit]
    
    items = [
        ConversationResponse(
            id=conv.id,
            type=conv.type,
            name=conv.name,
            owner_id=conv.owner_id,
            metadata=conv.metadata,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            member_count=member_count,
        )
        for conv, member_count in paginated
    ]
    
    return ConversationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create conversation",
)
async def create_conversation(
    request: CreateConversationRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> ConversationResponse:
    """Create a new conversation.
    
    - **type**: 'direct' or 'group'
    - **name**: Conversation name (required for group type)
    - **member_ids**: Initial member user IDs (owner is automatically added)
    """
    # Validate group conversations have a name
    if request.type == "group" and not request.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group conversations require a name",
        )
    
    # Create conversation
    conv_id = str(uuid4())
    now = datetime.now(timezone.utc)
    
    conv = ConversationStore(
        id=conv_id,
        type=request.type,
        name=request.name or current_user.username,
        owner_id=current_user.user_id,
        metadata=request.metadata,
        created_at=now,
        updated_at=now,
    )
    _conversations[conv_id] = conv
    
    # Add owner as member with 'owner' role
    owner_member = MemberStore(
        conversation_id=conv_id,
        user_id=current_user.user_id,
        role="owner",
        joined_at=now,
        is_active=True,
    )
    _members[(conv_id, current_user.user_id)] = owner_member
    
    # Add other members
    for member_id in request.member_ids:
        if member_id != current_user.user_id:
            member = MemberStore(
                conversation_id=conv_id,
                user_id=member_id,
                role="member",
                joined_at=now,
                is_active=True,
            )
            _members[(conv_id, member_id)] = member
    
    member_count = sum(
        1 for m in _members.values()
        if m.conversation_id == conv_id and m.is_active
    )
    
    return ConversationResponse(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        owner_id=conv.owner_id,
        metadata=conv.metadata,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        member_count=member_count,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get conversation",
)
async def get_conversation(
    conversation_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> ConversationResponse:
    """Get conversation details.
    
    - **conversation_id**: Conversation UUID
    """
    # Check access
    check_conversation_access(conversation_id, current_user.user_id)
    
    conv = _conversations[conversation_id]
    
    # Count members
    member_count = sum(
        1 for m in _members.values()
        if m.conversation_id == conversation_id and m.is_active
    )
    
    return ConversationResponse(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        owner_id=conv.owner_id,
        metadata=conv.metadata,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        member_count=member_count,
    )


@router.put(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Update conversation",
)
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> ConversationResponse:
    """Update conversation details (name, metadata).
    
    - **conversation_id**: Conversation UUID
    - **name**: New conversation name (optional)
    - **metadata**: Updated metadata (optional, merged)
    """
    # Check admin access
    check_admin_access(conversation_id, current_user.user_id)
    
    conv = _conversations[conversation_id]
    
    # Update fields
    if request.name is not None:
        conv.name = request.name
    
    if request.metadata is not None:
        conv.metadata.update(request.metadata)
    
    conv.updated_at = datetime.now(timezone.utc)
    
    # Count members
    member_count = sum(
        1 for m in _members.values()
        if m.conversation_id == conversation_id and m.is_active
    )
    
    return ConversationResponse(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        owner_id=conv.owner_id,
        metadata=conv.metadata,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        member_count=member_count,
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete conversation",
)
async def delete_conversation(
    conversation_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> None:
    """Delete a conversation.
    
    Only the owner can delete a conversation.
    
    - **conversation_id**: Conversation UUID
    """
    # Check owner access
    check_owner_access(conversation_id, current_user.user_id)
    
    # Delete all members first
    to_delete = [
        key for key, member in _members.items()
        if member.conversation_id == conversation_id
    ]
    for key in to_delete:
        del _members[key]
    
    # Delete conversation
    del _conversations[conversation_id]
