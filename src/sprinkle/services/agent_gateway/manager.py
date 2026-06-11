"""Agent Gateway Manager - Factory for managing gateway instances."""

from __future__ import annotations

import logging
from typing import Optional

from sprinkle.push.events import PushEventData
from sprinkle.services.agent_gateway.base import (
    AgentGatewayClient,
    GatewayProvider,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Agent Gateway Manager
# ============================================================================

class AgentGatewayManager:
    """Manager for agent gateway clients.
    
    This is a factory that manages multiple gateway instances.
    It supports registering gateway classes and getting/creating instances.
    
    Example:
        manager = AgentGatewayManager()
        
        # Register gateway classes
        manager.register(GatewayProvider.OPENCLAW, OpenClawGatewayClient)
        manager.register(GatewayProvider.MCP, MCPGatewayClient)
        
        # Get gateway instance
        client = manager.get(GatewayProvider.OPENCLAW, agent_id="scone")
        
        # Route event to gateway
        message_id = await manager.route_event(
            event=event_data,
            agent_id="scone",
            provider=GatewayProvider.OPENCLAW,
        )
        
        # Lifecycle management
        await manager.initialize_all()
        await manager.close_all()
    """
    
    def __init__(self):
        """Initialize the gateway manager."""
        self._gateway_classes: dict[GatewayProvider, type[AgentGatewayClient]] = {}
        self._instances: dict[str, AgentGatewayClient] = {}
    
    def register(
        self,
        provider: GatewayProvider,
        gateway_class: type[AgentGatewayClient],
    ) -> None:
        """Register a gateway class.
        
        Args:
            provider: The gateway provider type.
            gateway_class: The gateway class to register.
        
        Raises:
            TypeError: If gateway_class is not a subclass of AgentGatewayClient.
        """
        if not issubclass(gateway_class, AgentGatewayClient):
            raise TypeError(
                f"Gateway class must be a subclass of AgentGatewayClient, "
                f"got {gateway_class.__name__}"
            )
        
        self._gateway_classes[provider] = gateway_class
        logger.debug(f"Registered gateway class: {provider.value} -> {gateway_class.__name__}")
    
    def get(
        self,
        provider: GatewayProvider,
        agent_id: Optional[str] = None,
        **config,
    ) -> AgentGatewayClient:
        """Get or create a gateway instance.
        
        If an instance for the given provider and agent_id already exists,
        it will be reused. Otherwise, a new instance will be created.
        
        Args:
            provider: The gateway provider type.
            agent_id: Optional agent ID for multi-instance support.
            **config: Additional configuration passed to the gateway constructor.
        
        Returns:
            The gateway client instance.
        
        Raises:
            ValueError: If the provider is not registered.
        """
        # Build instance key
        key = f"{provider.value}:{agent_id}" if agent_id else provider.value
        
        # Return existing instance if available
        if key in self._instances:
            return self._instances[key]
        
        # Create new instance
        if provider not in self._gateway_classes:
            raise ValueError(
                f"Unknown gateway provider: {provider.value}. "
                f"Available providers: {[p.value for p in self._gateway_classes.keys()]}"
            )
        
        gateway_class = self._gateway_classes[provider]
        instance = gateway_class(**config)
        self._instances[key] = instance
        
        logger.debug(f"Created new gateway instance: {key} ({gateway_class.__name__})")
        return instance
    
    def get_provider_for_agent(self, agent_id: str) -> GatewayProvider:
        """Get the default gateway provider for an agent.
        
        This can be overridden to support per-agent gateway configuration.
        
        Args:
            agent_id: The agent ID.
        
        Returns:
            The default gateway provider (OPENCLAW).
        """
        return GatewayProvider.OPENCLAW
    
    async def initialize_all(self) -> None:
        """Initialize all registered gateway instances.
        
        Calls initialize() on each gateway instance that has been created.
        """
        for key, instance in self._instances.items():
            try:
                await instance.initialize()
                logger.info(f"Initialized gateway: {key}")
            except Exception as e:
                logger.error(f"Failed to initialize gateway {key}: {e}")
    
    async def close_all(self) -> None:
        """Close all gateway instances and clear the registry.
        
        Calls close() on each gateway instance and removes them from the manager.
        """
        for key, instance in list(self._instances.items()):
            try:
                await instance.close()
                logger.info(f"Closed gateway: {key}")
            except Exception as e:
                logger.error(f"Error closing gateway {key}: {e}")
        
        self._instances.clear()
        logger.debug("All gateway instances closed")
    
    async def route_event(
        self,
        event: PushEventData,
        agent_id: str,
        provider: Optional[GatewayProvider] = None,
    ) -> Optional[str]:
        """Route a push event to the appropriate gateway.
        
        This checks if the gateway supports the event type and forwards
        the event to the gateway for processing.
        
        Args:
            event: The push event data.
            agent_id: Target agent ID.
            provider: Gateway provider to use. If None, uses get_provider_for_agent().
        
        Returns:
            message_id if the event was routed successfully, None otherwise.
        
        Raises:
            ValueError: If the provider is not registered.
        """
        # Determine provider
        if provider is None:
            provider = self.get_provider_for_agent(agent_id)
        
        # Get gateway instance
        try:
            gateway = self.get(provider, agent_id)
        except ValueError as e:
            logger.warning(f"Cannot route event: {e}")
            return None
        
        # Check if gateway supports this event type
        if event.event not in gateway.supported_events:
            logger.debug(
                f"Gateway {provider.value} does not support event {event.event.value}"
            )
            return None
        
        # Route to gateway
        try:
            message_id = await gateway.send_message(
                agent_id=agent_id,
                conversation_id=event.conversation_id,
                content=str(event.content) if event.content else "",
                metadata=event.metadata,
            )
            logger.debug(
                f"Routed event to gateway: event={event.event.value}, "
                f"agent_id={agent_id}, message_id={message_id}"
            )
            return message_id
        except Exception as e:
            logger.error(f"Failed to route event to gateway: {e}")
            return None
    
    def list_registered_providers(self) -> list[GatewayProvider]:
        """List all registered gateway providers.
        
        Returns:
            List of registered GatewayProvider enums.
        """
        return list(self._gateway_classes.keys())
    
    def list_active_instances(self) -> list[str]:
        """List all active gateway instance keys.
        
        Returns:
            List of instance keys (format: "provider:agent_id" or "provider").
        """
        return list(self._instances.keys())


# ============================================================================
# Global Instance
# ============================================================================

# Global gateway manager instance
gateway_manager = AgentGatewayManager()


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "AgentGatewayManager",
    "gateway_manager",
]