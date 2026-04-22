"""Push notification database models for Sprinkle.

This module contains the SQLAlchemy models for:
- Agent subscriptions to conversations
- Push notification templates
"""

import enum
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

from sprinkle.storage.database import Base


# ============================================================================
# SubscriptionMode Enum (DB-level)
# ============================================================================

class SubscriptionMode(str, enum.Enum):
    """Subscription mode stored in database."""
    DIRECT = "direct"
    MENTION_ONLY = "mention_only"
    UNLIMITED = "unlimited"
    EVENT_BASED = "event_based"


# ============================================================================
# AgentSubscriptionModel
# ============================================================================

class AgentSubscriptionModel(Base):
    """Agent subscription to a conversation.
    
    Stores the subscription mode and optional event filter list
    for each agent-conversation pair.
    
    Attributes:
        id: Primary key (UUID)
        agent_id: Foreign key to users.id
        conversation_id: Foreign key to conversations.id
        mode: Subscription mode (direct/mention_only/unlimited/event_based)
        subscribed_events: For EVENT_BASED mode, list of event types to receive
        created_at: When the subscription was created
        updated_at: When the subscription was last updated
    """
    __tablename__ = "agent_subscriptions"
    
    id = Column(String(36), primary_key=True)
    agent_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    mode = Column(SQLEnum(SubscriptionMode), default=SubscriptionMode.MENTION_ONLY, nullable=False)
    subscribed_events = Column(JSONB, default=list, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("agent_id", "conversation_id", name="uq_agent_conv"),
    )


# ============================================================================
# PushTemplateModel
# ============================================================================

class PushTemplateModel(Base):
    """Push notification template configuration.
    
    Templates define how push notifications are rendered for different
    event types. Each template has a name, format, and content with
    optional Jinja2-style variable placeholders.
    
    Attributes:
        id: Primary key (UUID)
        name: Unique template name (e.g., "chat.message", "group.member.joined")
        format: Output format (markdown/html/text)
        content: Template content with variable placeholders
        quick_replies: Optional quick reply buttons/actions
        is_active: Whether this template is active
        created_at: When the template was created
        updated_at: When the template was last updated
    """
    __tablename__ = "push_templates"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    format = Column(String(20), default="markdown", nullable=False)
    content = Column(Text, nullable=False)
    quick_replies = Column(JSONB, default=list, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
