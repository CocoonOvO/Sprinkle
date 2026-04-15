"""ConversationMember model for managing conversation participants."""

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum
from datetime import datetime
from . import Base
import enum


class MemberRole(str, enum.Enum):
    """Member role enumeration."""
    owner = "owner"
    admin = "admin"
    member = "member"
    agent = "agent"


class ConversationMember(Base):
    """ConversationMember model representing a user's membership in a conversation."""
    
    __tablename__ = "conversation_members"
    
    id = Column(String(36), primary_key=True)  # UUID
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    role = Column(SQLEnum(MemberRole), default=MemberRole.member, nullable=False)
    invited_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)
