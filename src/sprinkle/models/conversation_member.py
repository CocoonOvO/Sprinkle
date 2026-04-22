"""ConversationMember model for managing conversation participants."""

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum, Boolean
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
    
    # 联合主键，无单独 id
    conversation_id = Column(String(36), ForeignKey("conversations.id"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), primary_key=True)
    role = Column(SQLEnum(MemberRole), default=MemberRole.member, nullable=False)
    nickname = Column(String(100), nullable=True)
    invited_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    left_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
