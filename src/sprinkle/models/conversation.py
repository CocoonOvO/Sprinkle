"""Conversation model for Sprinkle."""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Enum as SQLEnum
from datetime import datetime
from . import Base
import enum


class ConversationType(str, enum.Enum):
    direct = "direct"
    group = "group"


class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(String(36), primary_key=True)  # UUID
    type = Column(SQLEnum(ConversationType), default=ConversationType.direct, nullable=False)
    name = Column(String(255), nullable=False)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    extra_data = Column(Text, default="{}", nullable=False)  # JSON stored as text
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
