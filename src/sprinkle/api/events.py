"""SSE Handler - Server-Sent Events for real-time notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Set,
    TYPE_CHECKING,
)

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from sprinkle.kernel.session import SessionManager, SessionState
from sprinkle.kernel.auth import AuthService
from sprinkle.plugins.events import PluginEventBus

if TYPE_CHECKING:
    from sprinkle.kernel.message import Message

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# SSE Connection
# ============================================================================

@dataclass
class SSEConnection:
    """SSE 连接状态"""
    user_id: str
    session_id: str
    last_event_id: Optional[str]
    subscriptions: Set[str] = field(default_factory=set)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: float = field(default_factory=time.time)


# ============================================================================
# Event Types
# ============================================================================

class SSEEventType:
    """SSE 事件类型"""
    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    CONVERSATION_UPDATED = "conversation_updated"
    MESSAGE_SENT = "message_sent"


# ============================================================================
# Connection Manager Integration
# ============================================================================

# Global SSE connections: session_id -> SSEConnection
_sse_connections: Dict[str, SSEConnection] = {}


async def _register_sse_connection(connection: SSEConnection):
    """注册 SSE 连接"""
    _sse_connections[connection.session_id] = connection
    # Also register in WebSocket handler's connection manager
    from sprinkle.api.websocket import ConnectionManager
    await ConnectionManager.register_sse(connection.session_id, connection.queue)


async def _unregister_sse_connection(session_id: str) -> Optional[SSEConnection]:
    """注销 SSE 连接"""
    connection = _sse_connections.pop(session_id, None)
    if connection:
        from sprinkle.api.websocket import ConnectionManager
        await ConnectionManager.unregister_sse(session_id)
    return connection


def _get_sse_connection(session_id: str) -> Optional[SSEConnection]:
    """获取 SSE 连接"""
    return _sse_connections.get(session_id)


# ============================================================================
# Event Emitter
# ============================================================================

class SSEEventEmitter:
    """SSE 事件发射器 - 用于向订阅者发送事件"""
    
    _instance: Optional["SSEEventEmitter"] = None
    
    def __init__(self, event_bus: Optional[PluginEventBus] = None):
        self._event_bus = event_bus
        self._event_counter = 0
        self._lock = asyncio.Lock()
    
    @classmethod
    def get_instance(cls) -> "SSEEventEmitter":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def set_instance(cls, emitter: "SSEEventEmitter"):
        """设置单例实例"""
        cls._instance = emitter
    
    async def _generate_event_id(self) -> str:
        """生成递增的事件 ID"""
        async with self._lock:
            self._event_counter += 1
            return str(self._event_counter)
    
    async def emit_member_joined(
        self,
        conversation_id: str,
        user_id: str,
        member: Dict[str, Any],
    ):
        """发送成员加入事件"""
        event_id = await self._generate_event_id()
        event_data = {
            "event": SSEEventType.MEMBER_JOINED,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "member": member,
        }
        await self._broadcast_to_conversation(conversation_id, SSEEventType.MEMBER_JOINED, event_data, event_id)
    
    async def emit_member_left(
        self,
        conversation_id: str,
        user_id: str,
    ):
        """发送成员离开事件"""
        event_id = await self._generate_event_id()
        event_data = {
            "event": SSEEventType.MEMBER_LEFT,
            "conversation_id": conversation_id,
            "user_id": user_id,
        }
        await self._broadcast_to_conversation(conversation_id, SSEEventType.MEMBER_LEFT, event_data, event_id)
    
    async def emit_conversation_updated(
        self,
        conversation_id: str,
        update_type: str,
        data: Dict[str, Any],
    ):
        """发送会话更新事件"""
        event_id = await self._generate_event_id()
        event_data = {
            "event": SSEEventType.CONVERSATION_UPDATED,
            "conversation_id": conversation_id,
            "update_type": update_type,
            "data": data,
        }
        await self._broadcast_to_conversation(conversation_id, SSEEventType.CONVERSATION_UPDATED, event_data, event_id)
    
    async def emit_message_sent(
        self,
        conversation_id: str,
        message: Dict[str, Any],
    ):
        """发送新消息事件"""
        event_id = await self._generate_event_id()
        event_data = {
            "event": SSEEventType.MESSAGE_SENT,
            "conversation_id": conversation_id,
            "message": message,
        }
        await self._broadcast_to_conversation(conversation_id, SSEEventType.MESSAGE_SENT, event_data, event_id)
    
    async def _broadcast_to_conversation(
        self,
        conversation_id: str,
        event_type: str,
        data: Any,
        event_id: str,
    ):
        """广播事件到订阅了指定会话的所有 SSE 连接"""
        for session_id, connection in _sse_connections.items():
            if conversation_id in connection.subscriptions:
                try:
                    await connection.queue.put({
                        "event": event_type,
                        "data": data,
                        "id": event_id,
                    })
                except Exception as e:
                    logger.error(f"Failed to emit event to {session_id}: {e}")


# ============================================================================
# Heartbeat
# ============================================================================

HEARTBEAT_INTERVAL = 30  # 秒


async def sse_heartbeat(queue: asyncio.Queue):
    """SSE 心跳生成器"""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            await queue.put({
                "event": "comment",
                "data": ": heartbeat",
                "id": str(time.time()),
            })
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            break


# ============================================================================
# SSE Endpoint
# ============================================================================

@router.get("/api/v1/events")
async def events_endpoint(
    request: Request,
    Authorization: str = Depends(lambda req: req.headers.get("Authorization", "")),
    Last_Event_ID: Optional[str] = Query(None, alias="Last-Event-ID"),
):
    """SSE 端点 - 服务端推送事件
    
    Headers:
        Authorization: Bearer {token}
        Last-Event-ID: 可选的断线重连 ID
    
    Events:
        - member_joined: 成员加入
        - member_left: 成员离开
        - conversation_updated: 会话信息更新
        - message_sent: 新消息
    
    Heartbeat:
        - 每 30 秒发送一次 comment 行
    """
    # Extract token from Authorization header
    if not Authorization:
        return StreamingResponse(
            iter([f"event: error\ndata: {{\"error\": \"Authorization header required\"}}\n\n"]),
            media_type="text/event-stream",
            status_code=401,
        )
    
    # Remove "Bearer " prefix
    token = Authorization.replace("Bearer ", "").strip()
    
    # Get dependencies
    from sprinkle.api.dependencies import get_auth_service
    from sprinkle.config import settings
    from sprinkle.kernel import SessionManager
    from sprinkle.plugins.events import PluginEventBus
    
    # Authenticate
    auth_service = get_auth_service()
    user = await auth_service.authenticate_token(token)
    if not user:
        return StreamingResponse(
            iter([f"event: error\ndata: {{\"error\": \"Invalid or expired token\"}}\n\n"]),
            media_type="text/event-stream",
            status_code=401,
        )
    
    # Create session for SSE
    import uuid
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    connection_id = f"conn_{uuid.uuid4().hex[:16]}"
    
    # Create session manager
    session_manager = SessionManager(settings.redis)
    await session_manager.initialize()
    
    session = await session_manager.create_session(
        session_id=session_id,
        user_id=user.user_id,
        connection_id=connection_id,
        metadata={"username": user.username, "type": "sse"},
    )
    await session_manager.authenticate(session_id)
    
    # Create SSE connection
    queue: asyncio.Queue = asyncio.Queue()
    connection = SSEConnection(
        user_id=user.user_id,
        session_id=session_id,
        last_event_id=Last_Event_ID,
        subscriptions=set(),
        queue=queue,
    )
    
    # Register connection
    await _register_sse_connection(connection)
    
    # Start heartbeat
    heartbeat_task = asyncio.create_task(sse_heartbeat(queue))
    
    logger.info(f"SSE connected: session={session_id}, user={user.username}, last_event_id={Last_Event_ID}")
    
    # Event generator
    async def event_generator():
        try:
            # Send initial connection event
            yield {
                "event": "connected",
                "data": {"session_id": session_id, "user_id": user.user_id},
                "id": str(time.time()),
            }
            
            # If Last_Event_ID provided, could implement replay logic here
            # For now, just acknowledge
            
            while True:
                try:
                    # Wait for event with timeout
                    event = await asyncio.wait_for(queue.get(), timeout=60)
                    
                    # Format SSE event
                    if event.get("event") == "comment":
                        yield "data: : heartbeat\n\n"
                    else:
                        import json
                        yield f"event: {event.get('event', 'message')}\n"
                        yield f"data: {json.dumps(event.get('data', {}))}\n"
                        yield f"id: {event.get('id', '')}\n"
                        yield "\n"
                        
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield "data: : keepalive\n\n"
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"SSE error: {e}")
        finally:
            # Cleanup
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            
            await _unregister_sse_connection(session_id)
            await session_manager.delete_session(session_id)
            await session_manager.close()
            
            logger.info(f"SSE disconnected: session={session_id}")
    
    return EventSourceResponse(event_generator())


# ============================================================================
# Subscription Management
# ============================================================================

@router.post("/api/v1/events/subscribe")
async def subscribe_to_conversation(
    request: Request,
    conversation_id: str,
    Authorization: str = Depends(lambda req: req.headers.get("Authorization", "")),
):
    """订阅会话事件
    
    Headers:
        Authorization: Bearer {token}
    
    Body:
        conversation_id: 要订阅的会话 ID
    """
    if not Authorization:
        return {"error": "Authorization header required"}, 401
    
    token = Authorization.replace("Bearer ", "").strip()
    
    from sprinkle.api.dependencies import get_auth_service
    auth_service = get_auth_service()
    user = await auth_service.authenticate_token(token)
    if not user:
        return {"error": "Invalid or expired token"}, 401
    
    # Find SSE connection for this user
    connection = None
    for sess_id, conn in _sse_connections.items():
        if conn.user_id == user.user_id:
            connection = conn
            break
    
    if not connection:
        return {"error": "No SSE connection found"}, 400
    
    # Subscribe
    connection.subscriptions.add(conversation_id)
    
    return {
        "status": "subscribed",
        "conversation_id": conversation_id,
    }


@router.post("/api/v1/events/unsubscribe")
async def unsubscribe_from_conversation(
    request: Request,
    conversation_id: str,
    Authorization: str = Depends(lambda req: req.headers.get("Authorization", "")),
):
    """取消订阅会话事件"""
    if not Authorization:
        return {"error": "Authorization header required"}, 401
    
    token = Authorization.replace("Bearer ", "").strip()
    
    from sprinkle.api.dependencies import get_auth_service
    auth_service = get_auth_service()
    user = await auth_service.authenticate_token(token)
    if not user:
        return {"error": "Invalid or expired token"}, 401
    
    # Find SSE connection
    connection = None
    for sess_id, conn in _sse_connections.items():
        if conn.user_id == user.user_id:
            connection = conn
            break
    
    if not connection:
        return {"error": "No SSE connection found"}, 400
    
    # Unsubscribe
    connection.subscriptions.discard(conversation_id)
    
    return {
        "status": "unsubscribed",
        "conversation_id": conversation_id,
    }


# ============================================================================
# Event Bus Integration
# ============================================================================

class SSEEventBusIntegration:
    """将 SSE 事件发射器集成到 Plugin Event Bus"""
    
    def __init__(self, event_bus: PluginEventBus, emitter: SSEEventEmitter):
        self._event_bus = event_bus
        self._emitter = emitter
        self._setup_handlers()
    
    def _setup_handlers(self):
        """设置事件总线处理器"""
        self._event_bus.on("member.joined", self._on_member_joined, "SSE", priority=10)
        self._event_bus.on("member.left", self._on_member_left, "SSE", priority=10)
        self._event_bus.on("conversation.updated", self._on_conversation_updated, "SSE", priority=10)
        self._event_bus.on("message.sent", self._on_message_sent, "SSE", priority=10)
    
    async def _on_member_joined(self, conversation_id: str, user_id: str, member: Dict[str, Any]):
        """处理成员加入事件"""
        await self._emitter.emit_member_joined(conversation_id, user_id, member)
    
    async def _on_member_left(self, conversation_id: str, user_id: str):
        """处理成员离开事件"""
        await self._emitter.emit_member_left(conversation_id, user_id)
    
    async def _on_conversation_updated(self, conversation_id: str, update_type: str, data: Dict[str, Any]):
        """处理会话更新事件"""
        await self._emitter.emit_conversation_updated(conversation_id, update_type, data)
    
    async def _on_message_sent(self, message: Dict[str, Any], sender: Any = None):
        """处理消息发送事件"""
        await self._emitter.emit_message_sent(message.get("conversation_id"), message)


# Global integration instance
_sse_integration: Optional[SSEEventBusIntegration] = None


def setup_sse_integration(event_bus: PluginEventBus):
    """设置 SSE 事件总线集成"""
    global _sse_integration
    emitter = SSEEventEmitter.get_instance()
    _sse_integration = SSEEventBusIntegration(event_bus, emitter)


__all__ = [
    "router",
    "SSEConnection",
    "SSEEventType",
    "SSEEventEmitter",
    "setup_sse_integration",
    "subscribe_to_conversation",
    "unsubscribe_from_conversation",
]
