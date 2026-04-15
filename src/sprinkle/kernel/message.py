"""Message model for Sprinkle."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4


@dataclass
class Message:
    """
    Represents a message in the Sprinkle system.
    
    This is a simple data class that holds message information.
    In the full system, this would be more complex with database integration.
    
    Attributes:
        id: Unique message identifier.
        conversation_id: ID of the conversation this message belongs to.
        sender_id: ID of the user who sent the message.
        content: The message content.
        content_type: Type of content (text, markdown, image, file).
        created_at: When the message was created.
        metadata: Additional metadata dictionary.
        reply_to: ID of the message this is replying to.
        is_deleted: Whether the message is soft-deleted.
    """
    
    conversation_id: UUID
    sender_id: UUID
    content: str
    content_type: str = "text"
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    reply_to: Optional[UUID] = None
    is_deleted: bool = False
    
    def __post_init__(self):
        """Validate message after initialization."""
        if not self.content:
            raise ValueError("Message content cannot be empty")
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert message to dictionary.
        
        Returns:
            Dictionary representation of the message.
        """
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "sender_id": str(self.sender_id),
            "content": self.content,
            "content_type": self.content_type,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "reply_to": str(self.reply_to) if self.reply_to else None,
            "is_deleted": self.is_deleted,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """
        Create a Message from a dictionary.
        
        Args:
            data: Dictionary with message data.
            
        Returns:
            Message instance.
        """
        return cls(
            id=UUID(data["id"]) if isinstance(data["id"], str) else data["id"],
            conversation_id=UUID(data["conversation_id"]) if isinstance(data["conversation_id"], str) else data["conversation_id"],
            sender_id=UUID(data["sender_id"]) if isinstance(data["sender_id"], str) else data["sender_id"],
            content=data["content"],
            content_type=data.get("content_type", "text"),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data["created_at"], str) else data["created_at"],
            metadata=data.get("metadata", {}),
            reply_to=UUID(data["reply_to"]) if data.get("reply_to") else None,
            is_deleted=data.get("is_deleted", False),
        )
