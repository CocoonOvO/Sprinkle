# Sprinkle 架构文档

> 虚拟聊天软件后端系统  
> 版本：v0.2.0（审计修订版）  
> 更新日期：2026-04-15

---

## 1. 项目定位

**Sprinkle** 是一个轻量级的**多 Agent 协同工作平台**，类似简化版飞书。

### 核心特性

- 群聊为主，支持私聊
- Agent 与人类用户身份平等
- 全功能插件化架构
- 消息流式传输与处理
- 分层消息存储

### 设计目标

1. **轻量灵活**：单机部署，简单运维
2. **插件优先**：所有功能均以插件形式实现
3. **流式优先**：支持流式消息处理
4. **多端适配**：通过插件接入不同客户端/Agent

---

## 2. 技术选型

### 核心技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **语言** | Python 3.11+ | 高效开发，生态丰富 |
| **Web 框架** | FastAPI | 异步高性能，内置 WebSocket 支持 |
| **ORM** | SQLAlchemy 2.0 | 数据库抽象层，支持多数据库 |
| **数据库** | PostgreSQL（默认） | 关系型存储，JSONB 支持 |
| **缓存/消息队列** | Redis | 分层存储、实时消息队列 |
| **通信协议** | WebSocket + SSE | 双向流式传输 |

### 插件系统

| 项目 | 选择 | 说明 |
|------|------|------|
| **隔离级别** | 共享进程 | 简单直接，插件通过事件总线通信 |
| **热拔插** | 准热拔插（importlib + 状态重置） | 插件通过 importlib 重新加载模块，依赖状态清理机制 |
| **插件通信** | 事件总线 | 插件通过事件总线进行通信 |

> ⚠️ **风险提示**：共享进程模式下，插件崩溃可能影响主进程。建议插件实现超时保护和异常隔离。

### 流式消息处理

```
[Agent] → (流式接收) → [Buffer] → (完整后) → [分发] → [所有接收方]
              ↑                    ↑
        边接收边缓存         完整判断依据：
        chunk_size 最大 64KB    - 收到 EOS 标记
        - 超过 chunk_size      - 超时 5s 未收到新数据
```

---

## 3. 系统架构

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Sprinkle                             │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                    API Layer (FastAPI)                   │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │ │
│  │  │  REST API   │  │  WebSocket  │  │    SSE      │     │ │
│  │  │  (管理接口)  │  │  (消息通道)  │  │  (事件通知)  │     │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘     │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                  Core Kernel (核心)                      │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │ │
│  │  │ Session  │  │ Message  │  │  Event   │              │ │
│  │  │ Manager  │  │  Router  │  │   Bus    │              │ │
│  │  └──────────┘  └──────────┘  └──────────┘              │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │ │
│  │  │  Auth    │  │ Conversation│ │ Plugin   │              │ │
│  │  │ Service  │  │  Service  │  │  Manager  │              │ │
│  │  └──────────┘  └──────────┘  └──────────┘              │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │               Plugin Container (插件容器)                │ │
│  │  [Message Plugin] [AI Adapter] [File Plugin] ...       │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                  Infrastructure Layer                    │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │ │
│  │  │PostgreSQL│  │  Redis   │  │   File   │              │ │
│  │  │ (持久化)  │  │ (缓存/队列)│  │ Storage  │              │ │
│  │  └──────────┘  └──────────┘  └──────────┘              │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 核心模块

#### Session Manager（会话管理器）

负责管理所有 WebSocket 连接和用户会话。

```
Session Manager
├── Connection Pool（连接池）
├── Session Store（会话存储，Redis + 内存）
├── Heartbeat（心跳检测）
│   ├── ping_interval: 30s（发送间隔）
│   ├── ping_timeout: 10s（超时断开）
│   └── max_retry: 3（最大重试次数）
└── Reconnection Handler（断线重连）
    ├── 会话恢复（从 Redis 恢复用户会话状态）
    └── 消息补发（离线期间的消息通过 Redis 队列重发）
```

> ⚠️ **会话持久化**：WebSocket 连接本身不持久化，但用户订阅关系、会话列表存储在 Redis 中，重启后可恢复。

#### Message Router（消息路由）

负责消息的接收、缓存、路由和分发。

```
Message Router
├── Stream Buffer（流式消息缓冲）
│   ├── chunk_size: 64KB（最大单次接收）
│   ├── max_buffer: 10MB（单消息最大，超过则截断并返回 error）
│   ├── timeout: 5s（从收到首个 chunk 开始计时，超时则终止接收）
│   └── complete_trigger: EOS 标记 | 超时 5s
├── Message Queue（消息队列，Redis）
└── Dispatcher（分发器）
```

> ⚠️ **超时说明**：计时从接收到第一个 chunk 开始，5s 内未收到完整消息则终止接收并清理 buffer。

#### Event Bus（事件总线）

插件间通信的核心，支持同步/异步事件分发。

```
Event Bus
├── Event Registry（事件注册表）
├── Sync Dispatcher（同步分发器）
│   └── timeout: 5s（单事件处理超时）
├── Async Dispatcher（异步分发器）
├── Loop Detector（循环检测）
│   └── max_depth: 10（事件链最大深度）
└── Error Handler（错误处理）
```

> ⚠️ **循环防护**：事件链深度超过 10 层时自动中断，防止插件间循环触发导致死循环。

### 3.3 数据流

```
1. [Client] --WebSocket--> [API Layer]
2. [API Layer] --> [Session Manager] --> [Event Bus]
3. [Event Bus] --> [Plugin Chain]（按优先级执行，含超时 5s 保护）
4. [Plugin Chain] --> [Message Router]
5. [Message Router] --> [Stream Buffer] --> [完整消息]
6. [完整消息] --> [Session Manager] --> [所有订阅者]
```

---

## 4. 插件系统设计

### 4.1 插件架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Plugin Architecture                       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                    Plugin Interface                      │ │
│  │  - name: str                                           │ │
│  │  - version: str                                        │ │
│  │  - dependencies: List[str]                             │ │
│  │  - priority: int (0-100, 越高越先执行)                   │ │
│  │  - on_load()                                           │ │
│  │  - on_message(message) -> Optional[Message]            │ │
│  │  - on_before_send(message) -> Message                   │ │
│  │  - on_unload()                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                              │                               │
│         ┌────────────────────┼────────────────────┐        │
│         ▼                    ▼                    ▼        │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐  │
│  │   Message   │      │    AI      │      │    File    │  │
│  │   Plugin    │      │  Adapter   │      │   Plugin   │  │
│  │  (消息处理)  │      │  (Agent)   │      │  (文件存储)  │  │
│  └─────────────┘      └─────────────┘      └─────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 插件类型

| 类型 | 说明 | 示例 |
|------|------|------|
| **Message Plugin** | 消息处理插件 | 消息过滤、内容审核、格式转换 |
| **AI Adapter** | AI/Agent 适配器 | OpenClaw 适配器、自定义 Agent |
| **File Plugin** | 文件存储插件 | 本地存储、S3 存储 |
| **Channel Plugin** | 通道适配插件 | WebSocket、SSE、HTTP Polling |
| **Storage Plugin** | 存储后端插件 | PostgreSQL、MySQL、SQLite |

### 4.3 生命周期钩子

```python
class DropMessage(Exception):
    """抛出此异常以截断消息，不再继续传递"""
    pass

class Plugin:
    """插件接口"""
    
    name: str = "plugin-name"
    version: str = "1.0.0"
    dependencies: List[str] = []
    priority: int = 50  # 0-100，越高越先执行
    
    def on_load(self):
        """插件加载时调用"""
        pass
    
    def on_message(self, message: "Message") -> Optional["Message"]:
        """
        消息拦截处理
        - return message: 处理后的消息，继续传递
        - return None: 不修改消息，继续传递
        - raise DropMessage: 截断消息，不再传递
        """
        return message
    
    def on_before_send(self, message: "Message") -> "Message":
        """消息发送前处理（可修改消息内容）"""
        return message
    
    def on_unload(self):
        """插件卸载时调用（清理资源）"""
        pass
```

### 4.4 插件隔离策略

> ⚠️ **重要说明**：当前设计为**共享进程模式**，插件共享主进程内存空间。

**风险**：
- 插件崩溃可能导致主进程崩溃
- 插件间可能有变量污染

**保护措施**：
- 事件处理超时保护（5s）
- 事件链深度限制（max_depth: 10）
- 插件需实现异常捕获，不向上抛出未处理异常

### 4.5 事件总线

```python
# 插件间通信示例
event_bus.emit("message.received", message, sender=self)
event_bus.on("message.received", self.handle_message)

# 异步事件
await event_bus.emit_async("agent.response", response)

# 事件链深度保护
# 如果事件 A -> B -> C -> ... -> A，深度超过 10 层则中断
```

---

## 5. 数据库设计

### 5.1 实体关系

```
users (用户表)
    │
    ├── conversations (会话表)
    │       │
    │       └── conversation_members (会话成员表)
    │
    ├── messages (消息表)
    │       │
    │       └── message_attachments (消息附件表)
    │
    └── files (文件表)
```

### 5.2 表结构

#### users

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 主键 |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 用户名 |
| display_name | VARCHAR(100) | NOT NULL | 显示名 |
| user_type | VARCHAR(20) | NOT NULL, CHECK | human / agent |
| metadata | JSONB | DEFAULT '{}' | 扩展元数据 |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | 创建时间 |
| updated_at | TIMESTAMPTZ | DEFAULT NOW() | 更新时间 |

#### conversations

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 主键 |
| type | VARCHAR(20) | NOT NULL, CHECK | direct / group |
| name | VARCHAR(255) | | 群聊名称（group 类型时建议非空） |
| owner_id | UUID | FK -> users.id, NOT NULL | 创建者 |
| metadata | JSONB | DEFAULT '{}' | 扩展元数据 |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | 创建时间 |
| updated_at | TIMESTAMPTZ | DEFAULT NOW() | 更新时间（重命名、增删成员、修改公告时自动更新） |

#### conversation_members

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| conversation_id | UUID | PK, FK -> conversations.id | 会话ID |
| user_id | UUID | PK, FK -> users.id | 用户ID |
| role | VARCHAR(20) | NOT NULL, CHECK | owner / admin / member |
| nickname | VARCHAR(100) | | 群内昵称 |
| joined_at | TIMESTAMPTZ | DEFAULT NOW() | 加入时间 |
| left_at | TIMESTAMPTZ | | 离开时间 |
| is_active | BOOLEAN | DEFAULT TRUE | 是否激活 |

> ✅ **联合主键**：`PRIMARY KEY (conversation_id, user_id)`

#### messages

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 主键 |
| conversation_id | UUID | FK -> conversations.id, NOT NULL, INDEX | 会话ID |
| sender_id | UUID | FK -> users.id, NOT NULL | 发送者 |
| content | TEXT | NOT NULL | 消息内容 |
| content_type | VARCHAR(20) | NOT NULL, CHECK | text / markdown / image / file |
| metadata | JSONB | DEFAULT '{}' | 扩展元数据 |
| reply_to | UUID | FK -> messages.id ON DELETE SET NULL | 回复的消息ID（级联置空） |
| is_deleted | BOOLEAN | DEFAULT FALSE, INDEX | 软删除标记 |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), INDEX | 创建时间 |
| edited_at | TIMESTAMPTZ | | 编辑时间 |
| deleted_at | TIMESTAMPTZ | | 删除时间（由应用层自动填充） |

> ✅ **索引**：`INDEX idx_messages_conversation_time (conversation_id, created_at DESC)`

#### files

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 主键 |
| uploader_id | UUID | FK -> users.id, NOT NULL | 上传者 |
| conversation_id | UUID | FK -> conversations.id | 关联会话 |
| file_name | VARCHAR(255) | NOT NULL | 文件名 |
| file_path | VARCHAR(500) | NOT NULL | 存储路径 |
| file_size | BIGINT | NOT NULL | 文件大小(字节) |
| mime_type | VARCHAR(100) | | MIME类型 |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | 创建时间 |

### 5.3 CHECK 约束

```sql
-- users.user_type
ALTER TABLE users ADD CONSTRAINT chk_user_type 
    CHECK (user_type IN ('human', 'agent'));

-- conversations.type
ALTER TABLE conversations ADD CONSTRAINT chk_conversation_type 
    CHECK (type IN ('direct', 'group'));

-- conversation_members.role
ALTER TABLE conversation_members ADD CONSTRAINT chk_member_role 
    CHECK (role IN ('owner', 'admin', 'member'));

-- messages.content_type
ALTER TABLE messages ADD CONSTRAINT chk_content_type 
    CHECK (content_type IN ('text', 'markdown', 'image', 'file', 'system'));
```

### 5.4 分层存储策略

```
Redis（热数据）
├── 最近 7 天的消息
│   └── Key: messages:{conversation_id}:{date}（按日期分桶）
│   └── TTL: 8 天（比迁移周期多 1 天缓冲，确保迁移任务可访问）
├── 在线用户状态
│   └── Key: online:{user_id}
│   └── TTL: 5 分钟（心跳续期）
├── 离线消息队列
│   └── Key: offline:{user_id} -> List[message_id]
│   └── TTL: 30 天（离线消息保留）
├── 会话缓存
│   └── Key: conv:{conversation_id} -> JSON
│   └── TTL: 1 小时（定时失效）
└── 注意：不使用自动过期删除，由迁移任务统一清理

PostgreSQL（冷数据）
├── 历史消息（7 天以上）
├── 用户信息
├── 会话信息
└── 文件元数据

数据迁移
├── 定时任务：每日 03:00 执行
├── 迁移范围：Redis 中 created_at < 8 天前的消息
└── 一致性保障：
    1. 消息写入时双写（Redis + PostgreSQL）
    2. Redis TTL 设为 8 天，留出缓冲时间
    3. 迁移任务将 Redis 数据同步到 PostgreSQL 归档
    4. 迁移完成后统一删除 Redis 旧数据（不是等 TTL 自动过期）
```

> ⚠️ **一致性保障**：消息写入时同时写 Redis 和 PostgreSQL（双写），迁移任务统一清理 Redis 数据，不再依赖 TTL 自动过期。

---

## 6. API 设计

### 6.1 REST API

```
认证：
POST   /api/v1/auth/register        注册
POST   /api/v1/auth/login           登录
POST   /api/v1/auth/refresh         刷新Token

用户：
GET    /api/v1/users/me             当前用户信息
PUT    /api/v1/users/me             更新当前用户

会话：
GET    /api/v1/conversations        会话列表
POST   /api/v1/conversations        创建会话
GET    /api/v1/conversations/{id}   会话详情
PUT    /api/v1/conversations/{id}   更新会话
DELETE /api/v1/conversations/{id}   删除会话（仅 owner）

消息：
GET    /api/v1/conversations/{id}/messages     消息列表（分页）
POST   /api/v1/conversations/{id}/messages     发送消息
PUT    /api/v1/messages/{id}                   编辑消息（仅 sender 可编辑自己的消息）
DELETE /api/v1/messages/{id}                   删除消息（软删除，仅 sender 可删除自己的消息，admin 可删除任意消息）

成员：
GET    /api/v1/conversations/{id}/members      成员列表
POST   /api/v1/conversations/{id}/members      添加成员（admin+）
DELETE /api/v1/conversations/{id}/members/{uid} 移除成员（admin+）

文件：
POST   /api/v1/files/upload                   上传文件
GET    /api/v1/files/{id}                     下载文件
DELETE /api/v1/files/{id}                     删除文件（元数据软删除，物理文件异步清理）

> **文件清理策略**：删除时仅将 `files.deleted_at` 置为当前时间（软删除），物理文件由后台任务异步清理，避免删除阻塞。
```

#### 分页规范

```
GET /api/v1/conversations/{id}/messages

Query Parameters:
- limit: int = 50（最大 100）
- before: timestamp（时间倒序，翻页用）
- after: timestamp（时间正序）

Response:
{
  "items": [...],
  "next_cursor": "timestamp",
  "has_more": true
}
```

### 6.3 SSE (Server-Sent Events)

用于服务端主动推送事件通知（如成员变更、系统通知）。

```
GET /api/v1/events

Headers:
- Authorization: Bearer {token}
- Accept: text/event-stream
- Last-Event-ID: {id}（可选，断线重连用）

响应类型：text/event-stream

事件格式：
event: {event_type}
data: {json_data}
id: {event_id}

事件类型：
- member_joined    成员加入
- member_left      成员离开
- conversation_updated  会话信息更新
- message_sent     新消息（WS 的补充推送）

断开重连：
- 客户端需记录 Last-Event-ID
- 断线后重新连接时携带 Last-Event-ID
- 服务端从该 ID 之后开始推送遗漏事件

心跳：
- 服务端每 30s 发送一次 comment 行 `: heartbeat`
```

### 6.4 WebSocket API

```
连接：
WSS /ws?token=xxx（生产环境必须使用 WSS）

认证：
- 方式一：Query Parameter `?token=xxx`（开发环境）
- 方式二：Sec-WebSocket-Protocol 头传递 token（生产环境推荐）
```

分帧协议：
{
  "type": "message" | "subscribe" | "unsubscribe" | "ping" | "pong",
  "id": "uuid"（可选，用于 ack）,
  "params": {...}
}

客户端 → 服务端：

1. 订阅会话
{
  "type": "subscribe",
  "params": {
    "conversation_id": "uuid"
  }
}

2. 取消订阅
{
  "type": "unsubscribe", 
  "params": {
    "conversation_id": "uuid"
  }
}

3. 发送消息
{
  "type": "message",
  "id": "uuid",
  "params": {
    "conversation_id": "uuid",
    "content": "string",
    "content_type": "text",
    "mentions": ["uuid"],
    "reply_to": "uuid"
  }
}

4. 心跳
{
  "type": "ping"
}

服务端 → 客户端：

1. 新消息
{
  "type": "message",
  "data": {
    "id": "uuid",
    "conversation_id": "uuid",
    "sender_id": "uuid",
    "content": "string",
    "content_type": "text",
    "mentions": ["uuid"],
    "reply_to": "uuid",
    "created_at": "timestamp"
  }
}

2. 消息确认（ack）
{
  "type": "ack",
  "id": "uuid"（对应客户端消息 id）,
  "params": {
    "status": "sent" | "error",
    "message_id": "uuid"（服务端消息 id）,
    "error": "string"
  }
}

3. 事件通知
{
  "type": "event",
  "event": "member_joined" | "member_left" | "conversation_updated",
  "data": {...}
}

4. 错误
{
  "type": "error",
  "code": 1001 | 1002 | 1003 | ...,
  "message": "string"
}

Error Codes:
- 1001: INVALID_PARAMS（参数错误）
- 1002: UNAUTHORIZED（未认证）
- 1003: FORBIDDEN（无权限）
- 1004: NOT_FOUND（资源不存在）
- 1005: RATE_LIMIT（限流）
- 1010: INTERNAL_ERROR（服务器内部错误）
```

---

## 7. 权限矩阵

| 操作 | Owner | Admin | Member | Bot/Agent |
|------|-------|-------|--------|-----------|
| 发送消息 | ✅ | ✅ | ✅ | ✅ |
| 编辑自己的消息 | ✅ | ✅ | ✅ | ❌ |
| 删除自己的消息 | ✅ | ✅ | ✅ | ❌ |
| 删除他人的消息 | ✅ | ✅ | ❌ | ❌ |
| 查看会话信息 | ✅ | ✅ | ✅ | ✅ |
| 修改会话名称 | ✅ | ✅ | ❌ | ❌ |
| 修改群公告 | ✅ | ✅ | ❌ | ❌ |
| 添加成员 | ✅ | ✅ | ❌ | ❌ |
| 移除成员 | ✅ | ✅ | ❌ | ❌ |
| 设置管理员 | ✅ | ❌ | ❌ | ❌ |
| 删除会话 | ✅ | ❌ | ❌ | ❌ |
| 转让所有权 | ✅ | ❌ | ❌ | ❌ |

---

## 8. 目录结构

```
Sprinkle/
├── src/
│   └── sprinkle/
│       ├── __init__.py
│       ├── main.py              # FastAPI 入口
│       ├── config.py             # 配置管理
│       ├── kernel/              # 核心模块
│       │   ├── __init__.py
│       │   ├── session.py       # 会话管理
│       │   ├── message.py       # 消息路由
│       │   ├── event.py         # 事件总线
│       │   └── auth.py          # 认证服务
│       ├── plugins/             # 插件系统
│       │   ├── __init__.py
│       │   ├── manager.py       # 插件管理器
│       │   ├── registry.py     # 插件注册表
│       │   └── sandbox.py       # 插件沙箱（共享进程）
│       ├── models/              # 数据模型
│       │   ├── __init__.py
│       │   ├── user.py
│       │   ├── conversation.py
│       │   └── message.py
│       ├── api/                 # API 层
│       │   ├── __init__.py
│       │   ├── auth.py
│       │   ├── conversations.py
│       │   ├── messages.py
│       │   └── websocket.py
│       └── storage/             # 存储层
│           ├── __init__.py
│           ├── database.py       # 数据库连接
│           └── cache.py         # Redis 缓存
├── plugins/                     # 插件目录
│   └── .gitkeep
├── tests/                      # 测试目录
├── docs/                       # 文档
├── pyproject.toml
├── uv.lock
└── README.md
```

---

## 9. 配置示例

```yaml
# config.yaml.example - 开发环境配置

app:
  name: Sprinkle
  host: 0.0.0.0
  port: 8000
  debug: false

database:
  driver: postgresql
  host: localhost
  port: 5432
  name: sprinkle_db
  user: cream
  # password: ""  # 生产环境必须设置强密码

redis:
  host: localhost
  port: 6379
  db: 0
  # password: ""  # 生产环境建议设置密码

websocket:
  ping_interval: 30
  ping_timeout: 10
  max_retry: 3

storage:
  hot_ttl_days: 7
  file_dir: ./data/files

plugins:
  dir: ./plugins
  auto_load: true
  timeout: 5  # 事件处理超时(秒)
  max_depth: 10  # 事件链最大深度
```

> ⚠️ **生产环境要求**：
> - 数据库必须设置强密码
> - Redis 建议设置密码
> - 开启 HTTPS/WSS
> - 配置日志记录

---

## 10. 下一步计划

1. **项目初始化**：搭建 FastAPI 项目骨架
2. **核心模块实现**：Session Manager、Event Bus、Plugin Manager
3. **插件系统完善**：实现准热拔插机制
4. **API 实现**：REST API + WebSocket 消息通道
5. **测试**：单元测试 + 集成测试

---

## 附录 A：版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1.0 | 2026-04-15 | 初始版本 |
| v0.2.0 | 2026-04-15 | 审计修订版：补充数据库约束、API 规范、权限矩阵、分层存储细节 |
| v0.3.0 | 2026-04-15 | 第二轮审计修订：修复 reply_to 级联策略、分层存储 TTL 逻辑、group name 约束、deleted_at 填充说明、API 权限校验、SSE 端点、WebSocket token 安全、on_message 语义、文件清理策略、流式 buffer 超时说明 |

---

*文档由司康编写，布莱妮审计~🍪🍫*
