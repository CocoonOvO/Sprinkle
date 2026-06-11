"""Agent Gateway Services - Gateway client implementations for Sprinkle.

This module provides abstractions and implementations for communicating
with external agent gateways (like OpenClaw Gateway, MCP servers, etc.).

Example:
    from sprinkle.services.agent_gateway import gateway_manager, GatewayProvider
    
    # Register a gateway
    gateway_manager.register(GatewayProvider.OPENCLAW, OpenClawGatewayClient)
    
    # Get a gateway instance
    client = gateway_manager.get(GatewayProvider.OPENCLAW, agent_id="scone")
    
    # Initialize
    await gateway_manager.initialize_all()
    
    # Route an event
    message_id = await gateway_manager.route_event(
        event=event_data,
        agent_id="scone",
        provider=GatewayProvider.OPENCLAW,
    )
"""

from __future__ import annotations

from sprinkle.services.agent_gateway.base import (
    GatewayProvider,
    AgentGatewayError,
    GatewayTimeoutError,
    GatewayAuthError,
    GatewayRateLimitError,
    GatewayConnectionError,
    GatewayValidationError,
    AgentResponse,
    AgentGatewayClient,
)
from sprinkle.services.agent_gateway.manager import (
    AgentGatewayManager,
    gateway_manager,
)
from sprinkle.services.agent_gateway.openclaw import (
    OpenClawGatewayClient,
)

__version__ = "0.1.0"

__all__ = [
    # Provider
    "GatewayProvider",
    # Exceptions
    "AgentGatewayError",
    "GatewayTimeoutError",
    "GatewayAuthError",
    "GatewayRateLimitError",
    "GatewayConnectionError",
    "GatewayValidationError",
    # Data structures
    "AgentResponse",
    # Base class
    "AgentGatewayClient",
    # Manager
    "AgentGatewayManager",
    "gateway_manager",
    # OpenClaw implementation
    "OpenClawGatewayClient",
]