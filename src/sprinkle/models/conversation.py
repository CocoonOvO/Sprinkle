"""Conversation model for Sprinkle."""

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
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
    extra_data = Column(JSONB, default={}, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
