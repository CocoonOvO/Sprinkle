"""Message API endpoints - database-backed."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user, get_db_session
from sprinkle.api.conversations import (
    check_conversation_access,
    check_admin_access,
    is_owner,
    is_admin,
    get_member_role,
    is_member as check_conversation_member,
)
from sprinkle.models import Message, ContentType
from sprinkle.storage.database import SessionLocal

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
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

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
# Stub In-Memory Store (kept for backward compatibility with tests only)
# ============================================================================
# The API no longer uses these - all data goes through the database.
# Tests may still write to these stubs but the API will not read from them.

class MessageStore:
    """Message data store (stub for test compatibility)."""
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


# Stub stores (not used by API anymore, but tests may write to them)
_messages: Dict[str, MessageStore] = {}


def get_message_store() -> Dict[str, MessageStore]:
    """Get messages store (stub - not used by API, for test compatibility)."""
    return _messages


# ============================================================================
# Store Management (for testing)
# ============================================================================

def clear_message_store() -> None:
    """Clear all messages (for testing).

    Clears database tables.
    """
    db = SessionLocal()
    try:
        from sprinkle.models import Message
        db.query(Message).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================================
# Helper Functions
# ============================================================================

def _message_to_response(msg: Message) -> MessageResponse:
    """Convert a Message model to MessageResponse."""
    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        content=msg.content,
        content_type=msg.content_type.value if isinstance(msg.content_type, ContentType) else msg.content_type,
        metadata=msg.message_metadata or {},
        mentions=[],
        reply_to=msg.reply_to_id,
        is_deleted=msg.is_deleted,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        deleted_at=msg.deleted_at,
        deleted_by=msg.deleted_by,
    )


async def get_message_or_404_db(db: AsyncSession, message_id: str) -> Message:
    """Get message by ID from database or raise 404."""
    result = await db.execute(
        select(Message).where(Message.id == message_id)
    )
    msg = result.scalar_one_or_none()
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    return msg


async def check_message_access_db(db: AsyncSession, message_id: str, user_id: str) -> Message:
    """Check if user can access a message.

    Returns the message if access is allowed.
    Raises HTTPException if not found or not a member.
    """
    message = await get_message_or_404_db(db, message_id)

    # Check if user is a member of the conversation
    if not check_conversation_member(message.conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this conversation",
        )

    return message


def is_member(conversation_id: str, user_id: str) -> bool:
    """Check if user is a member of the conversation."""
    return check_conversation_member(conversation_id, user_id)


def can_edit_message(message: Message, user_id: str, is_agent: bool = False) -> bool:
    """Check if user can edit a message.

    - Owner can edit any message
    - Admin can edit any message
    - Human members can edit their own messages
    - Regular agents (role=member) cannot edit their own messages
    - Agent admins (role=admin) CAN edit their own messages
    - Agent owners CANNOT edit their own messages (special restriction)
    """
    # Check if sender is an agent trying to edit their own message
    if message.sender_id == user_id and is_agent:
        role = get_member_role(message.conversation_id, user_id)
        # Agents with role=admin can edit their own messages
        if role == "admin":
            return True
        # Agent owners cannot edit their own messages (special case)
        if role == "owner":
            return False
        # Regular agents (role=member) cannot edit their own messages
        return False

    # Owner/admin can edit any message
    role = get_member_role(message.conversation_id, user_id)
    if role in ("owner", "admin"):
        return True

    # Human sender can edit their own message
    if message.sender_id == user_id:
        return True

    return False


def can_delete_message(message: Message, user_id: str, is_agent: bool = False) -> bool:
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
    db: AsyncSession = Depends(get_db_session),
) -> MessageListResponse:
    """List messages in a conversation with pagination.

    - **conversation_id**: Conversation UUID
    - **limit**: Maximum number of messages (1-100)
    - **before**: Get messages before this timestamp (for backward pagination)
    - **after**: Get messages after this timestamp (for forward pagination)
    """
    # Check conversation access
    check_conversation_access(conversation_id, current_user.user_id)

    # Query messages from database
    query = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.is_deleted == False)  # noqa: E712
    )

    if before:
        query = query.where(Message.created_at < before)
    if after:
        query = query.where(Message.created_at > after)

    # Order by created_at descending (newest first)
    query = query.order_by(Message.created_at.desc())

    # Get one extra to check has_more
    query = query.limit(limit + 1)

    result = await db.execute(query)
    messages = result.scalars().all()

    # Check if there are more messages
    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    # Get next cursor (timestamp of last item)
    next_cursor = None
    if has_more and messages:
        next_cursor = messages[-1].created_at.isoformat()

    items = [_message_to_response(msg) for msg in messages]

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
    db: AsyncSession = Depends(get_db_session),
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
        reply_msg = await get_message_or_404_db(db, request.reply_to)
        if reply_msg.conversation_id != conversation_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reply-to message is in a different conversation",
            )

    # Parse content_type
    try:
        content_type_enum = ContentType(request.content_type)
    except ValueError:
        content_type_enum = ContentType.text

    # Create message in database
    msg = Message(
        id=str(uuid4()),
        conversation_id=conversation_id,
        sender_id=current_user.user_id,
        content=request.content,
        content_type=content_type_enum,
        reply_to_id=request.reply_to,
        is_deleted=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    return _message_to_response(msg)


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
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    """Edit an existing message.

    Only the sender or admin/owner can edit a message.

    - **message_id**: Message UUID
    - **content**: New message content
    """
    # Get message and check access
    message = await get_message_or_404_db(db, message_id)

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
    message.updated_at = datetime.utcnow()
    if message.edited_at is None:
        message.edited_at = datetime.utcnow()

    await db.commit()
    await db.refresh(message)

    return _message_to_response(message)


@message_ops_router.delete(
    "/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete message",
)
async def delete_message(
    message_id: str,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a message (soft delete).

    Only the sender or admin/owner can delete a message.

    - **message_id**: Message UUID
    """
    # Get message and check access
    message = await get_message_or_404_db(db, message_id)

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
    message.updated_at = datetime.utcnow()
    message.deleted_at = datetime.utcnow()

    await db.commit()
