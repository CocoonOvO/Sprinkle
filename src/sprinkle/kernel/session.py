"""Session Manager - WebSocket connection and session lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    TypeVar,
)

import redis.asyncio as redis

from sprinkle.config import RedisConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SessionState(Enum):
    """Session state enumeration."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


# ============================================================================
# Session Data
# ============================================================================

@dataclass
class SessionData:
    """Session data structure stored in Redis."""
    session_id: str
    user_id: str
    connection_id: str
    state: SessionState = SessionState.CONNECTING
    subscriptions: Set[str] = field(default_factory=set)  # conversation_ids
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_ping: float = field(default_factory=time.time)
    reconnect_count: int = 0
    max_retry: int = 3


# ============================================================================
# Connection Pool
# ============================================================================

class ConnectionPool:
    """Manages WebSocket connection pool with Redis backend.
    
    Attributes:
        redis_url: Redis connection URL
        max_connections: Maximum number of connections in pool
        ping_interval: Heartbeat interval in seconds (default: 30s)
        ping_timeout: Heartbeat timeout in seconds (default: 10s)
        max_retry: Maximum reconnection attempts
    """
    
    def __init__(
        self,
        redis_config: RedisConfig,
        max_connections: int = 100,
        ping_interval: int = 30,
        ping_timeout: int = 10,
        max_retry: int = 3,
    ):
        self._redis_config = redis_config
        self._max_connections = max_connections
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._max_retry = max_retry
        
        # Connection pool
        self._pool: Optional[redis.ConnectionPool] = None
        self._redis_client: Optional[redis.Redis] = None
        
        # In-memory session store (mirrors Redis for fast access)
        self._memory_store: Dict[str, SessionData] = {}
        
        # Active connections tracking
        self._active_connections: Dict[str, asyncio.StreamWriter] = {}
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
        
        # Heartbeat tasks per session
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
        
        # Session event callbacks
        self._on_connect: Optional[Callable] = None
        self._on_disconnect: Optional[Callable] = None
        self._on_reconnect: Optional[Callable] = None
    
    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------
    
    async def initialize(self) -> None:
        """Initialize the connection pool and Redis connection."""
        self._pool = redis.ConnectionPool.from_url(
            self._redis_config.url,
            max_connections=self._max_connections,
            decode_responses=True,
        )
        self._redis_client = redis.Redis(connection_pool=self._pool)
        logger.info("Connection pool initialized")
    
    async def close(self) -> None:
        """Close all connections and cleanup resources."""
        # Cancel all heartbeat tasks
        for task in self._heartbeat_tasks.values():
            if not task.done():
                task.cancel()
        self._heartbeat_tasks.clear()
        
        # Close Redis connection
        if self._redis_client:
            await self._redis_client.close()
        if self._pool:
            await self._pool.disconnect()
        
        # Clear memory store
        self._memory_store.clear()
        self._active_connections.clear()
        
        logger.info("Connection pool closed")
    
    # ------------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------------
    
    async def create_session(
        self,
        session_id: str,
        user_id: str,
        connection_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionData:
        """Create a new session.
        
        Args:
            session_id: Unique session identifier
            user_id: User identifier
            connection_id: WebSocket connection identifier
            metadata: Optional session metadata
            
        Returns:
            Created SessionData
        """
        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            state=SessionState.CONNECTING,
            metadata=metadata or {},
            max_retry=self._max_retry,
        )
        
        async with self._lock:
            # Store in memory
            self._memory_store[session_id] = session
            # Store in Redis
            await self._store_session_redis(session)
        
        logger.debug(f"Session created: {session_id}")
        return session
    
    async def get_session(self, session_id: str) -> Optional[SessionData]:
        """Get session by ID (checks memory first, then Redis).
        
        Args:
            session_id: Session identifier
            
        Returns:
            SessionData if found, None otherwise
        """
        # Check memory first
        session = self._memory_store.get(session_id)
        if session:
            return session
        
        # Fall back to Redis
        session = await self._load_session_redis(session_id)
        if session:
            async with self._lock:
                self._memory_store[session_id] = session
        return session
    
    async def update_session(self, session: SessionData) -> None:
        """Update session data in both memory and Redis.
        
        Args:
            session: Session data to update
        """
        async with self._lock:
            self._memory_store[session.session_id] = session
            await self._store_session_redis(session)
    
    async def delete_session(self, session_id: str) -> None:
        """Delete a session.
        
        Args:
            session_id: Session identifier
        """
        async with self._lock:
            # Remove from memory
            self._memory_store.pop(session_id, None)
            # Remove from Redis
            if self._redis_client:
                await self._redis_client.delete(f"session:{session_id}")
            # Cancel heartbeat
            task = self._heartbeat_tasks.pop(session_id, None)
            if task and not task.done():
                task.cancel()
        
        logger.debug(f"Session deleted: {session_id}")
    
    async def get_user_sessions(self, user_id: str) -> List[SessionData]:
        """Get all sessions for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            List of SessionData for the user
        """
        sessions = []
        async with self._lock:
            for session in self._memory_store.values():
                if session.user_id == user_id:
                    sessions.append(session)
        return sessions
    
    # ------------------------------------------------------------------------
    # State Management
    # ------------------------------------------------------------------------
    
    async def set_state(self, session_id: str, state: SessionState) -> bool:
        """Set session state.
        
        Args:
            session_id: Session identifier
            state: New state
            
        Returns:
            True if updated, False if session not found
        """
        session = await self.get_session(session_id)
        if not session:
            return False
        
        session.state = state
        await self.update_session(session)
        
        # Trigger state change callbacks
        if state == SessionState.CONNECTED and self._on_connect:
            await self._on_connect(session)
        elif state == SessionState.DISCONNECTED and self._on_disconnect:
            await self._on_disconnect(session)
        
        return True
    
    async def subscribe(
        self,
        session_id: str,
        conversation_id: str,
    ) -> bool:
        """Subscribe session to a conversation.
        
        Args:
            session_id: Session identifier
            conversation_id: Conversation to subscribe to
            
        Returns:
            True if subscribed, False if session not found
        """
        session = await self.get_session(session_id)
        if not session:
            return False
        
        session.subscriptions.add(conversation_id)
        
        # Update Redis sorted set for subscription
        if self._redis_client:
            await self._redis_client.sadd(
                f"user:{session.user_id}:subscriptions",
                conversation_id,
            )
        
        await self.update_session(session)
        return True
    
    async def unsubscribe(
        self,
        session_id: str,
        conversation_id: str,
    ) -> bool:
        """Unsubscribe session from a conversation.
        
        Args:
            session_id: Session identifier
            conversation_id: Conversation to unsubscribe from
            
        Returns:
            True if unsubscribed, False if session not found
        """
        session = await self.get_session(session_id)
        if not session:
            return False
        
        session.subscriptions.discard(conversation_id)
        
        # Update Redis
        if self._redis_client:
            await self._redis_client.srem(
                f"user:{session.user_id}:subscriptions",
                conversation_id,
            )
        
        await self.update_session(session)
        return True
    
    # ------------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------------
    
    async def start_heartbeat(self, session_id: str) -> None:
        """Start heartbeat monitoring for a session.
        
        Args:
            session_id: Session identifier
        """
        task = asyncio.create_task(self._heartbeat_loop(session_id))
        self._heartbeat_tasks[session_id] = task
    
    async def stop_heartbeat(self, session_id: str) -> None:
        """Stop heartbeat monitoring for a session.
        
        Args:
            session_id: Session identifier
        """
        task = self._heartbeat_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
    
    async def _heartbeat_loop(self, session_id: str) -> None:
        """Heartbeat loop that sends ping and checks pong response.
        
        Args:
            session_id: Session identifier
        """
        while True:
            try:
                await asyncio.sleep(self._ping_interval)
                
                session = await self.get_session(session_id)
                if not session or session.state == SessionState.DISCONNECTED:
                    break
                
                # Update last ping time
                session.last_ping = time.time()
                await self.update_session(session)
                
                # Wait for pong with timeout
                try:
                    await asyncio.wait_for(
                        self._wait_for_pong(session_id),
                        timeout=self._ping_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Heartbeat timeout for session {session_id}, "
                        f"starting reconnection"
                    )
                    await self._handle_disconnect(session)
                    break
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error for session {session_id}: {e}")
                break
    
    async def _wait_for_pong(self, session_id: str) -> None:
        """Wait for pong response from client (pub/sub pattern).
        
        Args:
            session_id: Session identifier
        """
        # Redis pub/sub for pong response
        if not self._redis_client:
            return
        
        pubsub = self._redis_client.pubsub()
        channel = f"heartbeat:{session_id}"
        await pubsub.subscribe(channel)
        
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=self._ping_timeout,
            )
            if message and message["type"] == "message":
                data = message["data"]
                if data == "pong":
                    return
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
    
    async def receive_pong(self, session_id: str) -> None:
        """Called when client sends pong response.
        
        Args:
            session_id: Session identifier
        """
        session = await self.get_session(session_id)
        if session:
            session.last_ping = time.time()
            await self.update_session(session)
    
    async def _handle_disconnect(self, session: SessionData) -> None:
        """Handle session disconnection and attempt reconnection.
        
        Args:
            session: Session data
        """
        if session.reconnect_count >= session.max_retry:
            logger.warning(
                f"Session {session.session_id} exceeded max reconnection "
                f"attempts ({session.max_retry}), marking as disconnected"
            )
            session.state = SessionState.DISCONNECTED
            await self.update_session(session)
            
            if self._on_disconnect:
                await self._on_disconnect(session)
            return
        
        session.state = SessionState.RECONNECTING
        session.reconnect_count += 1
        await self.update_session(session)
        
        if self._on_reconnect:
            await self._on_reconnect(session)
    
    # ------------------------------------------------------------------------
    # Redis Persistence
    # ------------------------------------------------------------------------
    
    async def _store_session_redis(self, session: SessionData) -> None:
        """Store session in Redis.
        
        Args:
            session: Session data to store
        """
        if not self._redis_client:
            return
        
        key = f"session:{session.session_id}"
        data = {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "connection_id": session.connection_id,
            "state": session.state.value,
            "subscriptions": ",".join(session.subscriptions),
            "metadata": str(session.metadata),
            "created_at": str(session.created_at),
            "last_ping": str(session.last_ping),
            "reconnect_count": str(session.reconnect_count),
            "max_retry": str(session.max_retry),
        }
        
        # Store with 1 hour TTL
        await self._redis_client.hset(key, mapping=data)
        await self._redis_client.expire(key, 3600)
        
        # Add to user's session set
        await self._redis_client.sadd(f"user:{session.user_id}:sessions", session.session_id)
    
    async def _load_session_redis(self, session_id: str) -> Optional[SessionData]:
        """Load session from Redis.
        
        Args:
            session_id: Session identifier
            
        Returns:
            SessionData if found, None otherwise
        """
        if not self._redis_client:
            return None
        
        key = f"session:{session_id}"
        data = await self._redis_client.hgetall(key)
        
        if not data:
            return None
        
        subscriptions = set()
        if data.get("subscriptions"):
            subscriptions = set(data["subscriptions"].split(","))
        
        import ast
        metadata = {}
        if data.get("metadata"):
            try:
                metadata = ast.literal_eval(data["metadata"])
            except (ValueError, SyntaxError):
                pass
        
        return SessionData(
            session_id=data["session_id"],
            user_id=data["user_id"],
            connection_id=data["connection_id"],
            state=SessionState(data.get("state", "connecting")),
            subscriptions=subscriptions,
            metadata=metadata,
            created_at=float(data.get("created_at", time.time())),
            last_ping=float(data.get("last_ping", time.time())),
            reconnect_count=int(data.get("reconnect_count", 0)),
            max_retry=int(data.get("max_retry", 3)),
        )
    
    # ------------------------------------------------------------------------
    # Reconnection
    # ------------------------------------------------------------------------
    
    async def reconnect_session(
        self,
        session_id: str,
        new_connection_id: str,
    ) -> Optional[SessionData]:
        """Attempt to reconnect an existing session.
        
        Args:
            session_id: Session identifier
            new_connection_id: New WebSocket connection identifier
            
        Returns:
            Reconnected SessionData if successful, None otherwise
        """
        session = await self.get_session(session_id)
        if not session:
            return None
        
        if session.state == SessionState.DISCONNECTED:
            logger.warning(f"Cannot reconnect disconnected session {session_id}")
            return None
        
        session.connection_id = new_connection_id
        session.state = SessionState.CONNECTED
        session.last_ping = time.time()
        await self.update_session(session)
        
        # Restart heartbeat
        await self.start_heartbeat(session_id)
        
        logger.info(f"Session reconnected: {session_id}")
        return session
    
    # ------------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------------
    
    def set_on_connect(self, callback: Callable) -> None:
        """Set callback for session connection.
        
        Args:
            callback: Async callable(session: SessionData)
        """
        self._on_connect = callback
    
    def set_on_disconnect(self, callback: Callable) -> None:
        """Set callback for session disconnection.
        
        Args:
            callback: Async callable(session: SessionData)
        """
        self._on_disconnect = callback
    
    def set_on_reconnect(self, callback: Callable) -> None:
        """Set callback for session reconnection.
        
        Args:
            callback: Async callable(session: SessionData)
        """
        self._on_reconnect = callback


# ============================================================================
# Session Manager (Main Class)
# ============================================================================

class SessionManager:
    """Main session manager that coordinates connection pool and session lifecycle.
    
    This is the primary interface for managing WebSocket sessions.
    
    Example:
        manager = SessionManager(redis_config)
        await manager.initialize()
        
        session = await manager.create_session(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        await manager.subscribe("sess_123", "conv_abc")
    """
    
    def __init__(
        self,
        redis_config: Optional[RedisConfig] = None,
        max_connections: int = 100,
        ping_interval: int = 30,
        ping_timeout: int = 10,
        max_retry: int = 3,
    ):
        self._redis_config = redis_config or RedisConfig()
        self._pool = ConnectionPool(
            redis_config=self._redis_config,
            max_connections=max_connections,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
            max_retry=max_retry,
        )
    
    async def initialize(self) -> None:
        """Initialize the session manager."""
        await self._pool.initialize()
    
    async def close(self) -> None:
        """Close the session manager and cleanup resources."""
        await self._pool.close()
    
    # ------------------------------------------------------------------------
    # Session Operations
    # ------------------------------------------------------------------------
    
    async def create_session(
        self,
        session_id: str,
        user_id: str,
        connection_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionData:
        """Create a new session.
        
        See ConnectionPool.create_session for details.
        """
        return await self._pool.create_session(
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            metadata=metadata,
        )
    
    async def get_session(self, session_id: str) -> Optional[SessionData]:
        """Get session by ID.
        
        See ConnectionPool.get_session for details.
        """
        return await self._pool.get_session(session_id)
    
    async def delete_session(self, session_id: str) -> None:
        """Delete a session.
        
        See ConnectionPool.delete_session for details.
        """
        await self._pool.delete_session(session_id)
    
    async def get_user_sessions(self, user_id: str) -> List[SessionData]:
        """Get all sessions for a user.
        
        See ConnectionPool.get_user_sessions for details.
        """
        return await self._pool.get_user_sessions(user_id)
    
    # ------------------------------------------------------------------------
    # State and Subscriptions
    # ------------------------------------------------------------------------
    
    async def set_state(self, session_id: str, state: SessionState) -> bool:
        """Set session state."""
        return await self._pool.set_state(session_id, state)
    
    async def authenticate(self, session_id: str) -> bool:
        """Mark session as authenticated.
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if authenticated, False if session not found
        """
        return await self.set_state(session_id, SessionState.AUTHENTICATED)
    
    async def subscribe(
        self,
        session_id: str,
        conversation_id: str,
    ) -> bool:
        """Subscribe session to a conversation."""
        return await self._pool.subscribe(session_id, conversation_id)
    
    async def unsubscribe(
        self,
        session_id: str,
        conversation_id: str,
    ) -> bool:
        """Unsubscribe session from a conversation."""
        return await self._pool.unsubscribe(session_id, conversation_id)
    
    # ------------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------------
    
    async def start_heartbeat(self, session_id: str) -> None:
        """Start heartbeat monitoring for a session."""
        await self._pool.start_heartbeat(session_id)
    
    async def stop_heartbeat(self, session_id: str) -> None:
        """Stop heartbeat monitoring for a session."""
        await self._pool.stop_heartbeat(session_id)
    
    async def receive_pong(self, session_id: str) -> None:
        """Handle pong response from client."""
        await self._pool.receive_pong(session_id)
    
    # ------------------------------------------------------------------------
    # Reconnection
    # ------------------------------------------------------------------------
    
    async def reconnect(
        self,
        session_id: str,
        new_connection_id: str,
    ) -> Optional[SessionData]:
        """Attempt to reconnect an existing session."""
        return await self._pool.reconnect_session(session_id, new_connection_id)
    
    # ------------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------------
    
    def on_connect(self, callback: Callable) -> None:
        """Set callback for session connection."""
        self._pool.set_on_connect(callback)
    
    def on_disconnect(self, callback: Callable) -> None:
        """Set callback for session disconnection."""
        self._pool.set_on_disconnect(callback)
    
    def on_reconnect(self, callback: Callable) -> None:
        """Set callback for session reconnection."""
        self._pool.set_on_reconnect(callback)
