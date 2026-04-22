"""Subscription management for Sprinkle push notifications."""

from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import JSONB

from sprinkle.models.push import AgentSubscriptionModel, SubscriptionMode as DBSubscriptionMode
from sprinkle.push.events import PushEvent, PushEventData

if TYPE_CHECKING:
    from sprinkle.push.events import PushEventData

logger = logging.getLogger(__name__)


# ============================================================================
# SubscriptionMode Enum
# ============================================================================

class SubscriptionMode(str, enum.Enum):
    """Agent subscription mode for a conversation.
    
    - DIRECT: Agent receives all events in the conversation (no filtering)
    - MENTION_ONLY: Agent only receives events when mentioned
    - UNLIMITED: Agent receives all events (alias for DIRECT, semantic distinction)
    - EVENT_BASED: Agent only receives specifically subscribed events
    """
    DIRECT = "direct"
    MENTION_ONLY = "mention_only"
    UNLIMITED = "unlimited"
    EVENT_BASED = "event_based"


# ============================================================================
# AgentSubscription dataclass (domain object)
# ============================================================================

@dataclass
class AgentSubscription:
    """Domain object representing an agent's subscription to a conversation.
    
    This is the in-memory representation used by the push system.
    """
    agent_id: str
    conversation_id: str
    mode: SubscriptionMode
    subscribed_events: Set[PushEvent] = field(default_factory=set)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_model(self) -> AgentSubscriptionModel:
        """Convert to database model."""
        return AgentSubscriptionModel(
            id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            conversation_id=self.conversation_id,
            mode=DBSubscriptionMode(self.mode.value),
            subscribed_events=[e.value for e in self.subscribed_events],
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
    
    @classmethod
    def from_model(cls, model: AgentSubscriptionModel) -> AgentSubscription:
        """Create from database model."""
        events = set()
        if model.subscribed_events:
            for e in model.subscribed_events:
                try:
                    events.add(PushEvent(e))
                except ValueError:
                    logger.warning(f"Unknown push event type: {e}")
        return cls(
            agent_id=model.agent_id,
            conversation_id=model.conversation_id,
            mode=SubscriptionMode(model.mode.value),
            subscribed_events=events,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


# ============================================================================
# SubscriptionService
# ============================================================================

class SubscriptionService:
    """Service for managing agent subscriptions.
    
    This service handles CRUD operations for agent subscriptions,
    including subscribe, unsubscribe, and querying subscriptions.
    
    Example:
        service = SubscriptionService(db_session)
        
        # Subscribe an agent to a conversation
        sub = await service.subscribe(
            agent_id="agent_123",
            conversation_id="conv_456",
            mode=SubscriptionMode.MENTION_ONLY,
        )
        
        # Check if an event should be pushed to an agent
        should_push = await service.should_push_to_agent(event, sub)
    """
    
    def __init__(self, db_session: AsyncSession):
        """Initialize the subscription service.
        
        Args:
            db_session: SQLAlchemy async session
        """
        self._db = db_session
    
    async def subscribe(
        self,
        agent_id: str,
        conversation_id: str,
        mode: SubscriptionMode,
        events: Optional[List[PushEvent]] = None,
    ) -> AgentSubscription:
        """Subscribe an agent to a conversation.
        
        If a subscription already exists, updates it.
        
        Args:
            agent_id: The agent's user ID
            conversation_id: The conversation to subscribe to
            mode: The subscription mode
            events: For EVENT_BASED mode, the list of events to subscribe to
        
        Returns:
            The created or updated subscription
        """
        # Check for existing subscription
        existing = await self.get_subscription(agent_id, conversation_id)
        
        now = datetime.now(timezone.utc)
        
        if existing:
            # Update existing
            existing.mode = mode
            existing.subscribed_events = set(events) if events else set()
            existing.updated_at = now
            
            # Persist to DB
            stmt = select(AgentSubscriptionModel).where(
                AgentSubscriptionModel.agent_id == agent_id,
                AgentSubscriptionModel.conversation_id == conversation_id,
            )
            result = await self._db.execute(stmt)
            model = result.scalar_one_or_none()
            if model:
                model.mode = DBSubscriptionMode(mode.value)
                model.subscribed_events = [e.value for e in existing.subscribed_events]
                model.updated_at = now
                await self._db.commit()
            
            return existing
        else:
            # Create new subscription
            subscribed_events = set(events) if events else set()
            sub = AgentSubscription(
                agent_id=agent_id,
                conversation_id=conversation_id,
                mode=mode,
                subscribed_events=subscribed_events,
                created_at=now,
                updated_at=now,
            )
            
            # Persist to DB
            model = sub.to_model()
            self._db.add(model)
            await self._db.commit()
            
            logger.info(f"Subscribed agent {agent_id} to conversation {conversation_id} with mode {mode.value}")
            return sub
    
    async def unsubscribe(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> bool:
        """Unsubscribe an agent from a conversation.
        
        Args:
            agent_id: The agent's user ID
            conversation_id: The conversation to unsubscribe from
        
        Returns:
            True if a subscription was deleted, False if not found
        """
        stmt = delete(AgentSubscriptionModel).where(
            AgentSubscriptionModel.agent_id == agent_id,
            AgentSubscriptionModel.conversation_id == conversation_id,
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        
        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Unsubscribed agent {agent_id} from conversation {conversation_id}")
        return deleted
    
    async def get_subscription(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> Optional[AgentSubscription]:
        """Get an agent's subscription to a conversation.
        
        Args:
            agent_id: The agent's user ID
            conversation_id: The conversation ID
        
        Returns:
            The subscription if found, None otherwise
        """
        stmt = select(AgentSubscriptionModel).where(
            AgentSubscriptionModel.agent_id == agent_id,
            AgentSubscriptionModel.conversation_id == conversation_id,
        )
        result = await self._db.execute(stmt)
        model = result.scalar_one_or_none()
        
        if model is None:
            return None
        
        return AgentSubscription.from_model(model)
    
    async def get_conversation_subscriptions(
        self,
        conversation_id: str,
    ) -> List[AgentSubscription]:
        """Get all subscriptions for a conversation.
        
        Args:
            conversation_id: The conversation ID
        
        Returns:
            List of all subscriptions for the conversation
        """
        stmt = select(AgentSubscriptionModel).where(
            AgentSubscriptionModel.conversation_id == conversation_id,
        )
        result = await self._db.execute(stmt)
        models = result.scalars().all()
        
        return [AgentSubscription.from_model(m) for m in models]
    
    async def should_push_to_agent(
        self,
        event: PushEventData,
        subscription: AgentSubscription,
    ) -> bool:
        """Determine if an event should be pushed to an agent based on subscription.
        
        Args:
            event: The push event data
            subscription: The agent's subscription
        
        Returns:
            True if the event should be pushed, False otherwise
        """
        # Check event-specific logic based on subscription mode
        if subscription.mode == SubscriptionMode.DIRECT:
            # Direct mode: push all events
            return True
        
        if subscription.mode == SubscriptionMode.UNLIMITED:
            # Unlimited: push all events (same as DIRECT but semantically different)
            return True
        
        if subscription.mode == SubscriptionMode.MENTION_ONLY:
            # Mention-only: check if agent is mentioned
            return self._is_agent_mentioned(event, subscription.agent_id)
        
        if subscription.mode == SubscriptionMode.EVENT_BASED:
            # Event-based: check if event type is in subscribed events
            return event.event in subscription.subscribed_events
        
        return False
    
    def _is_agent_mentioned(self, event: PushEventData, agent_id: str) -> bool:
        """Check if an agent is mentioned in the event.
        
        Args:
            event: The push event data
            agent_id: The agent's user ID
        
        Returns:
            True if the agent is mentioned
        """
        # Check target_ids (mentioned user IDs)
        if agent_id in event.target_ids:
            return True
        
        # For MENTION events, always push
        if event.event == PushEvent.MENTION:
            return True
        
        # Check mentions in metadata
        mentions = event.metadata.get("mentions", [])
        if agent_id in mentions:
            return True
        
        return False
