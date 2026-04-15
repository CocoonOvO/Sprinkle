"""Tests for Phase 5: WebSocket & SSE.

Tests cover:
- WebSocket connection and authentication
- WebSocket subscribe/unsubscribe
- WebSocket message sending and streaming
- WebSocket heartbeat
- SSE connection and events
- SSE subscription management
- Session integration
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sprinkle.api.websocket import (
    ConnectionManager,
    StreamBuffer,
    WebSocketHandler,
    ErrorCode,
)
from sprinkle.api.events import (
    SSEConnection,
    SSEEventType,
    SSEEventEmitter,
    _sse_connections,
    _register_sse_connection,
    _unregister_sse_connection,
)
from sprinkle.kernel.session import SessionManager, SessionState, SessionData
from sprinkle.kernel.auth import AuthService, UserCredentials
from sprinkle.plugins.events import PluginEventBus


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_auth_service():
    """Create a mock AuthService."""
    service = MagicMock(spec=AuthService)
    
    # Create mock user
    mock_user = UserCredentials(
        user_id="test_user_123",
        username="testuser",
        password_hash="test_hash",
        is_agent=False,
    )
    
    service.authenticate_token = AsyncMock(return_value=mock_user)
    service.authenticate = AsyncMock(return_value=mock_user)
    return service


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager."""
    manager = MagicMock(spec=SessionManager)
    
    # Mock session data
    mock_session = SessionData(
        session_id="sess_test123",
        user_id="test_user_123",
        connection_id="conn_test123",
        state=SessionState.AUTHENTICATED,
        subscriptions=set(),
    )
    
    manager.create_session = AsyncMock(return_value=mock_session)
    manager.get_session = AsyncMock(return_value=mock_session)
    manager.authenticate = AsyncMock(return_value=True)
    manager.subscribe = AsyncMock(return_value=True)
    manager.unsubscribe = AsyncMock(return_value=True)
    manager.start_heartbeat = AsyncMock()
    manager.stop_heartbeat = AsyncMock()
    manager.receive_pong = AsyncMock()
    manager.delete_session = AsyncMock()
    
    return manager


@pytest.fixture
def mock_event_bus():
    """Create a mock PluginEventBus."""
    bus = MagicMock(spec=PluginEventBus)
    bus.emit_async = AsyncMock(return_value=[])
    return bus


@pytest.fixture
def ws_handler(mock_session_manager, mock_event_bus, mock_auth_service):
    """Create a WebSocketHandler with mocked dependencies."""
    return WebSocketHandler(
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        auth_service=mock_auth_service,
    )


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    from starlette.websockets import WebSocketState
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED  # WebSocketState.CONNECTED
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


@pytest.fixture(autouse=True)
def cleanup_connection_manager():
    """Clean up ConnectionManager state before each test."""
    ConnectionManager._ws_connections.clear()
    ConnectionManager._sse_connections.clear()
    ConnectionManager._stream_buffers.clear()
    yield
    ConnectionManager._ws_connections.clear()
    ConnectionManager._sse_connections.clear()
    ConnectionManager._stream_buffers.clear()


@pytest.fixture(autouse=True)
def cleanup_sse_connections():
    """Clean up SSE connections before each test."""
    _sse_connections.clear()
    yield
    _sse_connections.clear()


# ============================================================================
# StreamBuffer Tests
# ============================================================================

class TestStreamBuffer:
    """Tests for StreamBuffer class."""

    def test_create_buffer(self):
        """Test buffer creation."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        
        assert buffer.message_id == "msg_123"
        assert buffer.conversation_id == "conv_456"
        assert buffer.sender_id == "user_789"
        assert buffer.content_type == "text"
        assert buffer.chunks == []
        assert buffer.total_size == 0

    def test_add_chunk_success(self):
        """Test adding chunks successfully."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        
        assert buffer.add_chunk("Hello", 0) is True
        assert buffer.add_chunk(" World", 5) is True
        assert buffer.full_content == "Hello World"
        assert buffer.total_size == 11

    def test_add_chunk_offset_mismatch(self):
        """Test adding chunk with wrong offset fails."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        
        assert buffer.add_chunk("Hello", 0) is True
        assert buffer.add_chunk("World", 10) is False  # Wrong offset
        assert buffer.full_content == "Hello"

    def test_add_chunk_exceeds_max_size(self):
        """Test adding chunk that exceeds max size fails."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
            max_size=10,
        )
        
        # "Hello" = 5 bytes, fits in 10
        assert buffer.add_chunk("Hello", 0) is True
        # "Hello World!" = 12 bytes total, exceeds 10 - this should fail
        result = buffer.add_chunk("Hello World!", 5)  # 12 bytes total > 10
        assert result is False

    def test_is_expired(self):
        """Test buffer expiration check."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
            timeout=1.0,  # 1 second timeout
        )
        
        assert buffer.is_expired() is False
        
        # Simulate time passing
        buffer.created_at = time.time() - 2.0
        assert buffer.is_expired() is True


# ============================================================================
# ConnectionManager Tests
# ============================================================================

class TestConnectionManager:
    """Tests for ConnectionManager class."""

    @pytest.mark.asyncio
    async def test_register_websocket(self, mock_websocket):
        """Test WebSocket registration."""
        await ConnectionManager.register_websocket("sess_123", mock_websocket)
        
        assert ConnectionManager._ws_connections.get("sess_123") == mock_websocket

    @pytest.mark.asyncio
    async def test_unregister_websocket(self, mock_websocket):
        """Test WebSocket unregistration."""
        await ConnectionManager.register_websocket("sess_123", mock_websocket)
        ws = await ConnectionManager.unregister_websocket("sess_123")
        
        assert ws == mock_websocket
        assert ConnectionManager._ws_connections.get("sess_123") is None

    @pytest.mark.asyncio
    async def test_send_to_websocket(self, mock_websocket):
        """Test sending message to WebSocket."""
        await ConnectionManager.register_websocket("sess_123", mock_websocket)
        
        result = await ConnectionManager.send_to_websocket(
            "sess_123",
            {"type": "message", "data": {"id": "msg_123"}}
        )
        
        assert result is True
        mock_websocket.send_json.assert_called_once_with({
            "type": "message",
            "data": {"id": "msg_123"}
        })

    @pytest.mark.asyncio
    async def test_send_to_websocket_not_found(self):
        """Test sending to non-existent WebSocket returns False."""
        result = await ConnectionManager.send_to_websocket(
            "nonexistent",
            {"type": "message"}
        )
        
        assert result is False

    def test_add_stream_buffer(self):
        """Test adding stream buffer."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        
        ConnectionManager.add_stream_buffer("msg_123", buffer)
        
        assert ConnectionManager.get_stream_buffer("msg_123") == buffer

    def test_remove_stream_buffer(self):
        """Test removing stream buffer."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        ConnectionManager.add_stream_buffer("msg_123", buffer)
        
        removed = ConnectionManager.remove_stream_buffer("msg_123")
        
        assert removed == buffer
        assert ConnectionManager.get_stream_buffer("msg_123") is None


# ============================================================================
# WebSocketHandler Tests
# ============================================================================

class TestWebSocketHandler:
    """Tests for WebSocketHandler class."""

    @pytest.mark.asyncio
    async def test_handle_connection_success(self, ws_handler, mock_websocket, mock_auth_service):
        """Test successful WebSocket connection."""
        # Create mock user for token verification
        mock_user = UserCredentials(
            user_id="test_user_123",
            username="testuser",
            password_hash="test_hash",
            is_agent=False,
        )
        mock_auth_service.authenticate_token = AsyncMock(return_value=mock_user)
        
        session_id = await ws_handler.handle_connection(mock_websocket, "valid_token")
        
        assert session_id is not None
        # Note: websocket.accept() is called in the endpoint, not in handle_connection
        mock_auth_service.authenticate_token.assert_called_once_with("valid_token")

    @pytest.mark.asyncio
    async def test_handle_connection_invalid_token(self, ws_handler, mock_websocket, mock_auth_service):
        """Test WebSocket connection with invalid token."""
        mock_auth_service.authenticate_token = AsyncMock(return_value=None)
        
        session_id = await ws_handler.handle_connection(mock_websocket, "invalid_token")
        
        assert session_id is None
        mock_websocket.send_json.assert_called()
        mock_websocket.close.assert_called_once_with(code=4001)

    @pytest.mark.asyncio
    async def test_handle_message_subscribe(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling subscribe message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        message = {
            "type": "subscribe",
            "params": {"conversation_id": "conv_123"}
        }
        
        await ws_handler.handle_message("sess_test123", message)
        
        mock_session_manager.subscribe.assert_called_once_with("sess_test123", "conv_123")
        mock_websocket.send_json.assert_called()

    @pytest.mark.asyncio
    async def test_handle_message_unsubscribe(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling unsubscribe message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        message = {
            "type": "unsubscribe",
            "params": {"conversation_id": "conv_123"}
        }
        
        await ws_handler.handle_message("sess_test123", message)
        
        mock_session_manager.unsubscribe.assert_called_once_with("sess_test123", "conv_123")

    @pytest.mark.asyncio
    async def test_handle_message_ping(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling ping message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        message = {"type": "ping"}
        
        await ws_handler.handle_message("sess_test123", message)
        
        mock_websocket.send_json.assert_called_with({"type": "pong"})
        mock_session_manager.receive_pong.assert_called_once_with("sess_test123")

    @pytest.mark.asyncio
    async def test_handle_message_unknown_type(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling unknown message type."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        message = {"type": "unknown_type"}
        
        await ws_handler.handle_message("sess_test123", message)
        
        # Should send error
        call_args = mock_websocket.send_json.call_args
        assert call_args is not None
        sent_data = call_args[0][0]
        assert sent_data["type"] == "error"
        assert sent_data["code"] == ErrorCode.INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_handle_stream_start(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling stream start message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        message = {
            "type": "message.start",
            "id": "stream_123",
            "params": {
                "conversation_id": "conv_123",
                "content_type": "text",
                "mentions": [],
                "reply_to": None,
            }
        }
        
        await ws_handler.handle_message("sess_test123", message)
        
        buffer = ConnectionManager.get_stream_buffer("stream_123")
        assert buffer is not None
        assert buffer.conversation_id == "conv_123"

    @pytest.mark.asyncio
    async def test_handle_stream_chunk(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling stream chunk message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        # First start the stream
        start_message = {
            "type": "message.start",
            "id": "stream_123",
            "params": {
                "conversation_id": "conv_123",
                "content_type": "text",
            }
        }
        await ws_handler.handle_message("sess_test123", start_message)
        
        # Then send chunks
        chunk_message = {
            "type": "message.chunk",
            "id": "stream_123",
            "params": {
                "content": "Hello",
                "offset": 0,
            }
        }
        await ws_handler.handle_message("sess_test123", chunk_message)
        
        buffer = ConnectionManager.get_stream_buffer("stream_123")
        assert buffer is not None
        assert buffer.full_content == "Hello"

    @pytest.mark.asyncio
    async def test_handle_stream_end(self, ws_handler, mock_websocket, mock_session_manager, mock_event_bus):
        """Test handling stream end message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        # Start stream
        start_message = {
            "type": "message.start",
            "id": "stream_123",
            "params": {
                "conversation_id": "conv_123",
                "content_type": "text",
            }
        }
        await ws_handler.handle_message("sess_test123", start_message)
        
        # Add chunk
        chunk_message = {
            "type": "message.chunk",
            "id": "stream_123",
            "params": {
                "content": "Hello World",
                "offset": 0,
            }
        }
        await ws_handler.handle_message("sess_test123", chunk_message)
        
        # End stream
        end_message = {
            "type": "message.end",
            "id": "stream_123",
            "params": {
                "is_complete": True,
            }
        }
        await ws_handler.handle_message("sess_test123", end_message)
        
        # Buffer should be removed
        assert ConnectionManager.get_stream_buffer("stream_123") is None
        
        # Event should be emitted
        mock_event_bus.emit_async.assert_called()

    @pytest.mark.asyncio
    async def test_handle_stream_cancel(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling stream cancel message."""
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        # Start and add some content
        start_message = {
            "type": "message.start",
            "id": "stream_123",
            "params": {
                "conversation_id": "conv_123",
                "content_type": "text",
            }
        }
        await ws_handler.handle_message("sess_test123", start_message)
        
        # Cancel
        cancel_message = {
            "type": "message.cancel",
            "id": "stream_123",
        }
        await ws_handler.handle_message("sess_test123", cancel_message)
        
        # Buffer should be removed
        assert ConnectionManager.get_stream_buffer("stream_123") is None


# ============================================================================
# SSEEventEmitter Tests
# ============================================================================

class TestSSEEventEmitter:
    """Tests for SSEEventEmitter class."""

    @pytest.mark.asyncio
    async def test_emit_member_joined(self):
        """Test emitting member_joined event."""
        emitter = SSEEventEmitter()
        SSEEventEmitter.set_instance(emitter)
        
        # Create a mock SSE connection
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id=None,
            subscriptions={"conv_456"},
            queue=asyncio.Queue(),
        )
        await _register_sse_connection(connection)
        
        # Emit event
        await emitter.emit_member_joined(
            conversation_id="conv_456",
            user_id="user_789",
            member={"id": "user_789", "username": "newuser"},
        )
        
        # Check event was queued
        event = await asyncio.wait_for(connection.queue.get(), timeout=1)
        assert event["event"] == SSEEventType.MEMBER_JOINED
        assert event["data"]["conversation_id"] == "conv_456"
        assert event["data"]["user_id"] == "user_789"

    @pytest.mark.asyncio
    async def test_emit_member_left(self):
        """Test emitting member_left event."""
        emitter = SSEEventEmitter()
        SSEEventEmitter.set_instance(emitter)
        
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id=None,
            subscriptions={"conv_456"},
            queue=asyncio.Queue(),
        )
        await _register_sse_connection(connection)
        
        await emitter.emit_member_left(
            conversation_id="conv_456",
            user_id="user_789",
        )
        
        event = await asyncio.wait_for(connection.queue.get(), timeout=1)
        assert event["event"] == SSEEventType.MEMBER_LEFT
        assert event["data"]["user_id"] == "user_789"

    @pytest.mark.asyncio
    async def test_emit_conversation_updated(self):
        """Test emitting conversation_updated event."""
        emitter = SSEEventEmitter()
        SSEEventEmitter.set_instance(emitter)
        
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id=None,
            subscriptions={"conv_456"},
            queue=asyncio.Queue(),
        )
        await _register_sse_connection(connection)
        
        await emitter.emit_conversation_updated(
            conversation_id="conv_456",
            update_type="name_changed",
            data={"old_name": "Old", "new_name": "New"},
        )
        
        event = await asyncio.wait_for(connection.queue.get(), timeout=1)
        assert event["event"] == SSEEventType.CONVERSATION_UPDATED
        assert event["data"]["update_type"] == "name_changed"

    @pytest.mark.asyncio
    async def test_emit_message_sent(self):
        """Test emitting message_sent event."""
        emitter = SSEEventEmitter()
        SSEEventEmitter.set_instance(emitter)
        
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id=None,
            subscriptions={"conv_456"},
            queue=asyncio.Queue(),
        )
        await _register_sse_connection(connection)
        
        await emitter.emit_message_sent(
            conversation_id="conv_456",
            message={"id": "msg_123", "content": "Hello"},
        )
        
        event = await asyncio.wait_for(connection.queue.get(), timeout=1)
        assert event["event"] == SSEEventType.MESSAGE_SENT
        assert event["data"]["message"]["id"] == "msg_123"

    @pytest.mark.asyncio
    async def test_emit_to_non_subscribed_conversation(self):
        """Test emitting event to non-subscribed conversation."""
        emitter = SSEEventEmitter()
        SSEEventEmitter.set_instance(emitter)
        
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id=None,
            subscriptions={"conv_other"},  # Not subscribed to conv_456
            queue=asyncio.Queue(),
        )
        await _register_sse_connection(connection)
        
        # Emit to conv_456, but connection is subscribed to conv_other
        await emitter.emit_message_sent(
            conversation_id="conv_456",
            message={"id": "msg_123"},
        )
        
        # Queue should be empty (no event sent)
        assert connection.queue.empty()


# ============================================================================
# SSEConnection Tests
# ============================================================================

class TestSSEConnection:
    """Tests for SSEConnection class."""

    def test_create_sse_connection(self):
        """Test SSE connection creation."""
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id=None,
        )
        
        assert connection.user_id == "user_123"
        assert connection.session_id == "sess_123"
        assert connection.last_event_id is None
        assert connection.subscriptions == set()
        assert isinstance(connection.queue, asyncio.Queue)

    def test_sse_connection_with_subscriptions(self):
        """Test SSE connection with initial subscriptions."""
        connection = SSEConnection(
            user_id="user_123",
            session_id="sess_123",
            last_event_id="event_100",
            subscriptions={"conv_1", "conv_2"},
        )
        
        assert connection.subscriptions == {"conv_1", "conv_2"}
        assert connection.last_event_id == "event_100"


# ============================================================================
# Integration Tests
# ============================================================================

class TestWebSocketAndSSEIntegration:
    """Integration tests for WebSocket and SSE."""

    @pytest.mark.asyncio
    async def test_websocket_broadcast_to_subscribers(self, mock_websocket, mock_session_manager):
        """Test broadcasting message to all subscribers."""
        from starlette.websockets import WebSocketState
        
        # Register two WebSocket connections
        ws1 = AsyncMock()
        ws1.client_state = WebSocketState.CONNECTED
        ws1.send_json = AsyncMock()
        
        ws2 = AsyncMock()
        ws2.client_state = WebSocketState.CONNECTED
        ws2.send_json = AsyncMock()
        
        await ConnectionManager.register_websocket("sess_1", ws1)
        await ConnectionManager.register_websocket("sess_2", ws2)
        
        # Both subscribed to conv_123
        session1 = SessionData(
            session_id="sess_1",
            user_id="user_1",
            connection_id="conn_1",
            state=SessionState.AUTHENTICATED,
            subscriptions={"conv_123"},
        )
        session2 = SessionData(
            session_id="sess_2",
            user_id="user_2",
            connection_id="conn_2",
            state=SessionState.AUTHENTICATED,
            subscriptions={"conv_123"},
        )
        
        mock_session_manager.get_session = AsyncMock(
            side_effect=lambda sid: session1 if sid == "sess_1" else session2 if sid == "sess_2" else None
        )
        
        # Broadcast
        message = {"type": "message", "data": {"id": "msg_123", "content": "Hello"}}
        await ConnectionManager.broadcast_to_conversation(
            "conv_123",
            ["sess_1", "sess_2"],
            message,
        )
        
        # Both should receive the message
        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_sse_and_websocket_independence(self, mock_session_manager, mock_event_bus, mock_auth_service):
        """Test that SSE and WebSocket connections are independent."""
        ws_handler = WebSocketHandler(
            session_manager=mock_session_manager,
            event_bus=mock_event_bus,
            auth_service=mock_auth_service,
        )
        
        # Create mock user
        mock_user = UserCredentials(
            user_id="test_user_123",
            username="testuser",
            password_hash="test_hash",
            is_agent=False,
        )
        mock_auth_service.authenticate_token = AsyncMock(return_value=mock_user)
        
        # Create mock websocket
        mock_ws = AsyncMock()
        mock_ws.client_state = 1
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()
        
        # Handle WebSocket connection
        ws_session_id = await ws_handler.handle_connection(mock_ws, "valid_token")
        assert ws_session_id is not None
        
        # Create SSE connection
        sse_queue = asyncio.Queue()
        sse_connection = SSEConnection(
            user_id="test_user_123",
            session_id="sess_sse_123",
            last_event_id=None,
            subscriptions={"conv_123"},
            queue=sse_queue,
        )
        await _register_sse_connection(sse_connection)
        
        # They should be independent
        assert ConnectionManager._ws_connections.get(ws_session_id) == mock_ws
        assert ConnectionManager._sse_connections.get("sess_sse_123") == sse_queue


# ============================================================================
# Error Handling Tests
# ============================================================================

class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_websocket_handler_invalid_json(self, ws_handler, mock_websocket, mock_session_manager):
        """Test handling invalid JSON message."""
        from starlette.websockets import WebSocketState
        
        await ConnectionManager.register_websocket("sess_test123", mock_websocket)
        
        # The actual JSON parsing would happen before calling handle_message
        # This tests the error response format
        message = {"type": "invalid_type"}
        await ws_handler.handle_message("sess_test123", message)
        
        # Should send error response
        error_call = None
        for call in mock_websocket.send_json.call_args_list:
            if call[0][0].get("type") == "error":
                error_call = call[0][0]
                break
        
        assert error_call is not None
        assert error_call["code"] == ErrorCode.INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_stream_buffer_concurrent_access(self):
        """Test concurrent access to stream buffer."""
        buffer = StreamBuffer(
            message_id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        
        # Simulate concurrent chunk additions
        # "Hello" = 5 bytes at offset 0
        buffer.add_chunk("Hello", 0)
        # " " = 1 byte at offset 5, total now 6
        buffer.add_chunk(" ", 5)
        # "World" with correct offset 6 should succeed
        result = buffer.add_chunk("World", 6)  # Correct offset
        assert result is True
        assert buffer.full_content == "Hello World"
        
        # Now try with wrong offset
        result2 = buffer.add_chunk("!", 12)  # Wrong offset (should be 11)
        assert result2 is False

    @pytest.mark.asyncio
    async def test_cleanup_expired_buffers(self):
        """Test cleanup of expired stream buffers."""
        # Add an expired buffer
        expired_buffer = StreamBuffer(
            message_id="msg_old",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
            timeout=0.1,  # Very short timeout
        )
        expired_buffer.created_at = time.time() - 1  # Already expired
        
        # Add a normal buffer
        normal_buffer = StreamBuffer(
            message_id="msg_new",
            conversation_id="conv_456",
            sender_id="user_789",
            content_type="text",
        )
        
        ConnectionManager.add_stream_buffer("msg_old", expired_buffer)
        ConnectionManager.add_stream_buffer("msg_new", normal_buffer)
        
        # Cleanup
        await ConnectionManager.cleanup_expired_buffers()
        
        # Old buffer should be removed, new one should remain
        assert ConnectionManager.get_stream_buffer("msg_old") is None
        assert ConnectionManager.get_stream_buffer("msg_new") == normal_buffer
