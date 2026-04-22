"""WebSocket Handler - Real-time bidirectional messaging."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    TYPE_CHECKING,
)

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from starlette.websockets import WebSocketState

from sprinkle.kernel.session import SessionManager, SessionState
from sprinkle.kernel.auth import AuthService
from sprinkle.plugins.events import PluginEventBus

if TYPE_CHECKING:
    from sprinkle.kernel.message import Message

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Error Codes
# ============================================================================

class ErrorCode:
    """WebSocket error codes."""
    INVALID_PARAMS = 1001
    UNAUTHORIZED = 1002
    FORBIDDEN = 1003
    NOT_FOUND = 1004
    RATE_LIMIT = 1005
    INTERNAL_ERROR = 1010


# ============================================================================
# Stream Buffer
# ============================================================================

@dataclass
class StreamBuffer:
    """流式消息缓冲"""
    message_id: str
    conversation_id: str
    sender_id: str
    content_type: str
    chunks: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    timeout: float = 5.0  # 超时时间（秒）
    max_size: int = 10 * 1024 * 1024  # 10MB

    @property
    def total_size(self) -> int:
        """获取已缓冲的总大小（字节）"""
        return sum(len(c.encode('utf-8')) for c in self.chunks)

    @property
    def full_content(self) -> str:
        """获取完整内容"""
        return ''.join(self.chunks)

    def add_chunk(self, content: str, offset: int) -> bool:
        """添加一个 chunk
        Returns True if added successfully, False if offset mismatch or size exceeded
        """
        expected_offset = sum(len(c.encode('utf-8')) for c in self.chunks)
        if offset != expected_offset:
            logger.warning(f"Stream buffer offset mismatch: expected {expected_offset}, got {offset}")
            return False
        
        if self.total_size + len(content.encode('utf-8')) > self.max_size:
            logger.warning(f"Stream buffer exceeded max size: {self.max_size}")
            return False
        
        self.chunks.append(content)
        return True

    def is_expired(self) -> bool:
        """检查是否超时"""
        return time.time() - self.created_at > self.timeout


# ============================================================================
# Connection Manager (Global)
# ============================================================================

class ConnectionManager:
    """全局连接管理器 - 管理所有 WebSocket 和 SSE 连接"""
    
    # WebSocket 连接: session_id -> WebSocket
    _ws_connections: Dict[str, WebSocket] = {}
    
    # SSE 连接: session_id -> asyncio.Queue
    _sse_connections: Dict[str, asyncio.Queue] = {}
    
    # 流式消息缓冲: msg_id -> StreamBuffer
    _stream_buffers: Dict[str, StreamBuffer] = {}
    
    # Lock for thread safety (initialized lazily)
    _lock: Optional[asyncio.Lock] = None
    
    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Get or create the lock."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock
    
    @classmethod
    async def register_websocket(cls, session_id: str, websocket: WebSocket):
        """注册 WebSocket 连接"""
        async with cls._get_lock():
            cls._ws_connections[session_id] = websocket
    
    @classmethod
    async def unregister_websocket(cls, session_id: str) -> Optional[WebSocket]:
        """注销 WebSocket 连接"""
        async with cls._get_lock():
            return cls._ws_connections.pop(session_id, None)
    
    @classmethod
    def get_websocket(cls, session_id: str) -> Optional[WebSocket]:
        """获取 WebSocket 连接"""
        return cls._ws_connections.get(session_id)
    
    @classmethod
    async def register_sse(cls, session_id: str, queue: asyncio.Queue):
        """注册 SSE 连接"""
        async with cls._get_lock():
            cls._sse_connections[session_id] = queue
    
    @classmethod
    async def unregister_sse(cls, session_id: str) -> Optional[asyncio.Queue]:
        """注销 SSE 连接"""
        async with cls._get_lock():
            return cls._sse_connections.pop(session_id, None)
    
    @classmethod
    def get_sse_queue(cls, session_id: str) -> Optional[asyncio.Queue]:
        """获取 SSE 队列"""
        return cls._sse_connections.get(session_id)
    
    @classmethod
    async def send_to_websocket(cls, session_id: str, message: dict) -> bool:
        """发送消息到 WebSocket"""
        websocket = cls._ws_connections.get(session_id)
        if not websocket:
            return False
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(message)
                return True
        except Exception as e:
            logger.error(f"Failed to send to websocket {session_id}: {e}")
        return False
    
    @classmethod
    async def send_to_sse(cls, session_id: str, event_type: str, data: Any, event_id: Optional[str] = None):
        """发送事件到 SSE"""
        queue = cls._sse_connections.get(session_id)
        if not queue:
            return False
        try:
            event = {
                "event": event_type,
                "data": data,
                "id": event_id or str(time.time()),
            }
            await queue.put(event)
            return True
        except Exception as e:
            logger.error(f"Failed to send to SSE {session_id}: {e}")
        return False
    
    @classmethod
    async def broadcast_to_conversation(cls, conversation_id: str, session_ids: List[str], message: dict):
        """广播消息到会话的所有订阅者"""
        for session_id in session_ids:
            await cls.send_to_websocket(session_id, message)
    
    @classmethod
    async def broadcast_event_to_conversation(cls, conversation_id: str, session_ids: List[str], event_type: str, data: Any):
        """广播事件到会话的所有订阅者"""
        event_id = str(time.time())
        for session_id in session_ids:
            await cls.send_to_sse(session_id, event_type, data, event_id)
    
    @classmethod
    def add_stream_buffer(cls, msg_id: str, buffer: StreamBuffer):
        """添加流式消息缓冲"""
        cls._stream_buffers[msg_id] = buffer
    
    @classmethod
    def get_stream_buffer(cls, msg_id: str) -> Optional[StreamBuffer]:
        """获取流式消息缓冲"""
        return cls._stream_buffers.get(msg_id)
    
    @classmethod
    def remove_stream_buffer(cls, msg_id: str) -> Optional[StreamBuffer]:
        """移除流式消息缓冲"""
        return cls._stream_buffers.pop(msg_id, None)
    
    @classmethod
    async def cleanup_expired_buffers(cls):
        """清理过期的流式消息缓冲"""
        expired_ids = [
            msg_id for msg_id, buffer in cls._stream_buffers.items()
            if buffer.is_expired()
        ]
        for msg_id in expired_ids:
            buffer = cls._stream_buffers.pop(msg_id, None)
            if buffer:
                logger.info(f"Cleaned up expired stream buffer: {msg_id}")


# ============================================================================
# WebSocket Handler
# ============================================================================

class WebSocketHandler:
    """WebSocket 消息处理器"""
    
    def __init__(
        self,
        session_manager: SessionManager,
        event_bus: PluginEventBus,
        auth_service: AuthService,
    ):
        self._session_manager = session_manager
        self._event_bus = event_bus
        self._auth_service = auth_service
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """启动处理器"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """停止处理器"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def _cleanup_loop(self):
        """定期清理过期的流式消息缓冲"""
        while True:
            try:
                await asyncio.sleep(1)
                await ConnectionManager.cleanup_expired_buffers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    async def handle_connection(self, websocket: WebSocket, token: str) -> Optional[str]:
        """处理 WebSocket 连接
        Returns session_id if successful, None otherwise
        """
        # Verify token
        user = await self._auth_service.authenticate_token(token)
        if not user:
            await self._send_error(websocket, ErrorCode.UNAUTHORIZED, "Invalid or expired token")
            await websocket.close(code=4001)
            return None
        
        # Generate session and connection IDs
        import uuid
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        connection_id = f"conn_{uuid.uuid4().hex[:16]}"
        
        # Create session
        session = await self._session_manager.create_session(
            session_id=session_id,
            user_id=user.user_id,
            connection_id=connection_id,
            metadata={"username": user.username},
        )
        
        # Set authenticated state
        await self._session_manager.authenticate(session_id)
        
        # Register connection
        await ConnectionManager.register_websocket(session_id, websocket)
        
        # Start heartbeat
        await self._session_manager.start_heartbeat(session_id)
        
        # Set up disconnect handler
        session.on_disconnect = self._on_disconnect
        
        logger.info(f"WebSocket connected: session={session_id}, user={user.username}")
        
        return session_id
    
    async def handle_apikey_connection(
        self,
        websocket: WebSocket,
        key_id: str,
        signature: str,
        timestamp: int,
        nonce: str,
    ) -> Optional[str]:
        """处理 API Key 认证的 WebSocket 连接
        
        Args:
            websocket: WebSocket connection
            key_id: API Key ID
            signature: HMAC-SHA256 signature
            timestamp: Unix timestamp
            nonce: Random nonce
            
        Returns:
            session_id if successful, None otherwise
        """
        # Authenticate using API Key
        from sprinkle.services.agent_key_service import AgentKeyService
        from sprinkle.storage.database import get_async_session
        
        async with get_async_session() as db:
            service = AgentKeyService(db)
            result = await service.authenticate_hmac(key_id, signature, timestamp, nonce)
            
            if not result.success:
                await self._send_error(websocket, ErrorCode.UNAUTHORIZED, result.message)
                await websocket.close(code=4001)
                return None
            
            user = result.user
            api_key = result.api_key
        
        # Generate session and connection IDs
        import uuid
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        connection_id = f"conn_{uuid.uuid4().hex[:16]}"
        
        # Create session
        session = await self._session_manager.create_session(
            session_id=session_id,
            user_id=user.id,
            connection_id=connection_id,
            metadata={
                "username": user.username,
                "auth_type": "apikey",
                "api_key_name": api_key.name if api_key else None,
            },
        )
        
        # Set authenticated state
        await self._session_manager.authenticate(session_id)
        
        # Register connection
        await ConnectionManager.register_websocket(session_id, websocket)
        
        # Start heartbeat
        await self._session_manager.start_heartbeat(session_id)
        
        # Set up disconnect handler
        session.on_disconnect = self._on_disconnect
        
        logger.info(f"WebSocket connected (API Key): session={session_id}, user={user.username}, key={key_id}")
        
        return session_id
    
    async def _on_disconnect(self, session):
        """会话断开处理"""
        await ConnectionManager.unregister_websocket(session.session_id)
        logger.info(f"WebSocket disconnected: session={session.session_id}")
    
    async def handle_disconnect(self, session_id: str):
        """处理 WebSocket 断开"""
        # Stop heartbeat
        await self._session_manager.stop_heartbeat(session_id)
        
        # Delete session
        await self._session_manager.delete_session(session_id)
        
        # Unregister connection
        await ConnectionManager.unregister_websocket(session_id)
        
        # Clean up stream buffers
        buffers_to_remove = [
            msg_id for msg_id, buffer in ConnectionManager._stream_buffers.items()
            if buffer.sender_id == session_id
        ]
        for msg_id in buffers_to_remove:
            ConnectionManager.remove_stream_buffer(msg_id)
        
        logger.info(f"WebSocket session cleaned up: {session_id}")
    
    async def handle_message(self, session_id: str, data: dict):
        """处理客户端消息"""
        msg_type = data.get("type")
        msg_id = data.get("id")
        params = data.get("params", {})
        
        session = await self._session_manager.get_session(session_id)
        if not session or session.state != SessionState.AUTHENTICATED:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.UNAUTHORIZED, "Not authenticated")
            return
        
        if msg_type == "subscribe":
            await self._handle_subscribe(session_id, params)
        elif msg_type == "unsubscribe":
            await self._handle_unsubscribe(session_id, params)
        elif msg_type == "message":
            await self._handle_message_send(session_id, msg_id, params)
        elif msg_type == "message.start":
            await self._handle_stream_start(session_id, msg_id, params)
        elif msg_type == "message.chunk":
            await self._handle_stream_chunk(session_id, msg_id, params)
        elif msg_type == "message.end":
            await self._handle_stream_end(session_id, msg_id, params)
        elif msg_type == "message.cancel":
            await self._handle_stream_cancel(session_id, msg_id)
        elif msg_type == "ping":
            await self._handle_ping(session_id)
        else:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, f"Unknown message type: {msg_type}")
    
    async def _handle_subscribe(self, session_id: str, params: dict):
        """处理订阅请求
        
        Supports optional mode parameter for subscription mode:
        - "direct": Receive all messages in conversation
        - "mention_only": Only receive when mentioned (default)
        - "unlimited": Receive all events (same as direct)
        - "event_based": Only receive specific events
        
        For event_based mode, also provide "events" list parameter.
        """
        conversation_id = params.get("conversation_id")
        if not conversation_id:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, "conversation_id required")
            return
        
        # Get session to get user_id
        session = await self._session_manager.get_session(session_id)
        if not session:
            return
        
        user_id = session.user_id
        
        # Get subscription mode (default: mention_only for backward compatibility)
        mode_str = params.get("mode", "mention_only")
        events = params.get("events", None)
        
        # Map mode string to SubscriptionMode enum
        from sprinkle.push.subscription import SubscriptionMode
        from sprinkle.push.events import PushEvent
        
        mode_map = {
            "direct": SubscriptionMode.DIRECT,
            "mention_only": SubscriptionMode.MENTION_ONLY,
            "unlimited": SubscriptionMode.UNLIMITED,
            "event_based": SubscriptionMode.EVENT_BASED,
        }
        mode = mode_map.get(mode_str, SubscriptionMode.MENTION_ONLY)
        
        # Parse events list if provided
        subscribed_events = None
        if mode == SubscriptionMode.EVENT_BASED and events:
            subscribed_events = []
            for e in events:
                try:
                    subscribed_events.append(PushEvent(e))
                except ValueError:
                    logger.warning(f"Unknown push event type in subscribe: {e}")
        
        # Also maintain session-based subscription for real-time WebSocket delivery
        success = await self._session_manager.subscribe(session_id, conversation_id)
        
        # Additionally, persist agent subscription to database for push routing
        try:
            from sprinkle.storage.database import get_async_session
            from sprinkle.push.subscription import SubscriptionService
            
            async with get_async_session() as db:
                sub_service = SubscriptionService(db)
                await sub_service.subscribe(
                    agent_id=user_id,
                    conversation_id=conversation_id,
                    mode=mode,
                    events=subscribed_events,
                )
        except ImportError:
            logger.warning("Push subscription service not available")
        except Exception as e:
            logger.error(f"Failed to persist subscription: {e}")
        
        websocket = ConnectionManager.get_websocket(session_id)
        if websocket:
            await websocket.send_json({
                "type": "ack",
                "id": None,
                "params": {
                    "action": "subscribe",
                    "conversation_id": conversation_id,
                    "status": "subscribed" if success else "error",
                    "mode": mode_str,
                }
            })
        
        logger.debug(f"Subscribe: session={session_id}, conversation={conversation_id}, mode={mode_str}")
    
    async def _handle_unsubscribe(self, session_id: str, params: dict):
        """处理取消订阅请求
        
        This removes both the session-based subscription (for real-time
        WebSocket delivery) and the database-backed agent subscription
        (for push notification routing).
        """
        conversation_id = params.get("conversation_id")
        if not conversation_id:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, "conversation_id required")
            return
        
        # Get session to get user_id
        session = await self._session_manager.get_session(session_id)
        if not session:
            return
        
        user_id = session.user_id
        
        # Remove session-based subscription for real-time WebSocket delivery
        success = await self._session_manager.unsubscribe(session_id, conversation_id)
        
        # Also remove database-backed agent subscription for push routing
        try:
            from sprinkle.storage.database import get_async_session
            from sprinkle.push.subscription import SubscriptionService
            
            async with get_async_session() as db:
                sub_service = SubscriptionService(db)
                await sub_service.unsubscribe(
                    agent_id=user_id,
                    conversation_id=conversation_id,
                )
        except ImportError:
            logger.warning("Push subscription service not available")
        except Exception as e:
            logger.error(f"Failed to remove subscription: {e}")
        
        websocket = ConnectionManager.get_websocket(session_id)
        if websocket:
            await websocket.send_json({
                "type": "ack",
                "id": None,
                "params": {
                    "action": "unsubscribe",
                    "conversation_id": conversation_id,
                    "status": "unsubscribed" if success else "error",
                }
            })
        
        logger.debug(f"Unsubscribe: session={session_id}, conversation={conversation_id}")
    
    async def _handle_message_send(self, session_id: str, msg_id: Optional[str], params: dict):
        """处理普通消息发送"""
        conversation_id = params.get("conversation_id")
        content = params.get("content", "")
        content_type = params.get("content_type", "text")
        mentions = params.get("mentions", [])
        reply_to = params.get("reply_to")
        
        if not conversation_id:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, "conversation_id required")
            return
        
        session = await self._session_manager.get_session(session_id)
        if not session:
            return
        
        # Create message (simplified - in production would persist to DB)
        import uuid
        message_id = str(uuid.uuid4())
        
        message = {
            "id": message_id,
            "conversation_id": conversation_id,
            "sender_id": session.user_id,
            "content": content,
            "content_type": content_type,
            "mentions": mentions,
            "reply_to": reply_to,
            "created_at": time.time(),
        }
        
        # Emit event
        await self._event_bus.emit_async("message.sent", message, sender=self)
        
        # Get subscribed sessions and broadcast
        subscribed_sessions = await self._get_subscribed_sessions(conversation_id)
        
        # Send ack to sender
        websocket = ConnectionManager.get_websocket(session_id)
        if websocket:
            await websocket.send_json({
                "type": "ack",
                "id": msg_id,
                "params": {
                    "status": "sent",
                    "message_id": message_id,
                }
            })
        
        # Broadcast to all subscribers
        for sess_id in subscribed_sessions:
            if sess_id != session_id:  # Don't send to self again
                await ConnectionManager.send_to_websocket(sess_id, {
                    "type": "message",
                    "data": message,
                })
        
        logger.debug(f"Message sent: {message_id} to {len(subscribed_sessions)} subscribers")
    
    async def _handle_stream_start(self, session_id: str, msg_id: str, params: dict):
        """处理流式消息开始"""
        conversation_id = params.get("conversation_id")
        content_type = params.get("content_type", "text")
        mentions = params.get("mentions", [])
        reply_to = params.get("reply_to")
        
        if not conversation_id or not msg_id:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, "conversation_id and id required")
            return
        
        session = await self._session_manager.get_session(session_id)
        if not session:
            return
        
        # Create stream buffer
        # Note: sender_id stores session_id for consistency in permission checks
        buffer = StreamBuffer(
            message_id=msg_id,
            conversation_id=conversation_id,
            sender_id=session_id,  # Use session_id for consistent comparison
            content_type=content_type,
            mentions=mentions,
            reply_to=reply_to,
        )
        ConnectionManager.add_stream_buffer(msg_id, buffer)
        
        logger.debug(f"Stream start: {msg_id}")
    
    async def _handle_stream_chunk(self, session_id: str, msg_id: str, params: dict):
        """处理流式消息片段"""
        content = params.get("content", "")
        offset = params.get("offset", 0)
        
        buffer = ConnectionManager.get_stream_buffer(msg_id)
        if not buffer:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, f"Stream not found: {msg_id}")
            return
        
        if buffer.sender_id != session_id:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.FORBIDDEN, "Not the sender")
            return
        
        if not buffer.add_chunk(content, offset):
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, "Chunk offset mismatch or size exceeded")
            ConnectionManager.remove_stream_buffer(msg_id)
            return
        
        logger.debug(f"Stream chunk: {msg_id}, offset={offset}, size={len(content)}")
    
    async def _handle_stream_end(self, session_id: str, msg_id: str, params: dict):
        """处理流式消息结束"""
        is_complete = params.get("is_complete", True)
        
        buffer = ConnectionManager.get_stream_buffer(msg_id)
        if not buffer:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.INVALID_PARAMS, f"Stream not found: {msg_id}")
            return
        
        if buffer.sender_id != session_id:
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await self._send_error(websocket, ErrorCode.FORBIDDEN, "Not the sender")
            return
        
        # Remove buffer
        ConnectionManager.remove_stream_buffer(msg_id)
        
        if is_complete:
            # Create and broadcast the complete message
            import uuid
            message_id = str(uuid.uuid4())
            
            message = {
                "id": message_id,
                "conversation_id": buffer.conversation_id,
                "sender_id": buffer.sender_id,
                "content": buffer.full_content,
                "content_type": buffer.content_type,
                "mentions": buffer.mentions,
                "reply_to": buffer.reply_to,
                "created_at": time.time(),
            }
            
            # Emit event
            await self._event_bus.emit_async("message.sent", message, sender=self)
            
            # Get subscribed sessions and broadcast
            subscribed_sessions = await self._get_subscribed_sessions(buffer.conversation_id)
            
            # Send ack to sender
            websocket = ConnectionManager.get_websocket(session_id)
            if websocket:
                await websocket.send_json({
                    "type": "ack",
                    "id": msg_id,
                    "params": {
                        "status": "sent",
                        "message_id": message_id,
                    }
                })
            
            # Broadcast to all subscribers
            for sess_id in subscribed_sessions:
                if sess_id != session_id:
                    await ConnectionManager.send_to_websocket(sess_id, {
                        "type": "message",
                        "data": message,
                    })
            
            logger.debug(f"Stream end: {msg_id} -> message {message_id}")
        else:
            logger.debug(f"Stream cancelled: {msg_id}")
    
    async def _handle_stream_cancel(self, session_id: str, msg_id: str):
        """处理流式消息取消"""
        buffer = ConnectionManager.get_stream_buffer(msg_id)
        if buffer and buffer.sender_id == session_id:
            ConnectionManager.remove_stream_buffer(msg_id)
            logger.debug(f"Stream cancelled: {msg_id}")
    
    async def _handle_ping(self, session_id: str):
        """处理心跳"""
        websocket = ConnectionManager.get_websocket(session_id)
        if websocket:
            await websocket.send_json({"type": "pong"})
        
        # Update heartbeat
        await self._session_manager.receive_pong(session_id)
    
    async def _get_subscribed_sessions(self, conversation_id: str) -> List[str]:
        """获取订阅了指定会话的所有 session_id"""
        # In a real implementation, this would query the session manager
        # For now, we iterate through all connections
        sessions = []
        for session_id in list(ConnectionManager._ws_connections.keys()):
            session = await self._session_manager.get_session(session_id)
            if session and conversation_id in session.subscriptions:
                sessions.append(session_id)
        return sessions
    
    async def _send_error(self, websocket: WebSocket, code: int, message: str):
        """发送错误响应"""
        try:
            await websocket.send_json({
                "type": "error",
                "code": code,
                "message": message,
            })
        except Exception as e:
            logger.error(f"Failed to send error: {e}")


# ============================================================================
# Dependency Injection
# ============================================================================

# Global handler instance
_ws_handler: Optional[WebSocketHandler] = None


def get_ws_handler() -> WebSocketHandler:
    """Get or create WebSocket handler instance"""
    global _ws_handler
    if _ws_handler is None:
        from sprinkle.config import settings
        from sprinkle.kernel import SessionManager
        from sprinkle.api.dependencies import get_auth_service
        
        # Create event bus - use PluginEventBus directly
        event_bus = PluginEventBus()
        
        session_manager = SessionManager(settings.redis)
        _ws_handler = WebSocketHandler(
            session_manager=session_manager,
            event_bus=event_bus,
            auth_service=get_auth_service(),
        )
    return _ws_handler


# ============================================================================
# WebSocket Endpoint
# ============================================================================

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    # JWT authentication
    token: Optional[str] = Query(None),
    # API Key authentication (for agents)
    key_id: Optional[str] = Query(None),
    sig: Optional[str] = Query(None),
    ts: Optional[str] = Query(None),
    nonce: Optional[str] = Query(None),
):
    """WebSocket endpoint
    
    Query Parameters (JWT mode):
        token: JWT access token for authentication
    
    Query Parameters (API Key mode - for agents):
        key_id: API Key ID
        sig: HMAC-SHA256 signature
        ts: Unix timestamp
        nonce: Random nonce
    
    Protocol:
        - Client -> Server: JSON messages with 'type' field
        - Server -> Client: JSON messages
    """
    await websocket.accept()
    
    handler = get_ws_handler()
    
    # Determine authentication mode
    if key_id and sig and ts and nonce:
        # API Key authentication mode
        try:
            timestamp = int(ts)
        except (ValueError, TypeError):
            await websocket.send_json({
                "type": "error",
                "code": ErrorCode.INVALID_PARAMS,
                "message": "Invalid timestamp",
            })
            await websocket.close(code=4001)
            return
        
        session_id = await handler.handle_apikey_connection(
            websocket, key_id, sig, timestamp, nonce
        )
    elif token:
        # JWT authentication mode
        session_id = await handler.handle_connection(websocket, token)
    else:
        await websocket.send_json({
            "type": "error",
            "code": ErrorCode.UNAUTHORIZED,
            "message": "Authentication required (token or key_id+sig+ts+nonce)",
        })
        await websocket.close(code=4001)
        return
    
    if not session_id:
        return
    
    try:
        while True:
            # Receive message
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "code": ErrorCode.INVALID_PARAMS,
                    "message": "Invalid JSON",
                })
                continue
            
            # Handle message
            await handler.handle_message(session_id, message)
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await handler.handle_disconnect(session_id)


__all__ = [
    "router",
    "WebSocketHandler",
    "ConnectionManager",
    "StreamBuffer",
    "ErrorCode",
]
