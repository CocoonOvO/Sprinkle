"""Push event router for Sprinkle notifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from sprinkle.push.events import PushEvent, PushEventData
from sprinkle.push.subscription import AgentSubscription, SubscriptionMode, SubscriptionService
from sprinkle.push.templates import PushTemplateEngine

if TYPE_CHECKING:
    from sprinkle.push.subscription import SubscriptionService
    from sprinkle.push.templates import PushTemplateEngine

logger = logging.getLogger(__name__)


# ============================================================================
# PushRouter
# ============================================================================

class PushRouter:
    """Routes push events to appropriate agents based on subscriptions.
    
    The router determines which agents should receive a given push event
    by consulting the subscription service and applying the routing logic
    for each subscription mode.
    
    Example:
        router = PushRouter(subscription_service, template_engine)
        
        # Route an event to agents
        target_agent_ids = await router.route_event(event_data)
        
        for agent_id in target_agent_ids:
            # Render and send push notification
            ...
    """
    
    def __init__(
        self,
        subscription_service: SubscriptionService,
        template_engine: PushTemplateEngine,
    ):
        """Initialize the push router.
        
        Args:
            subscription_service: Service for querying subscriptions
            template_engine: Service for rendering notification content
        """
        self._subscription_service = subscription_service
        self._template_engine = template_engine
    
    async def route_event(self, event: PushEventData) -> List[str]:
        """Determine which agents should receive a push event.
        
        This method queries all subscriptions for the event's conversation
        and filters them based on subscription mode and event type.
        
        Args:
            event: The push event data
        
        Returns:
            List of agent IDs that should receive this event
        """
        # Get all subscriptions for this conversation
        subscriptions = await self._subscription_service.get_conversation_subscriptions(
            event.conversation_id
        )
        
        target_agents = []
        
        for sub in subscriptions:
            # Check if this subscription should receive the event
            should_push = await self._subscription_service.should_push_to_agent(event, sub)
            
            if should_push:
                target_agents.append(sub.agent_id)
                logger.debug(
                    f"Routing event {event.event.value} to agent {sub.agent_id} "
                    f"(mode={sub.mode.value})"
                )
        
        return target_agents
    
    async def should_push_to_mention(
        self,
        event: PushEventData,
        agent_id: str,
    ) -> bool:
        """Check if an agent should receive a push for being mentioned.
        
        This is used when an agent has MENTION_ONLY subscription mode
        and determines whether they are mentioned in the event.
        
        Args:
            event: The push event data
            agent_id: The agent's user ID
        
        Returns:
            True if the agent should receive the push
        """
        # Check if agent is in target_ids (mentioned users)
        if agent_id in event.target_ids:
            return True
        
        # Check mentions in metadata
        mentions = event.metadata.get("mentions", [])
        if agent_id in mentions:
            return True
        
        # MENTION event type always goes to mentioned agents
        if event.event == PushEvent.MENTION:
            return True
        
        return False
    
    async def should_push_to_event_subscription(
        self,
        event: PushEventData,
        subscription: AgentSubscription,
    ) -> bool:
        """Check if an agent should receive an event based on EVENT_BASED subscription.
        
        Args:
            event: The push event data
            subscription: The agent's subscription
        
        Returns:
            True if the event type is in the subscription's event list
        """
        if subscription.mode != SubscriptionMode.EVENT_BASED:
            return False
        
        return event.event in subscription.subscribed_events
    
    async def route_event_with_render(
        self,
        event: PushEventData,
    ) -> List[tuple[str, str]]:
        """Route an event and render content for each target agent.
        
        This is a convenience method that combines routing with template
        rendering, returning agent IDs with their rendered notification content.
        
        Args:
            event: The push event data
        
        Returns:
            List of (agent_id, rendered_content) tuples
        """
        target_agents = await self.route_event(event)
        
        if not target_agents:
            return []
        
        # Build template context based on event type
        context = self._build_template_context(event)
        
        # Render for each agent
        results = []
        for agent_id in target_agents:
            try:
                content = await self._template_engine.render(event.template_name, context)
                results.append((agent_id, content))
            except Exception as e:
                logger.error(f"Failed to render template for agent {agent_id}: {e}")
                # Still include agent but with raw content
                results.append((agent_id, str(event.content)))
        
        return results
    
    def _build_template_context(self, event: PushEventData) -> dict:
        """Build template rendering context from event data.
        
        Args:
            event: The push event data
        
        Returns:
            Dictionary of context variables for template rendering
        """
        context = {
            "event": event.event.value if isinstance(event.event, PushEvent) else event.event,
            "conversation_id": event.conversation_id,
            "sender_id": event.sender_id,
            "content": event.content,
            "metadata": event.metadata,
            "created_at": event.created_at,
        }
        
        # Add event-specific context
        if event.event in (
            PushEvent.CHAT_MESSAGE,
            PushEvent.CHAT_MESSAGE_EDITED,
            PushEvent.CHAT_MESSAGE_REPLY,
        ):
            context["reply_to"] = event.metadata.get("reply_to")
            context["mentions"] = event.target_ids
        
        elif event.event in (
            PushEvent.GROUP_MEMBER_JOINED,
            PushEvent.GROUP_MEMBER_LEFT,
            PushEvent.GROUP_MEMBER_KICKED,
        ):
            context["actor_id"] = event.sender_id
            context["target_ids"] = event.target_ids
        
        elif event.event == PushEvent.MENTION:
            context["mentioned_users"] = event.target_ids
        
        return context
