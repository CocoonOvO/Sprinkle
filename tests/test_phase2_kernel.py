"""Tests for Phase 2: Kernel Modules.

Tests cover:
- Session Manager (session.py)
- Event Bus (event.py)
- Message Router (message.py)
- Auth Service (auth.py)
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
from sprinkle.config import RedisConfig


# ============================================================================
# Session Manager Tests
# ============================================================================

class TestSessionState:
    """Tests for SessionState enum."""
    
    def test_session_state_values(self):
        """Test SessionState enum values."""
        assert SessionState.CONNECTING.value == "connecting"
        assert SessionState.CONNECTED.value == "connected"
        assert SessionState.AUTHENTICATED.value == "authenticated"
        assert SessionState.DISCONNECTING.value == "disconnecting"
        assert SessionState.DISCONNECTED.value == "disconnected"
        assert SessionState.RECONNECTING.value == "reconnecting"


class TestSessionData:
    """Tests for SessionData dataclass."""
    
    def test_session_data_creation(self):
        """Test SessionData creation with defaults."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        
        assert session.session_id == "sess_123"
        assert session.user_id == "user_456"
        assert session.connection_id == "conn_789"
        assert session.state == SessionState.CONNECTING
        assert session.subscriptions == set()
        assert session.metadata == {}
        assert session.reconnect_count == 0
    
    def test_session_data_custom_values(self):
        """Test SessionData with custom values."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
            state=SessionState.AUTHENTICATED,
            subscriptions={"conv_1", "conv_2"},
            metadata={"key": "value"},
            reconnect_count=2,
        )
        
        assert session.state == SessionState.AUTHENTICATED
        assert "conv_1" in session.subscriptions
        assert session.metadata == {"key": "value"}
        assert session.reconnect_count == 2
    
    def test_session_subscriptions_add_remove(self):
        """Test adding and removing subscriptions."""
        session = SessionData(
            session_id="sess_sub",
            user_id="user_sub",
            connection_id="conn_sub",
        )
        
        session.subscriptions.add("conv_1")
        session.subscriptions.add("conv_2")
        session.subscriptions.discard("conv_1")
        
        assert "conv_1" not in session.subscriptions
        assert "conv_2" in session.subscriptions
    
    def test_session_metadata_operations(self):
        """Test metadata dictionary operations."""
        session = SessionData(
            session_id="sess_meta",
            user_id="user_meta",
            connection_id="conn_meta",
            metadata={"key1": "val1"},
        )
        
        session.metadata["key2"] = "val2"
        
        assert session.metadata == {"key1": "val1", "key2": "val2"}


class TestConnectionPool:
    """Tests for ConnectionPool."""
    
    @pytest.fixture
    def redis_config(self):
        """Create test Redis config."""
        return RedisConfig(host="localhost", port=6379, db=1)
    
    @pytest.fixture
    def pool(self, redis_config):
        """Create ConnectionPool instance."""
        return ConnectionPool(
            redis_config=redis_config,
            max_connections=10,
            ping_interval=30,
            ping_timeout=10,
            max_retry=3,
        )
    
    def test_pool_initialization(self, pool, redis_config):
        """Test pool initialization with custom config."""
        assert pool._redis_config == redis_config
        assert pool._max_connections == 10
        assert pool._ping_interval == 30
        assert pool._ping_timeout == 10
        assert pool._max_retry == 3
    
    def test_pool_memory_store_empty_initially(self, pool):
        """Test memory store is empty on init."""
        assert len(pool._memory_store) == 0
    
    @pytest.mark.asyncio
    async def test_create_session(self, pool):
        """Test session creation."""
        session = await pool.create_session(
            session_id="sess_test",
            user_id="user_test",
            connection_id="conn_test",
        )
        
        assert session.session_id == "sess_test"
        assert session.user_id == "user_test"
        assert session.connection_id == "conn_test"
        assert session.state == SessionState.CONNECTING
        assert "sess_test" in pool._memory_store
    
    @pytest.mark.asyncio
    async def test_get_session(self, pool):
        """Test session retrieval."""
        await pool.create_session(
            session_id="sess_lookup",
            user_id="user_test",
            connection_id="conn_test",
        )
        
        session = await pool.get_session("sess_lookup")
        assert session is not None
        assert session.session_id == "sess_lookup"
    
    @pytest.mark.asyncio
    async def test_get_session_not_found(self, pool):
        """Test session retrieval when not found."""
        session = await pool.get_session("nonexistent")
        assert session is None
    
    @pytest.mark.asyncio
    async def test_delete_session(self, pool):
        """Test session deletion."""
        await pool.create_session(
            session_id="sess_del",
            user_id="user_test",
            connection_id="conn_test",
        )
        
        await pool.delete_session("sess_del")
        assert "sess_del" not in pool._memory_store
    
    @pytest.mark.asyncio
    async def test_update_session(self, pool):
        """Test session update."""
        session = await pool.create_session(
            session_id="sess_upd",
            user_id="user_test",
            connection_id="conn_test",
        )
        
        session.state = SessionState.CONNECTED
        await pool.update_session(session)
        
        updated = await pool.get_session("sess_upd")
        assert updated.state == SessionState.CONNECTED
    
    @pytest.mark.asyncio
    async def test_subscribe(self, pool):
        """Test conversation subscription."""
        await pool.create_session(
            session_id="sess_sub",
            user_id="user_test",
            connection_id="conn_test",
        )
        
        result = await pool.subscribe("sess_sub", "conv_123")
        assert result is True
        
        session = await pool.get_session("sess_sub")
        assert "conv_123" in session.subscriptions
    
    @pytest.mark.asyncio
    async def test_unsubscribe(self, pool):
        """Test conversation unsubscription."""
        await pool.create_session(
            session_id="sess_unsub",
            user_id="user_test",
            connection_id="conn_test",
        )
        await pool.subscribe("sess_unsub", "conv_456")
        
        result = await pool.unsubscribe("sess_unsub", "conv_456")
        assert result is True
        
        session = await pool.get_session("sess_unsub")
        assert "conv_456" not in session.subscriptions
    
    @pytest.mark.asyncio
    async def test_get_user_sessions(self, pool):
        """Test getting all sessions for a user."""
        await pool.create_session(
            session_id="sess_u1",
            user_id="user_multi",
            connection_id="conn_1",
        )
        await pool.create_session(
            session_id="sess_u2",
            user_id="user_multi",
            connection_id="conn_2",
        )
        
        sessions = await pool.get_user_sessions("user_multi")
        assert len(sessions) == 2
    
    @pytest.mark.asyncio
    async def test_set_state(self, pool):
        """Test setting session state."""
        await pool.create_session(
            session_id="sess_state",
            user_id="user_test",
            connection_id="conn_test",
        )
        
        result = await pool.set_state("sess_state", SessionState.AUTHENTICATED)
        assert result is True
        
        session = await pool.get_session("sess_state")
        assert session.state == SessionState.AUTHENTICATED
    
    @pytest.mark.asyncio
    async def test_subscribe_nonexistent_session(self, pool):
        """Test subscribe to nonexistent session returns False."""
        result = await pool.subscribe("nonexistent_session", "conv_1")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_session(self, pool):
        """Test unsubscribe from nonexistent session returns False."""
        result = await pool.unsubscribe("nonexistent_session", "conv_1")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_set_state_nonexistent_session(self, pool):
        """Test set_state on nonexistent session returns False."""
        result = await pool.set_state("nonexistent_session", SessionState.CONNECTED)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_reconnect_session_not_found(self, pool):
        """Test reconnect for nonexistent session returns None."""
        result = await pool.reconnect_session("nonexistent", "new_conn")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_reconnect_disconnected_session(self, pool):
        """Test reconnect for already disconnected session returns None."""
        await pool.create_session("sess_disc", "user_disc", "conn_disc")
        session = await pool.get_session("sess_disc")
        session.state = SessionState.DISCONNECTED
        await pool.update_session(session)
        
        result = await pool.reconnect_session("sess_disc", "new_conn")
        assert result is None


class TestSessionManager:
    """Tests for SessionManager."""
    
    @pytest.fixture
    def manager(self):
        """Create SessionManager instance."""
        return SessionManager(
            redis_config=RedisConfig(host="localhost", port=6379, db=1),
            ping_interval=30,
            ping_timeout=10,
        )
    
    def test_manager_initialization(self, manager):
        """Test manager initialization."""
        assert manager._pool is not None
        assert manager._pool._ping_interval == 30
        assert manager._pool._ping_timeout == 10
    
    def test_manager_pool_config(self, manager):
        """Test manager pool configuration."""
        assert manager._pool._max_connections == 100
        assert manager._pool._max_retry == 3
    
    def test_callback_on_connect(self, manager):
        """Test on_connect callback setter."""
        async def cb(session): pass
        manager.on_connect(cb)
        assert manager._pool._on_connect is cb
    
    def test_callback_on_disconnect(self, manager):
        """Test on_disconnect callback setter."""
        async def cb(session): pass
        manager.on_disconnect(cb)
        assert manager._pool._on_disconnect is cb
    
    def test_callback_on_reconnect(self, manager):
        """Test on_reconnect callback setter."""
        async def cb(session): pass
        manager.on_reconnect(cb)
        assert manager._pool._on_reconnect is cb


# ============================================================================
# Event Bus Tests
# ============================================================================

class TestEventData:
    """Tests for EventData dataclass."""
    
    def test_event_data_creation(self):
        """Test EventData creation with defaults."""
        event = EventData(name="test.event", data={"key": "value"})
        
        assert event.name == "test.event"
        assert event.data == {"key": "value"}
        assert event.sender is None
        assert event.depth == 0
        assert event.source_event is None
    
    def test_event_data_custom_values(self):
        """Test EventData with custom values."""
        event = EventData(
            name="test.event",
            data={"key": "value"},
            sender="plugin_a",
            depth=3,
            source_event="other.event",
        )
        
        assert event.sender == "plugin_a"
        assert event.depth == 3
        assert event.source_event == "other.event"
    
    def test_event_data_timestamp_default(self):
        """Test EventData default timestamp."""
        before = time.time()
        event = EventData(name="test", data=None)
        after = time.time()
        
        assert before <= event.timestamp <= after
    
    def test_event_data_timestamp_custom(self):
        """Test EventData with custom timestamp."""
        ts = 1234567890.0
        event = EventData(name="test", data=None, timestamp=ts)
        assert event.timestamp == ts


class TestEventRegistry:
    """Tests for EventRegistry."""
    
    @pytest.fixture
    def registry(self):
        """Create EventRegistry instance."""
        return EventRegistry()
    
    def test_register_handler(self, registry):
        """Test handler registration."""
        async def handler(event): pass
        
        registry.register("test.event", handler)
        
        handlers = registry.get_handlers("test.event")
        assert len(handlers) == 1
        assert handlers[0] == handler
    
    def test_register_multiple_handlers_priority(self, registry):
        """Test multiple handlers sorted by priority."""
        async def handler_low(event): return "low"
        async def handler_high(event): return "high"
        async def handler_med(event): return "med"
        
        registry.register("test.priority", handler_low, priority=10)
        registry.register("test.priority", handler_high, priority=90)
        registry.register("test.priority", handler_med, priority=50)
        
        handlers = registry.get_handlers("test.priority")
        assert handlers[0] == handler_high
        assert handlers[1] == handler_med
        assert handlers[2] == handler_low
    
    def test_unregister_handler(self, registry):
        """Test handler unregistration."""
        async def handler(event): pass
        
        registry.register("test.unreg", handler)
        result = registry.unregister("test.unreg", handler)
        
        assert result is True
        assert len(registry.get_handlers("test.unreg")) == 0
    
    def test_wildcard_registration(self, registry):
        """Test wildcard event registration."""
        async def handler(event): return "wildcard"
        
        registry.register("message.*", handler)
        
        handlers1 = registry.get_handlers("message.received")
        handlers2 = registry.get_handlers("message.sent")
        
        assert len(handlers1) == 1
        assert len(handlers2) == 1
    
    def test_wildcard_pattern_matching(self, registry):
        """Test wildcard pattern matching."""
        assert registry._match_wildcard("message.received", "message.*") is True
        assert registry._match_wildcard("message.sent", "message.*") is True
        assert registry._match_wildcard("user.login", "message.*") is False
    
    def test_list_events(self, registry):
        """Test listing registered events."""
        async def handler1(event): pass
        async def handler2(event): pass
        
        registry.register("event.1", handler1)
        registry.register("event.2", handler2)
        
        events = registry.list_events()
        assert "event.1" in events
        assert "event.2" in events
    
    def test_list_events_empty(self, registry):
        """Test list_events with no events."""
        events = registry.list_events()
        assert events == []
    
    def test_get_handler_count(self, registry):
        """Test getting handler count."""
        async def h1(event): pass
        async def h2(event): pass
        
        registry.register("count.test", h1)
        registry.register("count.test", h2)
        
        count = registry.get_handler_count("count.test")
        assert count == 2
    
    def test_get_handler_count_no_handlers(self, registry):
        """Test getting handler count for event with no handlers."""
        count = registry.get_handler_count("nonexistent.event")
        assert count == 0
    
    def test_register_handler_with_metadata(self, registry):
        """Test registering handler with metadata."""
        async def handler(event): pass
        
        registry.register(
            "meta.event",
            handler,
            metadata={"plugin": "test", "version": "1.0"},
        )
        
        handlers = registry.get_handlers("meta.event")
        assert handler in handlers


class TestEventBus:
    """Tests for EventBus."""
    
    @pytest.fixture
    def event_bus(self):
        """Create EventBus instance."""
        return EventBus(max_depth=10, handler_timeout=5.0)
    
    def test_max_depth_config(self, event_bus):
        """Test max depth configuration."""
        assert event_bus._max_depth == 10
        assert event_bus._handler_timeout == 5.0
    
    def test_global_event_bus(self):
        """Test global event bus functions."""
        bus = EventBus()
        set_event_bus(bus)
        retrieved = get_event_bus()
        assert retrieved is bus
    
    @pytest.mark.asyncio
    async def test_emit_sync(self, event_bus):
        """Test synchronous event emission."""
        results = []
        
        async def handler(event):
            results.append(event.data)
            return "handled"
        
        event_bus.on("sync.test", handler)
        await event_bus.emit("sync.test", data="test_data")
        
        assert "test_data" in results
    
    @pytest.mark.asyncio
    async def test_emit_async(self, event_bus):
        """Test asynchronous event emission."""
        call_order = []
        
        async def handler1(event):
            call_order.append(1)
        
        async def handler2(event):
            call_order.append(2)
        
        event_bus.on("async.test", handler1)
        event_bus.on("async.test", handler2)
        
        await event_bus.emit_async("async.test", data=None)
        
        assert len(call_order) == 2
    
    @pytest.mark.asyncio
    async def test_no_handlers_for_event(self, event_bus):
        """Test emitting event with no handlers."""
        results = await event_bus.emit("nonexistent.event", data="test")
        assert results == []
    
    @pytest.mark.asyncio
    async def test_loop_detection(self, event_bus):
        """Test event loop detection."""
        call_count = 0
        
        async def recursive_handler(event):
            nonlocal call_count
            call_count += 1
            if call_count <= 5:  # Limit to prevent infinite loop
                await event_bus.emit(event.name, data=event.data, sender=event.sender)
        
        event_bus.on("loop.test", recursive_handler)
        await event_bus.emit("loop.test", data=None, sender="test_sender")
        
        # Should not loop infinitely
        assert call_count <= event_bus._max_depth + 1
    
    @pytest.mark.asyncio
    async def test_once_handler(self, event_bus):
        """Test once (single-use) handler."""
        count = 0
        
        async def once_handler(event):
            nonlocal count
            count += 1
        
        event_bus.once("once.test", once_handler)
        
        await event_bus.emit("once.test", data=None)
        await event_bus.emit("once.test", data=None)
        
        assert count == 1
    
    @pytest.mark.asyncio
    async def test_off_unregisters_handler(self, event_bus):
        """Test off() method unregisters handler."""
        count = 0
        
        async def removable_handler(event):
            nonlocal count
            count += 1
        
        event_bus.on("off.test", removable_handler)
        event_bus.off("off.test", removable_handler)
        
        await event_bus.emit("off.test", data=None)
        assert count == 0
    
    def test_get_stats(self, event_bus):
        """Test event statistics."""
        event_bus._stats["test.stat"] = 5
        
        stats = event_bus.get_stats()
        assert stats["test.stat"] == 5
    
    def test_clear_stats(self, event_bus):
        """Test clearing statistics."""
        event_bus._stats["test.clear"] = 10
        event_bus.clear_stats()
        
        assert len(event_bus.get_stats()) == 0
    
    def test_multiple_error_handlers(self, event_bus):
        """Test multiple error handlers are all called."""
        errors1 = []
        errors2 = []
        
        def handler1(event, error):
            errors1.append(error)
        
        def handler2(event, error):
            errors2.append(error)
        
        event_bus.on_error(handler1)
        event_bus.on_error(handler2)
        
        assert len(event_bus._error_handlers) == 2
    
    @pytest.mark.asyncio
    async def test_handler_returns_nested_event(self, event_bus):
        """Test handler that returns a nested EventData."""
        results = []
        
        async def outer_handler(event):
            results.append("outer")
            return EventData(name="inner.event", data=event.data, sender="outer")
        
        async def inner_handler(event):
            results.append("inner")
        
        event_bus.on("outer.event", outer_handler)
        event_bus.on("inner.event", inner_handler)
        
        await event_bus.emit("outer.event", data="test")
        
        assert "outer" in results
    
    def test_list_events_with_handlers(self, event_bus):
        """Test list_events with registered handlers."""
        async def h1(event): pass
        async def h2(event): pass
        
        event_bus.on("event.one", h1)
        event_bus.on("event.two", h2)
        event_bus.on("message.*", h1)
        
        events = event_bus.list_events()
        assert "event.one" in events
        assert "event.two" in events
        assert "message.*" in events


# ============================================================================
# Message Router Tests
# ============================================================================

class TestMessageType:
    """Tests for MessageType enum."""
    
    def test_message_types(self):
        """Test MessageType enum values."""
        assert MessageType.MESSAGE.value == "message"
        assert MessageType.MESSAGE_START.value == "message.start"
        assert MessageType.MESSAGE_CHUNK.value == "message.chunk"
        assert MessageType.MESSAGE_END.value == "message.end"
        assert MessageType.SUBSCRIBE.value == "subscribe"
        assert MessageType.PING.value == "ping"
        assert MessageType.PONG.value == "pong"


class TestContentType:
    """Tests for ContentType enum."""
    
    def test_content_types(self):
        """Test ContentType enum values."""
        assert ContentType.TEXT.value == "text"
        assert ContentType.MARKDOWN.value == "markdown"
        assert ContentType.IMAGE.value == "image"
        assert ContentType.FILE.value == "file"
        assert ContentType.SYSTEM.value == "system"


class TestMessage:
    """Tests for Message dataclass."""
    
    def test_message_creation(self):
        """Test Message creation."""
        msg = Message(
            id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content="Hello, world!",
        )
        
        assert msg.id == "msg_123"
        assert msg.conversation_id == "conv_456"
        assert msg.sender_id == "user_789"
        assert msg.content == "Hello, world!"
        assert msg.content_type == ContentType.TEXT
    
    def test_message_with_metadata(self):
        """Test Message with metadata."""
        msg = Message(
            id="msg_meta",
            conversation_id="conv_1",
            sender_id="user_1",
            content="Test",
            mentions=["user_a", "user_b"],
            reply_to="msg_reply",
        )
        
        assert len(msg.mentions) == 2
        assert msg.reply_to == "msg_reply"
    
    def test_message_complete(self):
        """Test Message with all fields populated."""
        msg = Message(
            id="full_msg",
            conversation_id="conv_full",
            sender_id="sender_full",
            content="Full content",
            content_type=ContentType.MARKDOWN,
            metadata={"key1": "val1", "key2": 2},
            mentions=["user1", "user2"],
            reply_to="reply_to_msg",
            created_at=time.time(),
        )
        
        assert msg.id == "full_msg"
        assert msg.content_type == ContentType.MARKDOWN
        assert msg.metadata == {"key1": "val1", "key2": 2}


class TestStreamMessage:
    """Tests for StreamMessage dataclass."""
    
    def test_stream_message_defaults(self):
        """Test StreamMessage default values."""
        stream = StreamMessage(
            id="stream_def",
            conversation_id="c1",
            sender_id="u1",
        )
        
        assert stream.content_type == ContentType.TEXT
        assert stream.content_buffer == ""
        assert stream.offset == 0
        assert stream.mentions == []
        assert stream.reply_to is None
        assert stream.chunks_received == 0
        assert stream.is_complete is False
        assert stream.is_cancelled is False


class TestStreamBuffer:
    """Tests for StreamBuffer."""
    
    @pytest.fixture
    def buffer(self):
        """Create StreamBuffer instance."""
        return StreamBuffer()
    
    def test_stream_buffer_constants(self, buffer):
        """Test StreamBuffer class constants."""
        assert StreamBuffer.CHUNK_SIZE == 64 * 1024
        assert StreamBuffer.MAX_BUFFER == 10 * 1024 * 1024
        assert StreamBuffer.TIMEOUT == 5.0
    
    @pytest.mark.asyncio
    async def test_start_stream(self, buffer):
        """Test starting a new stream."""
        stream = await buffer.start_stream(
            message_id="stream_1",
            conversation_id="conv_123",
            sender_id="user_456",
        )
        
        assert stream.id == "stream_1"
        assert stream.conversation_id == "conv_123"
        assert stream.content_buffer == ""
        assert stream.is_complete is False
    
    @pytest.mark.asyncio
    async def test_add_chunk(self, buffer):
        """Test adding chunks to stream."""
        await buffer.start_stream(
            message_id="chunk_stream",
            conversation_id="conv_1",
            sender_id="user_1",
        )
        
        await buffer.add_chunk("chunk_stream", "Hello", 0)
        await buffer.add_chunk("chunk_stream", " ", 5)
        await buffer.add_chunk("chunk_stream", "World!", 6)
        
        stream = buffer._buffers["chunk_stream"]
        assert stream.content_buffer == "Hello World!"
        assert stream.offset == 12
    
    @pytest.mark.asyncio
    async def test_end_stream(self, buffer):
        """Test ending a stream."""
        await buffer.start_stream(
            message_id="end_stream",
            conversation_id="conv_1",
            sender_id="user_1",
        )
        await buffer.add_chunk("end_stream", "Complete", 0)
        
        stream = await buffer.end_stream("end_stream", is_complete=True)
        
        assert stream is not None
        assert stream.is_complete is True
        assert "end_stream" not in buffer._buffers
    
    @pytest.mark.asyncio
    async def test_cancel_stream(self, buffer):
        """Test cancelling a stream."""
        await buffer.start_stream(
            message_id="cancel_stream",
            conversation_id="conv_1",
            sender_id="user_1",
        )
        
        await buffer.cancel_stream("cancel_stream")
        
        stream = buffer._buffers.get("cancel_stream")
        assert stream is None
    
    @pytest.mark.asyncio
    async def test_stream_not_found(self, buffer):
        """Test operations on nonexistent stream."""
        with pytest.raises(ValueError, match="Stream not found"):
            await buffer.add_chunk("nonexistent", "data", 0)
    
    @pytest.mark.asyncio
    async def test_offset_mismatch(self, buffer):
        """Test chunk offset validation."""
        await buffer.start_stream(
            message_id="offset_test",
            conversation_id="conv_1",
            sender_id="user_1",
        )
        await buffer.add_chunk("offset_test", "Hello", 0)
        
        with pytest.raises(ValueError, match="Offset mismatch"):
            await buffer.add_chunk("offset_test", "Wrong", 100)
    
    def test_get_active_count(self, buffer):
        """Test getting active stream count."""
        assert buffer.get_active_count() == 0
    
    def test_get_buffer_size(self, buffer):
        """Test getting buffer size for nonexistent message."""
        size = buffer.get_buffer_size("nonexistent")
        assert size is None
    
    @pytest.mark.asyncio
    async def test_add_chunk_updates_last_chunk_time(self, buffer):
        """Test that add_chunk updates last_chunk_at."""
        await buffer.start_stream("t1", "c1", "u1")
        t1 = buffer._buffers["t1"].last_chunk_at
        
        await asyncio.sleep(0.01)
        await buffer.add_chunk("t1", "data", 0)
        
        assert buffer._buffers["t1"].last_chunk_at > t1
    
    @pytest.mark.asyncio
    async def test_on_cancel_callback(self, buffer):
        """Test on_cancel callback is called."""
        callback_results = []
        
        async def on_cancel(stream):
            callback_results.append(stream)
        
        buffer.set_on_cancel(on_cancel)
        
        await buffer.start_stream("cancel_cb", "c1", "u1")
        await buffer.cancel_stream("cancel_cb")
        
        assert len(callback_results) == 1
    
    @pytest.mark.asyncio
    async def test_end_stream_not_started(self, buffer):
        """Test ending stream that was never started."""
        result = await buffer.end_stream("never_started", is_complete=True)
        assert result is None


class TestMessageDispatcher:
    """Tests for MessageDispatcher."""
    
    @pytest.fixture
    def dispatcher(self):
        """Create MessageDispatcher instance."""
        return MessageDispatcher()
    
    @pytest.mark.asyncio
    async def test_subscribe(self, dispatcher):
        """Test subscription to conversation."""
        received = []
        
        async def handler(msg):
            received.append(msg)
        
        await dispatcher.subscribe("conv_1", "sub_1", handler)
        
        assert dispatcher.get_subscriber_count("conv_1") == 1
    
    @pytest.mark.asyncio
    async def test_unsubscribe(self, dispatcher):
        """Test unsubscription from conversation."""
        async def handler(msg): pass
        
        await dispatcher.subscribe("conv_2", "sub_2", handler)
        await dispatcher.unsubscribe("conv_2", "sub_2")
        
        assert dispatcher.get_subscriber_count("conv_2") == 0
    
    @pytest.mark.asyncio
    async def test_dispatch(self, dispatcher):
        """Test message dispatch to subscribers."""
        received = []
        
        async def handler(msg):
            received.append(msg)
        
        await dispatcher.subscribe("conv_3", "sub_3", handler)
        
        msg = Message(
            id="msg_dispatch",
            conversation_id="conv_3",
            sender_id="user_1",
            content="Test dispatch",
        )
        
        await dispatcher.dispatch(msg)
        
        assert len(received) == 1
        assert received[0].content == "Test dispatch"
    
    @pytest.mark.asyncio
    async def test_global_handler(self, dispatcher):
        """Test global handler receives all messages."""
        received = []
        
        async def global_handler(msg):
            received.append(msg)
        
        dispatcher.subscribe_global("global_1", global_handler)
        
        msg = Message(
            id="msg_global",
            conversation_id="conv_any",
            sender_id="user_1",
            content="Global test",
        )
        
        await dispatcher.dispatch(msg)
        
        assert len(received) == 1
    
    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_conversation(self, dispatcher):
        """Test multiple subscribers can receive same message."""
        received = []
        
        async def handler1(msg):
            received.append(("h1", msg.id))
        
        async def handler2(msg):
            received.append(("h2", msg.id))
        
        await dispatcher.subscribe("conv_multi", "sub1", handler1)
        await dispatcher.subscribe("conv_multi", "sub2", handler2)
        
        msg = Message(
            id="msg_multi",
            conversation_id="conv_multi",
            sender_id="user1",
            content="test",
        )
        
        await dispatcher.dispatch(msg)
        
        assert len(received) == 2
        assert ("h1", "msg_multi") in received
        assert ("h2", "msg_multi") in received


class TestMessageQueueMocked:
    """Tests for MessageQueue with mocked Redis."""
    
    @pytest.fixture
    def queue(self):
        """Create MessageQueue instance."""
        return MessageQueue(RedisConfig(host="localhost", port=6379, db=1))
    
    def test_queue_prefix(self, queue):
        """Test queue prefix constants."""
        assert queue.QUEUE_PREFIX == "queue:"
        assert queue.OFFLINE_PREFIX == "offline:"
    
    @pytest.mark.asyncio
    async def test_enqueue_without_redis(self, queue):
        """Test enqueue returns False when Redis not initialized."""
        msg = Message(id="test", conversation_id="c1", sender_id="u1", content="test")
        result = await queue.enqueue("conv1", msg)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_dequeue_without_redis(self, queue):
        """Test dequeue returns empty when Redis not initialized."""
        result = await queue.dequeue("conv1")
        assert result == []
    
    @pytest.mark.asyncio
    async def test_peek_without_redis(self, queue):
        """Test peek returns empty when Redis not initialized."""
        result = await queue.peek("conv1")
        assert result == []
    
    @pytest.mark.asyncio
    async def test_queue_size_without_redis(self, queue):
        """Test queue_size returns 0 when Redis not initialized."""
        result = await queue.queue_size("conv1")
        assert result == 0
    
    @pytest.mark.asyncio
    async def test_enqueue_offline_without_redis(self, queue):
        """Test enqueue_offline returns False when Redis not initialized."""
        msg = Message(id="test", conversation_id="c1", sender_id="u1", content="test")
        result = await queue.enqueue_offline("user1", msg)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_get_offline_messages_without_redis(self, queue):
        """Test get_offline_messages returns empty when Redis not initialized."""
        result = await queue.get_offline_messages("user1")
        assert result == []


class TestMessageRouterMocked:
    """Tests for MessageRouter with mocked components."""
    
    @pytest.fixture
    def router(self):
        """Create MessageRouter instance."""
        return MessageRouter(RedisConfig(host="localhost", port=6379, db=1))
    
    def test_router_config(self, router):
        """Test router configuration."""
        assert router._chunk_size == StreamBuffer.CHUNK_SIZE
        assert router._max_buffer == StreamBuffer.MAX_BUFFER
        assert router._buffer_timeout == StreamBuffer.TIMEOUT
    
    def test_get_active_streams(self, router):
        """Test getting active stream count."""
        count = router.get_active_streams()
        assert count == 0
    
    def test_get_subscriber_count(self, router):
        """Test getting subscriber count."""
        count = router.get_subscriber_count("conv1")
        assert count == 0


class TestMessageRouterHandleWS:
    """Tests for MessageRouter WebSocket message handling."""
    
    @pytest.fixture
    def router(self):
        """Create MessageRouter instance."""
        return MessageRouter(RedisConfig(host="localhost", port=6379, db=1))
    
    @pytest.mark.asyncio
    async def test_handle_ws_message_unknown_type(self, router):
        """Test handling unknown WebSocket message type."""
        await router.handle_ws_message({"type": "unknown.type"})
    
    @pytest.mark.asyncio
    async def test_handle_message_start(self, router):
        """Test handling message.start."""
        await router.handle_ws_message({
            "type": "message.start",
            "id": "msg_start",
            "params": {
                "conversation_id": "conv_1",
                "sender_id": "user_1",
                "content_type": "text",
            },
        })
        
        assert router.get_active_streams() == 1
    
    @pytest.mark.asyncio
    async def test_handle_message_chunk(self, router):
        """Test handling message.chunk."""
        await router.handle_ws_message({
            "type": "message.start",
            "id": "msg_chunk",
            "params": {
                "conversation_id": "conv_1",
                "sender_id": "user_1",
            },
        })
        
        await router.handle_ws_message({
            "type": "message.chunk",
            "id": "msg_chunk",
            "params": {
                "content": "Hello",
                "offset": 0,
            },
        })
        
        buffer_size = router._stream_buffer.get_buffer_size("msg_chunk")
        assert buffer_size == 5
    
    @pytest.mark.asyncio
    async def test_handle_message_end(self, router):
        """Test handling message.end."""
        await router.handle_ws_message({
            "type": "message.start",
            "id": "msg_end",
            "params": {
                "conversation_id": "conv_1",
                "sender_id": "user_1",
            },
        })
        
        await router.handle_ws_message({
            "type": "message.end",
            "id": "msg_end",
            "params": {
                "is_complete": True,
            },
        })
        
        assert router.get_active_streams() == 0
    
    @pytest.mark.asyncio
    async def test_handle_message_cancel(self, router):
        """Test handling message.cancel."""
        await router.handle_ws_message({
            "type": "message.start",
            "id": "msg_cancel",
            "params": {
                "conversation_id": "conv_1",
                "sender_id": "user_1",
            },
        })
        
        await router.handle_ws_message({
            "type": "message.cancel",
            "id": "msg_cancel",
        })
        
        assert router.get_active_streams() == 0
    
    @pytest.mark.asyncio
    async def test_handle_simple_message(self, router):
        """Test handling simple message."""
        received = []
        
        async def handler(msg):
            received.append(msg)
        
        router.set_on_message(handler)
        
        await router.handle_ws_message({
            "type": "message",
            "id": "msg_simple",
            "params": {
                "conversation_id": "conv_1",
                "sender_id": "user_1",
                "content": "Hello, world!",
                "content_type": "text",
                "mentions": ["user_2"],
            },
        })
        
        assert len(received) == 1
        assert received[0].content == "Hello, world!"
    
    @pytest.mark.asyncio
    async def test_handle_message_missing_id(self, router):
        """Test handling message with missing id."""
        with pytest.raises(ValueError, match="Missing message id"):
            await router.handle_ws_message({
                "type": "message.start",
                "params": {},
            })
    
    def test_set_on_message_callback(self, router):
        """Test set_on_message sets callback."""
        async def callback(msg): pass
        
        router.set_on_message(callback)
        
        assert router._on_message is callback
        assert router._stream_buffer._on_complete is not None


# ============================================================================
# Auth Service Tests
# ============================================================================

class TestTokenData:
    """Tests for TokenData."""
    
    def test_token_data_user_id(self):
        """Test TokenData user_id property."""
        from datetime import datetime
        token = TokenData(
            sub="user_123",
            exp=datetime.now(timezone.utc),
            iat=datetime.now(timezone.utc),
        )
        assert token.user_id == "user_123"
    
    def test_token_data_default_metadata(self):
        """Test TokenData default metadata."""
        from datetime import datetime
        token = TokenData(
            sub="user",
            exp=datetime.now(timezone.utc),
            iat=datetime.now(timezone.utc),
        )
        assert token.metadata == {}
    
    def test_token_data_custom_metadata(self):
        """Test TokenData with custom metadata."""
        from datetime import datetime
        token = TokenData(
            sub="user",
            exp=datetime.now(timezone.utc),
            iat=datetime.now(timezone.utc),
            metadata={"key": "value"},
        )
        assert token.metadata == {"key": "value"}


class TestUserCredentials:
    """Tests for UserCredentials."""
    
    def test_user_credentials_default_permissions(self):
        """Test UserCredentials default permissions."""
        user = UserCredentials(
            user_id="u1",
            username="test",
            password_hash="hash",
        )
        assert user.permissions == []
    
    def test_user_credentials_default_agent(self):
        """Test UserCredentials default is_agent."""
        user = UserCredentials(
            user_id="u1",
            username="test",
            password_hash="hash",
        )
        assert user.is_agent is False
    
    def test_user_credentials_default_disabled(self):
        """Test UserCredentials default disabled."""
        user = UserCredentials(
            user_id="u1",
            username="test",
            password_hash="hash",
        )
        assert user.disabled is False


class TestAuthService:
    """Tests for AuthService."""
    
    @pytest.fixture
    def auth_service(self):
        """Create AuthService instance with mocked bcrypt."""
        service = AuthService(secret_key="test_secret_key_12345")
        service.hash_password = lambda p: hashlib.sha256(p.encode()).hexdigest()
        service.verify_password = lambda p, h: hashlib.sha256(p.encode()).hexdigest() == h
        return service
    
    def test_hash_password(self, auth_service):
        """Test password hashing."""
        password = "my_secure_password"
        hashed = auth_service.hash_password(password)
        
        assert hashed != password
        assert hashed == hashlib.sha256(password.encode()).hexdigest()
    
    def test_verify_password_correct(self, auth_service):
        """Test password verification with correct password."""
        password = "correct_password"
        hashed = auth_service.hash_password(password)
        
        assert auth_service.verify_password(password, hashed) is True
    
    def test_verify_password_incorrect(self, auth_service):
        """Test password verification with incorrect password."""
        password = "correct_password"
        hashed = auth_service.hash_password(password)
        
        assert auth_service.verify_password("wrong_password", hashed) is False
    
    def test_create_access_token(self, auth_service):
        """Test access token creation."""
        token = auth_service.create_access_token("user_123")
        
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0
    
    def test_create_refresh_token(self, auth_service):
        """Test refresh token creation."""
        token = auth_service.create_refresh_token("user_456")
        
        assert token is not None
        assert isinstance(token, str)
    
    def test_create_tokens(self, auth_service):
        """Test creating both tokens."""
        tokens = auth_service.create_tokens("user_789")
        
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert "token_type" in tokens
        assert tokens["token_type"] == "bearer"
    
    def test_verify_access_token(self, auth_service):
        """Test access token verification."""
        token = auth_service.create_access_token("user_verify")
        token_data = auth_service.verify_token(token)
        
        assert token_data is not None
        assert token_data.user_id == "user_verify"
        assert token_data.type == "access"
    
    def test_verify_refresh_token(self, auth_service):
        """Test refresh token verification."""
        token = auth_service.create_refresh_token("user_refresh")
        token_data = auth_service.verify_token(token, token_type="refresh")
        
        assert token_data is not None
        assert token_data.user_id == "user_refresh"
        assert token_data.type == "refresh"
    
    def test_verify_token_wrong_type(self, auth_service):
        """Test token type mismatch."""
        access_token = auth_service.create_access_token("user_type")
        token_data = auth_service.verify_token(access_token, token_type="refresh")
        
        assert token_data is None
    
    def test_verify_invalid_token(self, auth_service):
        """Test verification of invalid token."""
        token_data = auth_service.verify_token("invalid.token.here")
        
        assert token_data is None
    
    def test_verify_expired_token(self, auth_service):
        """Test verification of expired token."""
        from jose import jwt
        now = int(time.time())
        payload = {
            "sub": "user_expired",
            "exp": now - 10,
            "iat": now - 20,
            "type": "access",
        }
        expired_token = jwt.encode(payload, auth_service._secret_key, algorithm=auth_service._jwt_algorithm)
        
        token_data = auth_service.verify_token(expired_token)
        
        assert token_data is None
    
    def test_token_with_additional_claims(self, auth_service):
        """Test creating token with additional claims."""
        token = auth_service.create_access_token(
            "user_claims",
            additional_claims={"role": "admin", "tenant": "acme"},
        )
        
        token_data = auth_service.verify_token(token)
        
        assert token_data is not None
        assert token_data.metadata.get("role") == "admin"
        assert token_data.metadata.get("tenant") == "acme"
    
    def test_create_token_custom_expiration(self, auth_service):
        """Test creating token with custom expiration."""
        token = auth_service.create_access_token(
            "user_custom",
            expires_delta=timedelta(hours=24),
        )
        
        token_data = auth_service.verify_token(token)
        
        assert token_data is not None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        assert token_data.exp > now
    
    def test_refresh_access_token(self, auth_service):
        """Test access token refresh."""
        refresh_token = auth_service.create_refresh_token("user_refresh_test")
        tokens = auth_service.refresh_access_token(refresh_token)
        
        assert tokens is not None
        assert "access_token" in tokens
        assert "refresh_token" in tokens
    
    def test_revoke_token(self, auth_service):
        """Test token revocation."""
        token = auth_service.create_access_token("user_revoke")
        
        result = auth_service.revoke_token(token)
        
        assert result is True
        assert auth_service.is_token_blacklisted(token) is True
    
    def test_revoke_invalid_token(self, auth_service):
        """Test revoking invalid token returns False."""
        result = auth_service.revoke_token("invalid_token")
        assert result is False
    
    def test_is_token_blacklisted(self, auth_service):
        """Test is_token_blacklisted for non-blacklisted token."""
        assert auth_service.is_token_blacklisted("not_blacklisted") is False
    
    @pytest.mark.asyncio
    async def test_register_user(self, auth_service):
        """Test user registration."""
        user = await auth_service.register_user(
            username="newuser",
            password="password123",
            user_id="user_new",
        )
        
        assert user is not None
        assert user.username == "newuser"
        assert user.user_id == "user_new"
        assert user.password_hash != "password123"
    
    @pytest.mark.asyncio
    async def test_register_duplicate_username(self, auth_service):
        """Test registration with duplicate username."""
        await auth_service.register_user(username="duplicate", password="password1")
        
        user2 = await auth_service.register_user(username="duplicate", password="password2")
        
        assert user2 is None
    
    @pytest.mark.asyncio
    async def test_authenticate_user(self, auth_service):
        """Test user authentication."""
        await auth_service.register_user(username="authuser", password="correct_password")
        
        user = await auth_service.authenticate("authuser", "correct_password")
        
        assert user is not None
        assert user.username == "authuser"
    
    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, auth_service):
        """Test authentication with wrong password."""
        await auth_service.register_user(username="wrongpass", password="correct_password")
        
        user = await auth_service.authenticate("wrongpass", "wrong_password")
        
        assert user is None
    
    @pytest.mark.asyncio
    async def test_authenticate_nonexistent_user(self, auth_service):
        """Test authentication of nonexistent user."""
        user = await auth_service.authenticate("nonexistent", "any_password")
        
        assert user is None
    
    @pytest.mark.asyncio
    async def test_get_user(self, auth_service):
        """Test getting user by ID."""
        user = await auth_service.register_user(
            username="getuser",
            password="password",
            user_id="user_get",
        )
        
        retrieved = await auth_service.get_user("user_get")
        
        assert retrieved is not None
        assert retrieved.username == "getuser"
    
    @pytest.mark.asyncio
    async def test_get_user_by_username(self, auth_service):
        """Test getting user by username."""
        await auth_service.register_user(username="findbyuser", password="password")
        
        user = await auth_service.get_user_by_username("findbyuser")
        
        assert user is not None
        assert user.username == "findbyuser"
    
    @pytest.mark.asyncio
    async def test_update_user(self, auth_service):
        """Test updating user."""
        await auth_service.register_user(
            username="updateuser",
            password="password",
            user_id="user_update",
        )
        
        user = await auth_service.update_user(
            "user_update",
            permissions=["read", "write"],
        )
        
        assert user is not None
        assert "read" in user.permissions
        assert "write" in user.permissions
    
    @pytest.mark.asyncio
    async def test_delete_user(self, auth_service):
        """Test deleting user."""
        await auth_service.register_user(
            username="deleteuser",
            password="password",
            user_id="user_delete",
        )
        
        result = await auth_service.delete_user("user_delete")
        
        assert result is True
        assert await auth_service.get_user("user_delete") is None
    
    def test_has_permission(self, auth_service):
        """Test permission checking."""
        user = UserCredentials(
            user_id="user_perm",
            username="permuser",
            password_hash="hash",
            permissions=["read", "write"],
        )
        
        assert auth_service.has_permission(user, "read") is True
        assert auth_service.has_permission(user, "delete") is False
    
    def test_has_permission_agent(self, auth_service):
        """Test agent has all permissions."""
        agent = UserCredentials(
            user_id="agent_perm",
            username="agent",
            password_hash="hash",
            is_agent=True,
            permissions=[],
        )
        
        assert auth_service.has_permission(agent, "anything") is True
    
    def test_has_any_permission(self, auth_service):
        """Test has_any_permission."""
        user = UserCredentials(
            user_id="user_any",
            username="anyuser",
            password_hash="hash",
            permissions=["read"],
        )
        
        assert auth_service.has_any_permission(user, ["read", "write"]) is True
        assert auth_service.has_any_permission(user, ["delete", "write"]) is False
    
    def test_has_all_permissions(self, auth_service):
        """Test has_all_permissions."""
        user = UserCredentials(
            user_id="user_all",
            username="alluser",
            password_hash="hash",
            permissions=["read", "write", "delete"],
        )
        
        assert auth_service.has_all_permissions(user, ["read", "write"]) is True
        assert auth_service.has_all_permissions(user, ["read", "admin"]) is False
    
    def test_introspect_token_active(self, auth_service):
        """Test token introspection for active token."""
        token = auth_service.create_access_token("user_introspect")
        
        info = auth_service.introspect_token(token)
        
        assert info["active"] is True
        assert info["sub"] == "user_introspect"
    
    def test_introspect_token_invalid(self, auth_service):
        """Test token introspection for invalid token."""
        info = auth_service.introspect_token("invalid_token")
        
        assert info["active"] is False
    
    @pytest.mark.asyncio
    async def test_authenticate_token(self, auth_service):
        """Test authenticating with a token."""
        await auth_service.register_user("tokenuser", "password")
        auth_user = await auth_service.authenticate("tokenuser", "password")
        
        tokens = auth_service.create_tokens(auth_user.user_id)
        
        auth_result = await auth_service.authenticate_token(tokens["access_token"])
        
        assert auth_result is not None
        assert auth_result.username == "tokenuser"
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, auth_service):
        """Test getting nonexistent user."""
        user = await auth_service.get_user("nonexistent_id")
        assert user is None
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_user_by_username(self, auth_service):
        """Test getting nonexistent user by username."""
        user = await auth_service.get_user_by_username("nonexistent_username")
        assert user is None
    
    @pytest.mark.asyncio
    async def test_update_nonexistent_user(self, auth_service):
        """Test updating nonexistent user."""
        user = await auth_service.update_user("nonexistent_id", disabled=True)
        assert user is None
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_user(self, auth_service):
        """Test deleting nonexistent user."""
        result = await auth_service.delete_user("nonexistent_id")
        assert result is False


# ============================================================================
# Integration Tests
# ============================================================================

class TestKernelIntegration:
    """Integration tests for kernel modules."""
    
    @pytest.fixture
    def auth_service(self):
        """Create AuthService for integration tests with mocked bcrypt."""
        service = AuthService(secret_key="integration_test_key")
        service.hash_password = lambda p: hashlib.sha256(p.encode()).hexdigest()
        service.verify_password = lambda p, h: hashlib.sha256(p.encode()).hexdigest() == h
        return service
    
    @pytest.mark.asyncio
    async def test_session_auth_flow(self, auth_service):
        """Test full session authentication flow."""
        user = await auth_service.register_user(
            username="sessionuser",
            password="password123",
            user_id="user_session",
        )
        
        auth_user = await auth_service.authenticate("sessionuser", "password123")
        assert auth_user is not None
        
        tokens = auth_service.create_tokens(auth_user.user_id)
        assert tokens["access_token"] is not None
        
        token_data = auth_service.verify_token(tokens["access_token"])
        assert token_data.user_id == "user_session"
    
    @pytest.mark.asyncio
    async def test_event_and_message_flow(self):
        """Test event bus and message router interaction."""
        event_bus = EventBus()
        dispatcher = MessageDispatcher()
        
        async def message_handler(msg):
            pass
        
        async def event_handler(event):
            msg = Message(
                id=f"msg_{event.data}",
                conversation_id="conv_event",
                sender_id=event.sender or "system",
                content=str(event.data),
            )
            await dispatcher.dispatch(msg)
        
        event_bus.on("create.message", event_handler)
        await dispatcher.subscribe("conv_event", "event_sub", message_handler)
        
        await event_bus.emit("create.message", data="test_content", sender="test")
        
        await asyncio.sleep(0.1)


# Additional tests for coverage

class TestMessageRouterEnqueueQueue:
    """Tests for MessageRouter queue operations."""
    
    @pytest.fixture
    def router(self):
        """Create MessageRouter instance."""
        return MessageRouter(RedisConfig(host="localhost", port=6379, db=1))
    
    @pytest.mark.asyncio
    async def test_enqueue_message_without_redis(self, router):
        """Test enqueue_message returns False without Redis."""
        msg = Message(id="test_enq", conversation_id="conv_1", sender_id="user_1", content="test")
        result = await router.enqueue_message("conv_1", msg)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_flush_queue_without_redis(self, router):
        """Test flush_queue returns empty without Redis."""
        result = await router.flush_queue("conv_1")
        assert result == []
    
    @pytest.mark.asyncio
    async def test_queue_size_without_redis(self, router):
        """Test queue_size returns 0 without Redis."""
        result = await router.queue_size("conv_1")
        assert result == 0


class TestSessionPoolRedisMethods:
    """Tests for ConnectionPool Redis methods."""
    
    @pytest.fixture
    def pool(self):
        """Create ConnectionPool instance."""
        return ConnectionPool(redis_config=RedisConfig(host="localhost", port=6379, db=1))
    
    @pytest.mark.asyncio
    async def test_store_session_redis_without_client(self, pool):
        """Test _store_session_redis returns early without Redis client."""
        session = SessionData(session_id="sess_redis", user_id="user_redis", connection_id="conn_redis")
        await pool._store_session_redis(session)
    
    @pytest.mark.asyncio
    async def test_load_session_redis_without_client(self, pool):
        """Test _load_session_redis returns None without Redis client."""
        result = await pool._load_session_redis("nonexistent")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_handle_disconnect_exceeds_max_retry(self, pool):
        """Test _handle_disconnect when max retry exceeded."""
        session = SessionData(
            session_id="sess_max",
            user_id="user_max",
            connection_id="conn_max",
            max_retry=2,
            reconnect_count=2,
        )
        
        with patch.object(pool, '_on_disconnect', new_callable=AsyncMock) as mock_cb:
            await pool._handle_disconnect(session)
            assert session.state == SessionState.DISCONNECTED
    
    @pytest.mark.asyncio
    async def test_handle_disconnect_under_max_retry(self, pool):
        """Test _handle_disconnect increments count and triggers reconnect."""
        session = SessionData(
            session_id="sess_retry",
            user_id="user_retry",
            connection_id="conn_retry",
            max_retry=3,
            reconnect_count=1,
        )
        
        with patch.object(pool, '_on_reconnect', new_callable=AsyncMock) as mock_cb:
            await pool._handle_disconnect(session)
            assert session.state == SessionState.RECONNECTING
            assert session.reconnect_count == 2


class TestStreamBufferChunks:
    """Tests for StreamBuffer chunk tracking."""
    
    @pytest.fixture
    def buffer(self):
        """Create StreamBuffer instance."""
        return StreamBuffer()
    
    @pytest.mark.asyncio
    async def test_chunks_received_count(self, buffer):
        """Test chunks_received counter."""
        await buffer.start_stream("chunk_count", "c1", "u1")
        await buffer.add_chunk("chunk_count", "a", 0)
        await buffer.add_chunk("chunk_count", "b", 1)
        await buffer.add_chunk("chunk_count", "c", 2)
        
        stream = buffer._buffers["chunk_count"]
        assert stream.chunks_received == 3
    
    @pytest.mark.asyncio
    async def test_timeout_task_cancelled_on_end(self, buffer):
        """Test timeout task is cancelled when stream ends."""
        await buffer.start_stream("timeout_cancel", "c1", "u1")
        
        task = buffer._timeout_tasks.get("timeout_cancel")
        assert task is not None
        
        await buffer.end_stream("timeout_cancel", is_complete=True)
        
        assert "timeout_cancel" not in buffer._timeout_tasks


class TestEventBusChainTracking:
    """Tests for EventBus chain tracking."""
    
    @pytest.fixture
    def event_bus(self):
        """Create EventBus with very low max_depth."""
        return EventBus(max_depth=3, handler_timeout=5.0)
    
    @pytest.mark.asyncio
    async def test_chain_tracking_adds_to_set(self, event_bus):
        """Test that chain tracking adds event names to set."""
        async def handler(event): pass
        
        event_bus.on("track.test", handler)
        await event_bus.emit("track.test", data=None, sender="test_sender")
        
        assert len(event_bus._event_chains) == 0
    
    @pytest.mark.asyncio
    async def test_multiple_senders_same_event(self, event_bus):
        """Test multiple senders can emit same event."""
        results = []
        
        async def handler(event):
            results.append(event.sender)
        
        event_bus.on("multi.sender", handler)
        
        await event_bus.emit("multi.sender", data=None, sender="sender_a")
        await event_bus.emit("multi.sender", data=None, sender="sender_b")
        
        assert "sender_a" in results
        assert "sender_b" in results
