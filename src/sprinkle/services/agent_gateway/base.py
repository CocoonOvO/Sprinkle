"""Agent Gateway Base - Abstract interfaces and data structures."""

from __future__ import annotations

import enum
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sprinkle.push.events import PushEvent

logger = logging.getLogger(__name__)


# ============================================================================
# Gateway Provider Enum
# ============================================================================

class GatewayProvider(str, enum.Enum):
    """Supported agent gateway providers."""
    
    OPENCLAW = "openclaw"
    MCP = "mcp"
    ACP = "acp"  # Reserved for future


# ============================================================================
# Exceptions
# ============================================================================

class AgentGatewayError(Exception):
    """Base exception for agent gateway errors."""
    
    def __init__(self, message: str, provider: Optional[GatewayProvider] = None):
        super().__init__(message)
        self.provider = provider


class GatewayTimeoutError(AgentGatewayError):
    """Raised when gateway request times out."""
    pass


class GatewayAuthError(AgentGatewayError):
    """Raised when authentication fails."""
    pass


class GatewayRateLimitError(AgentGatewayError):
    """Raised when rate limit is exceeded."""
    pass


class GatewayConnectionError(AgentGatewayError):
    """Raised when connection to gateway fails."""
    pass


class GatewayValidationError(AgentGatewayError):
    """Raised when request validation fails."""
    pass


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class AgentResponse:
    """Agent response data structure.
    
    This is passed to the response callback when an agent sends back a response.
    """
    
    message_id: str
    """Unique message ID linking to the original request."""
    
    agent_id: str
    """The agent that generated this response."""
    
    conversation_id: str
    """The conversation this response belongs to."""
    
    content: str
    """Response content."""
    
    content_type: str = "text"
    """Content type: text/markdown/image/file."""
    
    stream: bool = False
    """Whether this is a streaming response."""
    
    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional metadata."""
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    """When the response was created."""


# ============================================================================
# Abstract Base Class
# ============================================================================

class AgentGatewayClient(ABC):
    """Abstract base class for agent gateway clients.
    
    All gateway implementations must inherit from this class and implement
    the required abstract methods.
    
    Example:
        class MyGatewayClient(AgentGatewayClient):
            @property
            def provider(self) -> GatewayProvider:
                return GatewayProvider.MCP
            
            async def send_message(self, agent_id, conversation_id, content, ...) -> str:
                # Implement sending logic
                ...
        
        # Register and use
        manager = AgentGatewayManager()
        manager.register(GatewayProvider.MCP, MyGatewayClient)
        client = manager.get(GatewayProvider.MCP)
    """
    
    @property
    @abstractmethod
    def provider(self) -> GatewayProvider:
        """Return the gateway provider type.
        
        Returns:
            GatewayProvider enum value identifying this gateway type.
        """
        pass
    
    @property
    def supported_events(self) -> list[PushEvent]:
        """Return list of PushEvent types this gateway supports.
        
        Default implementation returns common chat events.
        Override to customize event filtering.
        
        Returns:
            List of PushEvent enums this gateway can handle.
        """
        return [
            PushEvent.CHAT_MESSAGE,
            PushEvent.CHAT_MESSAGE_REPLY,
            PushEvent.MENTION,
        ]
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the gateway connection.
        
        Called once when the gateway is first used.
        Should set up HTTP clients, WebSocket connections, etc.
        
        Raises:
            GatewayConnectionError: If initialization fails.
        """
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close the gateway connection.
        
        Called when the application is shutting down or when
        the gateway is no longer needed.
        
        Should clean up any resources (HTTP clients, WebSockets, etc.).
        """
        pass
    
    @abstractmethod
    async def send_message(
        self,
        agent_id: str,
        conversation_id: str,
        content: str,
        content_type: str = "text",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Send a message to an agent through the gateway.
        
        Args:
            agent_id: Target agent ID (unique identifier in the gateway).
            conversation_id: Associated conversation ID (for routing responses).
            content: Message content to send.
            content_type: Content type (text/markdown/image/file).
            metadata: Additional metadata (mentions, reply_to, etc.).
        
        Returns:
            message_id: A unique ID for tracking the request and correlating responses.
        
        Raises:
            GatewayValidationError: If the request is invalid.
            GatewayAuthError: If authentication fails.
            GatewayTimeoutError: If the request times out.
            GatewayConnectionError: If connection to gateway fails.
        """
        pass
    
    @abstractmethod
    async def set_response_callback(
        self,
        callback: Callable[[AgentResponse], Awaitable[None]],
    ) -> None:
        """Set the callback for receiving agent responses.
        
        The callback will be invoked whenever the gateway receives
        a response from an agent.
        
        Args:
            callback: Async callable that accepts AgentResponse.
        """
        pass
    
    @abstractmethod
    async def abort(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> bool:
        """Abort an ongoing agent session.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
        
        Returns:
            True if abort was successful, False otherwise.
        """
        pass
    
    @abstractmethod
    async def set_context(
        self,
        agent_id: str,
        conversation_id: str,
        context: dict[str, Any],
    ) -> None:
        """Set conversation context for an agent session.
        
        This allows setting things like system prompts, conversation prompts,
        or other session-level configuration.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
            context: Context data to set (keys depend on gateway implementation).
        """
        pass


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "GatewayProvider",
    "AgentGatewayError",
    "GatewayTimeoutError",
    "GatewayAuthError",
    "GatewayRateLimitError",
    "GatewayConnectionError",
    "GatewayValidationError",
    "AgentResponse",
    "AgentGatewayClient",
]