"""Message API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user
from sprinkle.api.conversations import (
    _conversations,
    _members,
    check_conversation_access,
    check_admin_access,
    is_owner,
    is_admin,
    get_member_role,
)

# Router for conversation-scoped message endpoints
# Routes: GET/POST /{conversation_id}/messages
conversation_messages_router = APIRouter()

# Router for standalone message endpoints
# Routes: PUT/DELETE /{message_id}
message_ops_router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class MessageResponse(BaseModel):
    """Message response schema."""
    id: str
    conversation_id: str
    sender_id: str
    content: str
    content_type: str = "text"
    metadata: Dict[str, Any] = {}
    mentions: List[str] = []
    reply_to: Optional[str] = None
    is_deleted: bool = False
    created_at: datetime
    edited_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SendMessageRequest(BaseModel):
    """Send message request schema."""
    content: str = Field(..., min_length=1)
    content_type: str = Field("text", pattern="^(text|markdown|image|file)$")
    mentions: List[str] = []
    reply_to: Optional[str] = None


class UpdateMessageRequest(BaseModel):
    """Update message request schema."""
    content: str = Field(..., min_length=1)


class MessageListResponse(BaseModel):
    """Message list response schema."""
    items: List[MessageResponse]
    next_cursor: Optional[str] = None
    has_more: bool = False


# For backwards compatibility
router = conversation_messages_router


# ============================================================================
# In-Memory Message Store
# ============================================================================

class MessageStore:
    """Message data store."""
    def __init__(
        self,
        id: str,
        conversation_id: str,
        sender_id: str,
        content: str,
        content_type: str = "text",
        metadata: Dict[str, Any] = None,
        mentions: List[str] = None,
        reply_to: Optional[str] = None,
        created_at: datetime = None,
        edited_at: Optional[datetime] = None,
        is_deleted: bool = False,
        deleted_at: Optional[datetime] = None,
    ):
        self.id = id
        self.conversation_id = conversation_id
        self.sender_id = sender_id
        self.content = content
        self.content_type = content_type
        self.metadata = metadata or {}
        self.mentions = mentions or []
        self.reply_to = reply_to
        self.created_at = created_at or datetime.now(timezone.utc)
        self.edited_at = edited_at
        self.is_deleted = is_deleted
        self.deleted_at = deleted_at


# Store
_messages: Dict[str, MessageStore] = {}


def get_message_store() -> Dict[str, MessageStore]:
    """Get messages store."""
    return _messages


def clear_message_store() -> None:
    """Clear all messages (for testing)."""
    _messages.clear()


# ============================================================================
# Helper Functions
# ============================================================================

def get_message_or_404(message_id: str) -> MessageStore:
    """Get message by ID or raise 404."""
    if message_id not in _messages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    return _messages[message_id]


def check_message_access(message_id: str, user_id: str) -> MessageStore:
    """Check if user can access a message.
    
    Returns the message if access is allowed.
    Raises HTTPException if not found or not a member.
    """
    message = get_message_or_404(message_id)
    
    # Check if user is a member of the conversation
    if not is_member(message.conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this conversation",
        )
    
    return message


def is_member(conversation_id: str, user_id: str) -> bool:
    """Check if user is a member of the conversation."""
    key = (conversation_id, user_id)
    member = _members.get(key)
    return member is not None and member.is_active


def can_edit_message(message: MessageStore, user_id: str, is_agent: bool = False) -> bool:
    """Check if user can edit a message.
    
    - Owner/admin can edit any message
    - Human members can edit their own messages
    - Regular agents cannot edit their own messages
    """
    # Owner/admin can edit any message
    role = get_member_role(message.conversation_id, user_id)
    if role in ("owner", "admin"):
        return True
    
    # Sender is trying to edit their own message
    if message.sender_id == user_id:
        # Regular agents cannot edit their own messages
        if is_agent:
            return False
        return True
    
    return False


def can_delete_message(message: MessageStore, user_id: str, is_agent: bool = False) -> bool:
    """Check if user can delete a message.
    
    - Owner/admin can delete any message
    - Human members can delete their own messages
    - Regular agents cannot delete their own messages
    """
    # Same rules as edit
    return can_edit_message(message, user_id, is_agent)


# ============================================================================
# Conversation-Scoped API Endpoints (GET/POST /{conversation_id}/messages)
# ============================================================================

@conversation_messages_router.get(
    "/{conversation_id}/messages",
    response_model=MessageListResponse,
    summary="List messages",
)
async def list_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[datetime] = None,
    after: Optional[datetime] = None,
    current_user: UserCredentials = Depends(get_current_user),
) -> MessageListResponse:
    """List messages in a conversation with pagination.
    
    - **conversation_id**: Conversation UUID
    - **limit**: Maximum number of messages (1-100)
    - **before**: Get messages before this timestamp (for backward pagination)
    - **after**: Get messages after this timestamp (for forward pagination)
    """
    # Check conversation access
    check_conversation_access(conversation_id, current_user.user_id)
    
    # Get messages for this conversation
    conv_messages = [
        msg for msg in _messages.values()
        if msg.conversation_id == conversation_id and not msg.is_deleted
    ]
    
    # Sort by created_at descending (newest first)
    conv_messages.sort(key=lambda m: m.created_at, reverse=True)
    
    # Apply time filters
    if before:
        conv_messages = [m for m in conv_messages if m.created_at < before]
    if after:
        conv_messages = [m for m in conv_messages if m.created_at > after]
    
    # Calculate pagination
    has_more = len(conv_messages) > limit
    paginated = conv_messages[:limit]
    
    # Get next cursor (timestamp of last item)
    next_cursor = None
    if has_more and paginated:
        next_cursor = paginated[-1].created_at.isoformat()
    
    items = [
        MessageResponse(
            id=msg.id,
            conversation_id=msg.conversation_id,
            sender_id=msg.sender_id,
            content=msg.content,
            content_type=msg.content_type,
            metadata=msg.metadata,
            mentions=msg.mentions,
            reply_to=msg.reply_to,
            is_deleted=msg.is_deleted,
            created_at=msg.created_at,
            edited_at=msg.edited_at,
        )
        for msg in paginated
    ]
    
    return MessageListResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@conversation_messages_router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send message",
)
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> MessageResponse:
    """Send a new message to a conversation.
    
    - **conversation_id**: Conversation UUID
    - **content**: Message content
    - **content_type**: Content type (text/markdown/image/file)
    - **mentions**: List of mentioned user IDs
    - **reply_to**: Message ID being replied to
    """
    # Check conversation access
    check_conversation_access(conversation_id, current_user.user_id)
    
    # Verify reply_to message exists and is in same conversation
    if request.reply_to:
        reply_msg = get_message_or_404(request.reply_to)
        if reply_msg.conversation_id != conversation_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reply-to message is in a different conversation",
            )
    
    # Create message
    msg_id = str(uuid4())
    now = datetime.now(timezone.utc)
    
    msg = MessageStore(
        id=msg_id,
        conversation_id=conversation_id,
        sender_id=current_user.user_id,
        content=request.content,
        content_type=request.content_type,
        mentions=request.mentions,
        reply_to=request.reply_to,
        created_at=now,
    )
    _messages[msg_id] = msg
    
    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        content=msg.content,
        content_type=msg.content_type,
        metadata=msg.metadata,
        mentions=msg.mentions,
        reply_to=msg.reply_to,
        is_deleted=msg.is_deleted,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
    )


# ============================================================================
# Standalone Message API Endpoints (PUT/DELETE /{message_id})
# ============================================================================

@message_ops_router.put(
    "/{message_id}",
    response_model=MessageResponse,
    summary="Edit message",
)
async def update_message(
    message_id: str,
    request: UpdateMessageRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> MessageResponse:
    """Edit an existing message.
    
    Only the sender or admin/owner can edit a message.
    
    - **message_id**: Message UUID
    - **content**: New message content
    """
    # Get message and check access
    message = get_message_or_404(message_id)
    
    if message.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    
    # Check if user can edit
    if not can_edit_message(message, current_user.user_id, current_user.is_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own messages",
        )
    
    # Update message
    message.content = request.content
    message.edited_at = datetime.now(timezone.utc)
    
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        content=message.content,
        content_type=message.content_type,
        metadata=message.metadata,
        mentions=message.mentions,
        reply_to=message.reply_to,
        is_deleted=message.is_deleted,
        created_at=message.created_at,
        edited_at=message.edited_at,
    )


@message_ops_router.delete(
    "/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete message",
)
async def delete_message(
    message_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> None:
    """Delete a message (soft delete).
    
    Only the sender or admin/owner can delete a message.
    
    - **message_id**: Message UUID
    """
    # Get message and check access
    message = get_message_or_404(message_id)
    
    if message.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    
    # Check if user can delete
    if not can_delete_message(message, current_user.user_id, current_user.is_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own messages",
        )
    
    # Soft delete
    message.is_deleted = True
    message.deleted_at = datetime.now(timezone.utc)
