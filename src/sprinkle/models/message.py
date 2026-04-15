"""Message model for Sprinkle."""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Enum as SQLEnum
from datetime import datetime
from . import Base
import enum


class ContentType(str, enum.Enum):
    text = "text"
    markdown = "markdown"


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True)  # UUID
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    sender_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    content_type = Column(SQLEnum(ContentType), default=ContentType.text, nullable=False)
    reply_to_id = Column(String(36), ForeignKey("messages.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
