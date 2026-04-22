"""Message model for Sprinkle."""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from . import Base
import enum


class ContentType(str, enum.Enum):
    text = "text"
    markdown = "markdown"
    image = "image"
    file = "file"
    system = "system"


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True)  # UUID
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    sender_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    content_type = Column(SQLEnum(ContentType), default=ContentType.text, nullable=False)
    message_metadata = Column(JSONB, default={}, nullable=False)
    reply_to_id = Column(String(36), ForeignKey("messages.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    edited_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(36), ForeignKey("users.id"), nullable=True)
