"""Push event types and data structures for Sprinkle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime as dt


# ============================================================================
# PushEvent Enum
# ============================================================================

class PushEvent(str, enum.Enum):
    """Push notification event types.
    
    Categories:
    - CHAT_*: Chat message events
    - GROUP_*: Group management events  
    - SYSTEM_*: System notification events
    """
    
    # Chat message events
    CHAT_MESSAGE = "chat.message"
    CHAT_MESSAGE_EDITED = "chat.message.edited"
    CHAT_MESSAGE_DELETED = "chat.message.deleted"
    CHAT_MESSAGE_REPLY = "chat.message.reply"
    
    # Group management events
    GROUP_MEMBER_JOINED = "group.member.joined"
    GROUP_MEMBER_LEFT = "group.member.left"
    GROUP_MEMBER_KICKED = "group.member.kicked"
    GROUP_CREATED = "group.created"
    GROUP_DISBANDED = "group.disbanded"
    GROUP_INFO_UPDATED = "group.info.updated"
    
    # System events
    SYSTEM_NOTIFICATION = "system.notification"
    MENTION = "mention"


# ============================================================================
# PushEventData
# ============================================================================

@dataclass
class PushEventData:
    """Data structure for a push event.
    
    This is created when an event occurs and is passed through the
    push pipeline (router -> template engine -> delivery).
    
    Attributes:
        event: The type of push event
        conversation_id: The conversation this event belongs to
        sender_id: Who triggered this event
        target_ids: Who should receive this notification
        content: The raw event content
        metadata: Additional event metadata
        template_name: Which template to use for rendering
        created_at: When the event occurred
    """
    event: PushEvent
    conversation_id: str
    sender_id: str
    target_ids: List[str] = field(default_factory=list)
    content: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    template_name: str = "default"
    created_at: datetime = field(default_factory=lambda: dt.utcnow())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event": self.event.value if isinstance(self.event, PushEvent) else self.event,
            "conversation_id": self.conversation_id,
            "sender_id": self.sender_id,
            "target_ids": self.target_ids,
            "content": self.content,
            "metadata": self.metadata,
            "template_name": self.template_name,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PushEventData:
        """Create from a dictionary."""
        event = data.get("event")
        if isinstance(event, str):
            event = PushEvent(event)
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return cls(
            event=event,
            conversation_id=data["conversation_id"],
            sender_id=data["sender_id"],
            target_ids=data.get("target_ids", []),
            content=data.get("content"),
            metadata=data.get("metadata", {}),
            template_name=data.get("template_name", "default"),
            created_at=created_at or dt.utcnow(),
        )


# ============================================================================
# Template render context helpers
# ============================================================================

def build_mention_context(event_data: PushEventData) -> Dict[str, Any]:
    """Build template context for mention events."""
    return {
        "event": event_data.event.value,
        "conversation_id": event_data.conversation_id,
        "sender_id": event_data.sender_id,
        "mentioned_users": event_data.target_ids,
        "content": event_data.content,
        "metadata": event_data.metadata,
        "created_at": event_data.created_at,
    }


def build_group_event_context(event_data: PushEventData) -> Dict[str, Any]:
    """Build template context for group management events."""
    return {
        "event": event_data.event.value,
        "conversation_id": event_data.conversation_id,
        "actor_id": event_data.sender_id,
        "affected_users": event_data.target_ids,
        "metadata": event_data.metadata,
        "created_at": event_data.created_at,
    }


def build_message_context(event_data: PushEventData) -> Dict[str, Any]:
    """Build template context for chat message events."""
    return {
        "event": event_data.event.value,
        "conversation_id": event_data.conversation_id,
        "sender_id": event_data.sender_id,
        "content": event_data.content,
        "metadata": event_data.metadata,
        "reply_to": event_data.metadata.get("reply_to"),
        "mentions": event_data.target_ids,
        "created_at": event_data.created_at,
    }
