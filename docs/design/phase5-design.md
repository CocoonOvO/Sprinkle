# Phase 5: WebSocket & SSE 设计文档

## 1. 概述

### 1.1 目标
实现 Sprinkle 的实时通信能力，包括 WebSocket 消息通道和 SSE 事件通知。

### 1.2 范围
- WebSocket Handler：双向消息传输
- SSE Handler：服务端推送事件通知
- Session 集成：与 Session Manager 集成实现连接管理

### 1.3 与其他模块的关系

| 依赖模块 | 用途 |
|----------|------|
| kernel/session.py | SessionManager - 连接和会话管理 |
| plugins/events.py | PluginEventBus - 事件分发 |
| kernel/auth.py | AuthService - Token 认证 |
| api/auth.py | 注册/登录接口 |

## 2. 接口设计

### 2.1 WebSocket Handler (api/websocket.py)

#### 连接建立
```
WSS /ws?token=xxx
```

**认证流程**：
1. 客户端通过 query parameter 传递 token
2. 服务端验证 token，获取 user_id
3. 创建 SessionData，会话状态设为 AUTHENTICATED
4. 启动心跳

**分帧协议（JSON）**：

客户端 → 服务端：

| type | 说明 | params |
|------|------|--------|
| subscribe | 订阅会话 | conversation_id |
| unsubscribe | 取消订阅 | conversation_id |
| message | 普通消息 | conversation_id, content, content_type, mentions, reply_to |
| message.start | 流式消息开始 | conversation_id, content_type, mentions, reply_to |
| message.chunk | 流式消息片段 | id(引用start的id), content, offset |
| message.end | 流式消息结束 | id, is_complete |
| message.cancel | 取消流式消息 | id |
| ping | 心跳 | - |

服务端 → 客户端：

| type | 说明 | data |
|------|------|------|
| message | 新消息 | Message 对象 |
| ack | 消息确认 | id, status(sent/error), message_id, error |
| event | 事件通知 | event_type, data |
| error | 错误响应 | code, message |
| pong | 心跳响应 | - |

**错误码**：
- 1001: INVALID_PARAMS
- 1002: UNAUTHORIZED
- 1003: FORBIDDEN
- 1004: NOT_FOUND
- 1005: RATE_LIMIT
- 1010: INTERNAL_ERROR

#### 流式消息处理

```
Client: message.start (id="uuid1", params={conversation_id, content_type})
Client: message.chunk (id="uuid1", params={content: "Hello", offset: 0})
Client: message.chunk (id="uuid1", params={content: " World", offset: 5})
Client: message.end (id="uuid1", params={is_complete: true})
Server: ack (id="uuid1", status="sent", message_id="uuid2")
```

**Buffer 管理**：
- chunk_size: 最大 64KB
- max_buffer: 10MB
- timeout: 5s（从 start 开始计时）

### 2.2 SSE Handler (api/events.py)

#### 端点
```
GET /api/v1/events
Headers:
  - Authorization: Bearer {token}
  - Accept: text/event-stream
  - Last-Event-ID: {id}（可选）
```

**事件格式**：
```
event: {event_type}
data: {json_data}
id: {event_id}

```

**事件类型**：
| event_type | 说明 | data |
|------------|------|------|
| member_joined | 成员加入 | conversation_id, user_id, member |
| member_left | 成员离开 | conversation_id, user_id |
| conversation_updated | 会话更新 | conversation_id, update_type, data |
| message_sent | 新消息 | Message 对象 |

**心跳**：
- 每 30 秒发送一次 comment 行：`: heartbeat`

**断线重连**：
- 客户端记录 Last-Event-ID
- 重连时携带 Last-Event-ID
- 服务端从该 ID 之后开始推送

## 3. 数据结构

### 3.1 StreamBuffer
```python
@dataclass
class StreamBuffer:
    """流式消息缓冲"""
    message_id: str
    conversation_id: str
    sender_id: str
    content_type: str
    chunks: List[str]  # 累积的消息片段
    mentions: List[str]
    reply_to: Optional[str]
    created_at: float
    timeout: float = 5.0  # 超时时间
```

### 3.2 SSEConnection
```python
@dataclass
class SSEConnection:
    """SSE 连接状态"""
    user_id: str
    session_id: str
    last_event_id: Optional[str]
    subscriptions: Set[str]  # 订阅的 conversation_id
    queue: asyncio.Queue
```

## 4. 实现细节

### 4.1 WebSocket Handler

**类结构**：
```python
class WebSocketHandler:
    """WebSocket 消息处理器"""
    
    def __init__(
        self,
        session_manager: SessionManager,
        event_bus: PluginEventBus,
        auth_service: AuthService,
    ):
        ...
    
    async def handle_connection(self, websocket: WebSocket, token: str):
        """处理 WebSocket 连接"""
        ...
    
    async def handle_message(self, session_id: str, data: dict):
        """处理客户端消息"""
        ...
    
    async def handle_stream_start(self, session_id: str, msg_id: str, params: dict):
        """处理流式消息开始"""
        ...
    
    async def handle_stream_chunk(self, session_id: str, msg_id: str, content: str, offset: int):
        """处理流式消息片段"""
        ...
    
    async def handle_stream_end(self, session_id: str, msg_id: str, is_complete: bool):
        """处理流式消息结束"""
        ...
    
    async def broadcast_message(self, message: dict):
        """广播消息到所有订阅者"""
        ...
```

**消息分发流程**：
1. 收到消息或流式消息完成
2. 通过 event_bus 发送 `message.received` 事件
3. 等待插件处理
4. 调用 SessionManager 获取所有订阅者
5. 通过 WebSocket 发送 ack
6. 广播消息给所有订阅者（包括发送者）

### 4.2 SSE Handler

**类结构**：
```python
class SSEHandler:
    """SSE 事件处理器"""
    
    def __init__(
        self,
        session_manager: SessionManager,
        event_bus: PluginEventBus,
    ):
        ...
    
    async def events_endpoint(
        self,
        token: str,
        last_event_id: Optional[str],
    ) -> AsyncGenerator[dict, None]:
        """SSE 端点"""
        ...
    
    async def subscribe(self, session_id: str, conversation_id: str):
        """订阅会话事件"""
        ...
    
    async def emit_event(self, event_type: str, data: dict):
        """发送事件到所有订阅者"""
        ...
```

### 4.3 全局连接管理器

```python
class ConnectionManager:
    """全局连接管理器"""
    
    # WebSocket 连接: session_id -> WebSocket
    _ws_connections: Dict[str, WebSocket]
    
    # SSE 连接: session_id -> SSEConnection
    _sse_connections: Dict[str, SSEConnection]
    
    # 流式消息缓冲: msg_id -> StreamBuffer
    _stream_buffers: Dict[str, StreamBuffer]
    
    @classmethod
    async def broadcast_to_conversation(cls, conversation_id: str, message: dict):
        """广播消息到会话的所有订阅者"""
        ...
    
    @classmethod
    async def send_to_session(cls, session_id: str, message: dict):
        """发送消息到指定会话"""
        ...
    
    @classmethod
    def register_websocket(cls, session_id: str, websocket: WebSocket):
        ...
    
    @classmethod
    def unregister_websocket(cls, session_id: str):
        ...
```

## 5. Session 集成

### 5.1 WebSocket 连接与会话管理
1. WebSocket 连接建立 → SessionManager.create_session
2. 认证成功 → SessionManager.authenticate
3. 订阅会话 → SessionManager.subscribe
4. 心跳 → SessionManager.start_heartbeat
5. 断开连接 → SessionManager.delete_session

### 5.2 SSE 连接与会话管理
1. SSE 请求到达 → SessionManager.create_session
2. 认证成功 → SessionManager.authenticate
3. 订阅会话 → SessionManager.subscribe
4. SSE 不使用心跳（通过 keep-alive）

### 5.3 消息分发
```python
async def dispatch_message(message: Message):
    """分发消息到所有订阅者"""
    # 1. 存储消息到数据库
    # 2. 通过 event_bus 发送事件
    await event_bus.emit_async("message.sent", message)
    
    # 3. 获取所有订阅者
    sessions = await session_manager.get_subscribed_sessions(message.conversation_id)
    
    # 4. 广播消息
    for session in sessions:
        await connection_manager.send_to_session(
            session.session_id,
            {"type": "message", "data": message.to_dict()}
        )
```

## 6. 测试策略

### 6.1 WebSocket 测试
1. 连接认证测试
2. 订阅/取消订阅测试
3. 普通消息发送/接收测试
4. 流式消息测试（start/chunk/end）
5. 流式消息超时测试
6. 心跳测试
7. 错误响应测试

### 6.2 SSE 测试
1. 连接认证测试
2. 事件接收测试
3. Last-Event-ID 断线重连测试
4. 心跳测试

### 6.3 Session 集成测试
1. Session 创建/删除测试
2. 订阅/取消订阅测试
3. 消息分发测试

## 7. 文件清单

| 文件 | 说明 |
|------|------|
| api/websocket.py | WebSocket Handler |
| api/events.py | SSE Handler |
| tests/test_phase5_websocket.py | Phase 5 测试 |

## 8. 依赖注入

在 `api/__init__.py` 中注册新的路由：
```python
from sprinkle.api.websocket import router as websocket_router
from sprinkle.api.events import router as events_router

api_router.include_router(websocket_router, tags=["websocket"])
api_router.include_router(events_router, tags=["events"])
```

---

*设计文档由司康编写~🍪*
