"""Kernel module - core infrastructure components.

Phase 2 implements:
- Session Manager (session.py): WebSocket connection lifecycle
- Event Bus (event.py): Plugin communication
- Message Router (message.py): Stream buffer & dispatch
- Auth Service (auth.py): JWT & password authentication
"""

from sprinkle.kernel.session import (
    SessionManager,
    SessionState,
    SessionData,
    ConnectionPool,
)
from sprinkle.kernel.event import (
    EventBus,
    EventData,
    EventRegistry,
    get_event_bus,
    set_event_bus,
)
from sprinkle.kernel.message import (
    MessageRouter,
    Message,
    StreamMessage,
    MessageType,
    ContentType,
    StreamBuffer,
    MessageQueue,
    MessageDispatcher,
)
from sprinkle.kernel.auth import (
    AuthService,
    TokenData,
    UserCredentials,
)

__all__ = [
    # Session
    "SessionManager",
    "SessionState",
    "SessionData",
    "ConnectionPool",
    # Event
    "EventBus",
    "EventData",
    "EventRegistry",
    "get_event_bus",
    "set_event_bus",
    # Message
    "MessageRouter",
    "Message",
    "StreamMessage",
    "MessageType",
    "ContentType",
    "StreamBuffer",
    "MessageQueue",
    "MessageDispatcher",
    # Auth
    "AuthService",
    "TokenData",
    "UserCredentials",
]
