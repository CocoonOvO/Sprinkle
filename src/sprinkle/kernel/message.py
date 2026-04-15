"""Message Router - Stream buffer, message queue, and dispatch."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
)

import redis.asyncio as redis

from sprinkle.config import RedisConfig

logger = logging.getLogger(__name__)


# ============================================================================
# Message Types
# ============================================================================

class MessageType(Enum):
    """WebSocket message types."""
    MESSAGE = "message"
    MESSAGE_START = "message.start"
    MESSAGE_CHUNK = "message.chunk"
    MESSAGE_END = "message.end"
    MESSAGE_CANCEL = "message.cancel"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    PONG = "pong"
    ACK = "ack"
    ERROR = "error"


class ContentType(Enum):
    """Message content types."""
    TEXT = "text"
    MARKDOWN = "markdown"
    IMAGE = "image"
    FILE = "file"
    SYSTEM = "system"


# ============================================================================
# Message Data
# ============================================================================

@dataclass
class Message:
    """Complete message structure."""
    id: str
    conversation_id: str
    sender_id: str
    content: str
    content_type: ContentType = ContentType.TEXT
    metadata: Dict[str, Any] = field(default_factory=dict)
    mentions: List[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class StreamMessage:
    """Streaming message state (for fragmented messages)."""
    id: str
    conversation_id: str
    sender_id: str
    content_type: ContentType = ContentType.TEXT
    content_buffer: str = ""
    offset: int = 0
    mentions: List[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    chunks_received: int = 0
    started_at: float = field(default_factory=time.time)
    last_chunk_at: float = field(default_factory=time.time)
    is_complete: bool = False
    is_cancelled: bool = False


# ============================================================================
# Stream Buffer
# ============================================================================

class StreamBuffer:
    """Stream buffer for handling fragmented messages.
    
    Manages chunked message assembly with:
    - chunk_size: 64KB maximum per chunk
    - max_buffer: 10MB maximum total message size
    - timeout: 5 seconds from first chunk to complete
    
    Complete message is determined by:
    - EOS marker (message.end with is_complete=True)
    - Timeout (5s without complete message)
    """
    
    CHUNK_SIZE: int = 64 * 1024  # 64KB
    MAX_BUFFER: int = 10 * 1024 * 1024  # 10MB
    TIMEOUT: float = 5.0  # 5 seconds
    
    def __init__(self) -> None:
        # Active stream buffers: message_id -> StreamMessage
        self._buffers: Dict[str, StreamMessage] = {}
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
        
        # Timeout tasks: message_id -> asyncio.Task
        self._timeout_tasks: Dict[str, asyncio.Task] = {}
        
        # Callbacks
        self._on_complete: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None
    
    async def start_stream(
        self,
        message_id: str,
        conversation_id: str,
        sender_id: str,
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> StreamMessage:
        """Start a new streaming message.
        
        Args:
            message_id: Unique message identifier
            conversation_id: Target conversation
            sender_id: Sender user/agent ID
            content_type: Content type (text, markdown, etc.)
            mentions: Optional list of mentioned user IDs
            reply_to: Optional message ID being replied to
            
        Returns:
            StreamMessage state object
        """
        async with self._lock:
            # Cancel any existing stream with same ID
            if message_id in self._buffers:
                await self._cancel_stream(message_id)
            
            stream = StreamMessage(
                id=message_id,
                conversation_id=conversation_id,
                sender_id=sender_id,
                content_type=ContentType(content_type),
                mentions=mentions or [],
                reply_to=reply_to,
            )
            self._buffers[message_id] = stream
            
            # Start timeout task
            self._timeout_tasks[message_id] = asyncio.create_task(
                self._timeout_handler(message_id)
            )
            
            logger.debug(f"Stream started: {message_id}")
            return stream
    
    async def add_chunk(
        self,
        message_id: str,
        content: str,
        offset: int,
    ) -> StreamMessage:
        """Add a chunk to an existing stream.
        
        Args:
            message_id: Message identifier
            content: Text chunk to add
            offset: Character offset in complete message
            
        Returns:
            Updated StreamMessage
            
        Raises:
            ValueError: If stream not found or offset mismatch
        """
        async with self._lock:
            stream = self._buffers.get(message_id)
            if not stream:
                raise ValueError(f"Stream not found: {message_id}")
            
            if stream.is_complete or stream.is_cancelled:
                raise ValueError(f"Stream already finished: {message_id}")
            
            # Validate offset
            if offset != stream.offset:
                raise ValueError(
                    f"Offset mismatch: expected {stream.offset}, got {offset}"
                )
            
            # Check size limit
            new_size = len(content) + len(stream.content_buffer)
            if new_size > self.MAX_BUFFER:
                logger.error(
                    f"Stream {message_id} exceeds max buffer size "
                    f"({new_size} > {self.MAX_BUFFER})"
                )
                await self._error_stream(message_id, "Message exceeds maximum size")
                raise ValueError("Message exceeds maximum size")
            
            # Add chunk
            stream.content_buffer += content
            stream.offset += len(content)
            stream.chunks_received += 1
            stream.last_chunk_at = time.time()
            
            logger.debug(
                f"Chunk added to stream {message_id}: "
                f"+{len(content)} bytes (total: {stream.offset})"
            )
            return stream
    
    async def end_stream(
        self,
        message_id: str,
        is_complete: bool = True,
    ) -> Optional[StreamMessage]:
        """End a streaming message.
        
        Args:
            message_id: Message identifier
            is_complete: Whether the message is complete
            
        Returns:
            StreamMessage if complete, None otherwise
        """
        async with self._lock:
            stream = self._buffers.get(message_id)
            if not stream:
                return None
            
            # Cancel timeout task
            task = self._timeout_tasks.pop(message_id, None)
            if task and not task.done():
                task.cancel()
            
            if is_complete:
                stream.is_complete = True
                result = stream
                
                # Remove from buffers
                del self._buffers[message_id]
                
                logger.debug(f"Stream completed: {message_id} ({stream.offset} bytes)")
                
                # Trigger callback
                if self._on_complete:
                    message = self._to_message(stream)
                    await self._on_complete(message)
                
                return stream
            else:
                return stream
    
    async def cancel_stream(self, message_id: str) -> None:
        """Cancel a streaming message.
        
        Args:
            message_id: Message identifier
        """
        await self._cancel_stream(message_id)
    
    async def _cancel_stream(self, message_id: str) -> None:
        """Internal cancel implementation."""
        async with self._lock:
            stream = self._buffers.pop(message_id, None)
            if not stream:
                return
            
            stream.is_cancelled = True
            
            # Cancel timeout task
            task = self._timeout_tasks.pop(message_id, None)
            if task and not task.done():
                task.cancel()
            
            logger.debug(f"Stream cancelled: {message_id}")
            
            if self._on_cancel:
                await self._on_cancel(stream)
    
    async def _error_stream(self, message_id: str, error: str) -> None:
        """Handle stream error."""
        async with self._lock:
            stream = self._buffers.pop(message_id, None)
            if not stream:
                return
            
            # Cancel timeout task
            task = self._timeout_tasks.pop(message_id, None)
            if task and not task.done():
                task.cancel()
            
            logger.error(f"Stream error {message_id}: {error}")
            
            if self._on_error:
                await self._on_error(stream, error)
    
    async def _timeout_handler(self, message_id: str) -> None:
        """Handle stream timeout (5s from first chunk).
        
        Args:
            message_id: Message identifier
        """
        try:
            await asyncio.sleep(self.TIMEOUT)
            
            async with self._lock:
                stream = self._buffers.get(message_id)
                if stream and not stream.is_complete and not stream.is_cancelled:
                    # Timeout - cancel the stream
                    logger.warning(
                        f"Stream timeout {message_id} "
                        f"(buffered: {stream.offset} bytes)"
                    )
                    self._buffers.pop(message_id, None)
                    self._timeout_tasks.pop(message_id, None)
                    
                    if self._on_error:
                        await self._on_error(stream, "Stream timeout")
        except asyncio.CancelledError:
            pass
    
    def _to_message(self, stream: StreamMessage) -> Message:
        """Convert StreamMessage to Message.
        
        Args:
            stream: StreamMessage to convert
            
        Returns:
            Complete Message
        """
        return Message(
            id=stream.id,
            conversation_id=stream.conversation_id,
            sender_id=stream.sender_id,
            content=stream.content_buffer,
            content_type=stream.content_type,
            mentions=stream.mentions,
            reply_to=stream.reply_to,
            created_at=stream.started_at,
        )
    
    # ------------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------------
    
    def set_on_complete(self, callback: Callable[[Message], Awaitable[Any]]) -> None:
        """Set callback for complete messages.
        
        Args:
            callback: Async callable(message: Message)
        """
        self._on_complete = callback
    
    def set_on_error(
        self,
        callback: Callable[[StreamMessage, str], Awaitable[Any]],
    ) -> None:
        """Set callback for stream errors.
        
        Args:
            callback: Async callable(stream: StreamMessage, error: str)
        """
        self._on_error = callback
    
    def set_on_cancel(self, callback: Callable[[StreamMessage], Awaitable[Any]]) -> None:
        """Set callback for cancelled streams.
        
        Args:
            callback: Async callable(stream: StreamMessage)
        """
        self._on_cancel = callback
    
    # ------------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------------
    
    def get_active_count(self) -> int:
        """Get number of active streams.
        
        Returns:
            Number of streams currently buffering
        """
        return len(self._buffers)
    
    def get_buffer_size(self, message_id: str) -> Optional[int]:
        """Get current buffer size for a message.
        
        Args:
            message_id: Message identifier
            
        Returns:
            Buffer size in bytes, or None if not found
        """
        stream = self._buffers.get(message_id)
        return len(stream.content_buffer) if stream else None


# ============================================================================
# Message Queue (Redis-backed)
# ============================================================================

class MessageQueue:
    """Redis-backed message queue for reliable delivery.
    
    Features:
    - Per-conversation message queues
    - Offline message queuing
    - Priority support
    - Message persistence
    """
    
    QUEUE_PREFIX = "queue:"
    OFFLINE_PREFIX = "offline:"
    
    def __init__(self, redis_config: Optional[RedisConfig] = None):
        self._redis_config = redis_config or RedisConfig()
        self._redis_client: Optional[redis.Redis] = None
        self._pool: Optional[redis.ConnectionPool] = None
    
    async def initialize(self) -> None:
        """Initialize Redis connection."""
        self._pool = redis.ConnectionPool.from_url(
            self._redis_config.url,
            max_connections=50,
            decode_responses=True,
        )
        self._redis_client = redis.Redis(connection_pool=self._pool)
        logger.info("MessageQueue initialized")
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis_client:
            await self._redis_client.close()
        if self._pool:
            await self._pool.disconnect()
    
    async def enqueue(
        self,
        conversation_id: str,
        message: Message,
        priority: int = 0,
    ) -> bool:
        """Add a message to the conversation queue.
        
        Args:
            conversation_id: Target conversation
            message: Message to enqueue
            priority: Message priority (higher = first)
            
        Returns:
            True if enqueued successfully
        """
        if not self._redis_client:
            return False
        
        queue_key = f"{self.QUEUE_PREFIX}{conversation_id}"
        
        # Serialize message
        import json
        message_data = {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "content": message.content,
            "content_type": message.content_type.value,
            "metadata": message.metadata,
            "mentions": message.mentions,
            "reply_to": message.reply_to,
            "created_at": message.created_at,
            "priority": priority,
        }
        
        # Add to sorted set with score = -priority (for desc order) + timestamp
        score = -priority * 1e10 + message.created_at
        await self._redis_client.zadd(queue_key, {json.dumps(message_data): score})
        
        # Set TTL (24 hours for queue)
        await self._redis_client.expire(queue_key, 86400)
        
        logger.debug(f"Enqueued message {message.id} to {conversation_id}")
        return True
    
    async def dequeue(
        self,
        conversation_id: str,
        count: int = 1,
    ) -> List[Message]:
        """Remove and return messages from the conversation queue.
        
        Args:
            conversation_id: Target conversation
            count: Maximum number of messages to dequeue
            
        Returns:
            List of dequeued Messages
        """
        if not self._redis_client:
            return []
        
        queue_key = f"{self.QUEUE_PREFIX}{conversation_id}"
        
        # Get highest priority messages (score is negative for desc order)
        results = await self._redis_client.zpopmax(queue_key, count)
        
        messages = []
        import json
        for item, _ in results:
            try:
                data = json.loads(item)
                messages.append(Message(
                    id=data["id"],
                    conversation_id=data["conversation_id"],
                    sender_id=data["sender_id"],
                    content=data["content"],
                    content_type=ContentType(data.get("content_type", "text")),
                    metadata=data.get("metadata", {}),
                    mentions=data.get("mentions", []),
                    reply_to=data.get("reply_to"),
                    created_at=data.get("created_at", time.time()),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse message: {e}")
        
        return messages
    
    async def peek(
        self,
        conversation_id: str,
        count: int = 10,
    ) -> List[Message]:
        """Peek at messages without removing them.
        
        Args:
            conversation_id: Target conversation
            count: Maximum number of messages to peek
            
        Returns:
            List of Messages (oldest first)
        """
        if not self._redis_client:
            return []
        
        queue_key = f"{self.QUEUE_PREFIX}{conversation_id}"
        
        # Get oldest messages
        results = await self._redis_client.zrange(queue_key, 0, count - 1)
        
        messages = []
        import json
        for item in results:
            try:
                data = json.loads(item)
                messages.append(Message(
                    id=data["id"],
                    conversation_id=data["conversation_id"],
                    sender_id=data["sender_id"],
                    content=data["content"],
                    content_type=ContentType(data.get("content_type", "text")),
                    metadata=data.get("metadata", {}),
                    mentions=data.get("mentions", []),
                    reply_to=data.get("reply_to"),
                    created_at=data.get("created_at", time.time()),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse message: {e}")
        
        return messages
    
    async def queue_size(self, conversation_id: str) -> int:
        """Get number of messages in queue.
        
        Args:
            conversation_id: Target conversation
            
        Returns:
            Queue size
        """
        if not self._redis_client:
            return 0
        
        queue_key = f"{self.QUEUE_PREFIX}{conversation_id}"
        return await self._redis_client.zcard(queue_key)
    
    async def enqueue_offline(
        self,
        user_id: str,
        message: Message,
    ) -> bool:
        """Add a message to a user's offline queue.
        
        Args:
            user_id: Target user
            message: Message to enqueue
            
        Returns:
            True if enqueued successfully
        """
        if not self._redis_client:
            return False
        
        queue_key = f"{self.OFFLINE_PREFIX}{user_id}"
        
        import json
        message_data = {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "content": message.content,
            "content_type": message.content_type.value,
            "metadata": message.metadata,
            "mentions": message.mentions,
            "reply_to": message.reply_to,
            "created_at": message.created_at,
        }
        
        # Add to list (FIFO)
        await self._redis_client.rpush(queue_key, json.dumps(message_data))
        
        # Set TTL (30 days for offline messages)
        await self._redis_client.expire(queue_key, 2592000)
        
        logger.debug(f"Enqueued offline message {message.id} for user {user_id}")
        return True
    
    async def get_offline_messages(
        self,
        user_id: str,
        count: int = 50,
    ) -> List[Message]:
        """Get and remove offline messages for a user.
        
        Args:
            user_id: Target user
            count: Maximum number of messages to retrieve
            
        Returns:
            List of Messages
        """
        if not self._redis_client:
            return []
        
        queue_key = f"{self.OFFLINE_PREFIX}{user_id}"
        
        # Pop multiple items from the left (oldest first)
        items = await self._redis_client.lpop(queue_key, count)
        if not items:
            return []
        
        if isinstance(items, str):
            items = [items]
        
        messages = []
        import json
        for item in items:
            try:
                data = json.loads(item)
                messages.append(Message(
                    id=data["id"],
                    conversation_id=data["conversation_id"],
                    sender_id=data["sender_id"],
                    content=data["content"],
                    content_type=ContentType(data.get("content_type", "text")),
                    metadata=data.get("metadata", {}),
                    mentions=data.get("mentions", []),
                    reply_to=data.get("reply_to"),
                    created_at=data.get("created_at", time.time()),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse offline message: {e}")
        
        return messages


# ============================================================================
# Message Dispatcher
# ============================================================================

class MessageDispatcher:
    """Routes messages to appropriate handlers.
    
    Manages:
    - Subscription-based routing
    - Per-conversation handlers
    - Broadcast to multiple subscribers
    """
    
    def __init__(self) -> None:
        # Conversation subscribers: conversation_id -> set of handler info
        self._subscribers: Dict[str, Dict[str, Callable]] = defaultdict(dict)
        
        # Global handlers (receive all messages)
        self._global_handlers: Dict[str, Callable] = {}
        
        # Lock
        self._lock = asyncio.Lock()
    
    async def subscribe(
        self,
        conversation_id: str,
        subscriber_id: str,
        handler: Callable[[Message], Awaitable[Any]],
    ) -> None:
        """Subscribe a handler to a conversation.
        
        Args:
            conversation_id: Conversation to subscribe to
            subscriber_id: Unique subscriber identifier
            handler: Async callable to receive messages
        """
        async with self._lock:
            self._subscribers[conversation_id][subscriber_id] = handler
            logger.debug(
                f"Subscribed {subscriber_id} to conversation {conversation_id}"
            )
    
    async def unsubscribe(
        self,
        conversation_id: str,
        subscriber_id: str,
    ) -> None:
        """Unsubscribe a handler from a conversation.
        
        Args:
            conversation_id: Conversation to unsubscribe from
            subscriber_id: Subscriber identifier
        """
        async with self._lock:
            self._subscribers[conversation_id].pop(subscriber_id, None)
            if not self._subscribers[conversation_id]:
                del self._subscribers[conversation_id]
            logger.debug(
                f"Unsubscribed {subscriber_id} from conversation {conversation_id}"
            )
    
    def subscribe_global(
        self,
        handler_id: str,
        handler: Callable[[Message], Awaitable[Any]],
    ) -> None:
        """Subscribe a global handler (receives all messages).
        
        Args:
            handler_id: Unique handler identifier
            handler: Async callable to receive messages
        """
        self._global_handlers[handler_id] = handler
        logger.debug(f"Subscribed global handler: {handler_id}")
    
    def unsubscribe_global(self, handler_id: str) -> None:
        """Unsubscribe a global handler.
        
        Args:
            handler_id: Handler identifier
        """
        self._global_handlers.pop(handler_id, None)
        logger.debug(f"Unsubscribed global handler: {handler_id}")
    
    async def dispatch(self, message: Message) -> List[Any]:
        """Dispatch a message to all subscribers.
        
        Args:
            message: Message to dispatch
            
        Returns:
            List of handler results
        """
        results = []
        
        # Dispatch to conversation subscribers
        async with self._lock:
            subscribers = self._subscribers.get(message.conversation_id, {})
        
        for subscriber_id, handler in subscribers.items():
            try:
                result = await handler(message)
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Handler error for subscriber {subscriber_id}: {e}",
                    exc_info=True
                )
        
        # Dispatch to global handlers
        for handler_id, handler in self._global_handlers.items():
            try:
                result = await handler(message)
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Global handler error {handler_id}: {e}",
                    exc_info=True
                )
        
        return results
    
    def get_subscriber_count(self, conversation_id: str) -> int:
        """Get number of subscribers for a conversation.
        
        Args:
            conversation_id: Conversation identifier
            
        Returns:
            Subscriber count
        """
        return len(self._subscribers.get(conversation_id, {}))


# ============================================================================
# Message Router (Main Class)
# ============================================================================

class MessageRouter:
    """Main message router coordinating buffer, queue, and dispatcher.
    
    This is the primary interface for handling messages.
    
    Example:
        router = MessageRouter(redis_config)
        await router.initialize()
        
        # Handle incoming WebSocket message
        await router.handle_ws_message(message_data)
        
        # Handle complete message
        async def handle(msg):
            print(f"Got message: {msg.content}")
        router.set_on_message(handler)
    """
    
    def __init__(
        self,
        redis_config: Optional[RedisConfig] = None,
        chunk_size: int = StreamBuffer.CHUNK_SIZE,
        max_buffer: int = StreamBuffer.MAX_BUFFER,
        buffer_timeout: float = StreamBuffer.TIMEOUT,
    ):
        self._redis_config = redis_config
        self._stream_buffer = StreamBuffer()
        self._message_queue = MessageQueue(redis_config)
        self._dispatcher = MessageDispatcher()
        
        # Store config for reference
        self._chunk_size = chunk_size
        self._max_buffer = max_buffer
        self._buffer_timeout = buffer_timeout
        
        # Callbacks
        self._on_message: Optional[Callable[[Message], Awaitable[Any]]] = None
    
    async def initialize(self) -> None:
        """Initialize the message router."""
        await self._message_queue.initialize()
        logger.info("MessageRouter initialized")
    
    async def close(self) -> None:
        """Close the message router and cleanup resources."""
        await self._message_queue.close()
    
    # ------------------------------------------------------------------------
    # Stream Message Handling
    # ------------------------------------------------------------------------
    
    async def handle_ws_message(self, data: Dict[str, Any]) -> None:
        """Handle an incoming WebSocket message.
        
        Args:
            data: Parsed WebSocket message data
            
        Raises:
            ValueError: If message format is invalid
        """
        msg_type = data.get("type")
        
        if msg_type == MessageType.MESSAGE_START.value:
            await self._handle_message_start(data)
        elif msg_type == MessageType.MESSAGE_CHUNK.value:
            await self._handle_message_chunk(data)
        elif msg_type == MessageType.MESSAGE_END.value:
            await self._handle_message_end(data)
        elif msg_type == MessageType.MESSAGE_CANCEL.value:
            await self._handle_message_cancel(data)
        elif msg_type == MessageType.MESSAGE.value:
            await self._handle_simple_message(data)
        else:
            logger.warning(f"Unknown message type: {msg_type}")
    
    async def _handle_message_start(self, data: Dict[str, Any]) -> None:
        """Handle message.start event."""
        message_id = data.get("id")
        params = data.get("params", {})
        
        if not message_id:
            raise ValueError("Missing message id")
        
        await self._stream_buffer.start_stream(
            message_id=message_id,
            conversation_id=params.get("conversation_id", ""),
            sender_id=params.get("sender_id", ""),
            content_type=params.get("content_type", "text"),
            mentions=params.get("mentions"),
            reply_to=params.get("reply_to"),
        )
    
    async def _handle_message_chunk(self, data: Dict[str, Any]) -> None:
        """Handle message.chunk event."""
        message_id = data.get("id")
        params = data.get("params", {})
        
        if not message_id:
            raise ValueError("Missing message id")
        
        content = params.get("content", "")
        offset = params.get("offset", 0)
        
        # Validate chunk size
        if len(content) > self._chunk_size:
            await self._stream_buffer.cancel_stream(message_id)
            raise ValueError(f"Chunk exceeds maximum size ({self._chunk_size})")
        
        await self._stream_buffer.add_chunk(message_id, content, offset)
    
    async def _handle_message_end(self, data: Dict[str, Any]) -> None:
        """Handle message.end event."""
        message_id = data.get("id")
        params = data.get("params", {})
        
        if not message_id:
            raise ValueError("Missing message id")
        
        is_complete = params.get("is_complete", True)
        await self._stream_buffer.end_stream(message_id, is_complete)
    
    async def _handle_message_cancel(self, data: Dict[str, Any]) -> None:
        """Handle message.cancel event."""
        message_id = data.get("id")
        if message_id:
            await self._stream_buffer.cancel_stream(message_id)
    
    async def _handle_simple_message(self, data: Dict[str, Any]) -> None:
        """Handle simple (non-streaming) message."""
        params = data.get("params", {})
        
        message = Message(
            id=data.get("id", str(uuid.uuid4())),
            conversation_id=params.get("conversation_id", ""),
            sender_id=params.get("sender_id", ""),
            content=params.get("content", ""),
            content_type=ContentType(params.get("content_type", "text")),
            mentions=params.get("mentions", []),
            reply_to=params.get("reply_to"),
        )
        
        await self._deliver_message(message)
    
    # ------------------------------------------------------------------------
    # Message Delivery
    # ------------------------------------------------------------------------
    
    async def _deliver_message(self, message: Message) -> None:
        """Deliver a complete message to subscribers.
        
        Args:
            message: Complete message to deliver
        """
        # Route through dispatcher
        results = await self._dispatcher.dispatch(message)
        
        # Also trigger callback
        if self._on_message:
            await self._on_message(message)
        
        logger.debug(
            f"Message {message.id} delivered to conversation "
            f"{message.conversation_id} ({len(results)} subscribers)"
        )
    
    # ------------------------------------------------------------------------
    # Queue Operations
    # ------------------------------------------------------------------------
    
    async def enqueue_message(
        self,
        conversation_id: str,
        message: Message,
        priority: int = 0,
    ) -> bool:
        """Add a message to the queue.
        
        Args:
            conversation_id: Target conversation
            message: Message to enqueue
            priority: Message priority
            
        Returns:
            True if enqueued successfully
        """
        return await self._message_queue.enqueue(conversation_id, message, priority)
    
    async def flush_queue(
        self,
        conversation_id: str,
    ) -> List[Message]:
        """Flush all queued messages for a conversation.
        
        Args:
            conversation_id: Target conversation
            
        Returns:
            List of flushed messages
        """
        messages = await self._message_queue.dequeue(conversation_id)
        for msg in messages:
            await self._deliver_message(msg)
        return messages
    
    async def queue_size(self, conversation_id: str) -> int:
        """Get queue size for a conversation.
        
        Args:
            conversation_id: Target conversation
            
        Returns:
            Queue size
        """
        return await self._message_queue.queue_size(conversation_id)
    
    # ------------------------------------------------------------------------
    # Subscription Management
    # ------------------------------------------------------------------------
    
    async def subscribe(
        self,
        conversation_id: str,
        subscriber_id: str,
        handler: Callable[[Message], Awaitable[Any]],
    ) -> None:
        """Subscribe to a conversation.
        
        Args:
            conversation_id: Conversation to subscribe to
            subscriber_id: Unique subscriber identifier
            handler: Async callable to receive messages
        """
        await self._dispatcher.subscribe(conversation_id, subscriber_id, handler)
    
    async def unsubscribe(
        self,
        conversation_id: str,
        subscriber_id: str,
    ) -> None:
        """Unsubscribe from a conversation.
        
        Args:
            conversation_id: Conversation to unsubscribe from
            subscriber_id: Subscriber identifier
        """
        await self._dispatcher.unsubscribe(conversation_id, subscriber_id)
    
    # ------------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------------
    
    def set_on_message(
        self,
        callback: Callable[[Message], Awaitable[Any]],
    ) -> None:
        """Set callback for incoming messages.
        
        Args:
            callback: Async callable(message: Message)
        """
        self._on_message = callback
        self._stream_buffer.set_on_complete(self._deliver_message)
    
    # ------------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------------
    
    def get_active_streams(self) -> int:
        """Get number of active streams.
        
        Returns:
            Active stream count
        """
        return self._stream_buffer.get_active_count()
    
    def get_subscriber_count(self, conversation_id: str) -> int:
        """Get subscriber count for a conversation.
        
        Args:
            conversation_id: Conversation identifier
            
        Returns:
            Subscriber count
        """
        return self._dispatcher.get_subscriber_count(conversation_id)
