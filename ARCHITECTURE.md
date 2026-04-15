# Sprinkle 架构文档

> 虚拟聊天软件后端系统  
> 版本：v0.1.0  
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
| **隔离级别** | 低隔离（共享进程） | 简单直接，插件通过 RPC 调用 |
| **热拔插** | 支持 | 基于 `importlib` 实现插件动态加载 |
| **插件通信** | 事件总线 | 插件通过事件总线进行通信 |

### 流式消息处理

```
[Agent] → (流式) → [Backend] → (完整后) → [所有接收方]
              ↑
         边接收边缓存，完整后才分发
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
├── Session Store（会话存储，Redis）
└── Heartbeat（心跳检测）
```

#### Message Router（消息路由）

负责消息的接收、缓存、路由和分发。

```
Message Router
├── Stream Buffer（流式消息缓冲）
├── Message Queue（消息队列）
└── Dispatcher（分发器）
```

#### Event Bus（事件总线）

插件间通信的核心，支持同步/异步事件分发。

```
Event Bus
├── Event Registry（事件注册表）
├── Sync Dispatcher（同步分发器）
└── Async Dispatcher（异步分发器）
```

#### Plugin Manager（插件管理器）

负责插件的加载、卸载、热拔插。

```
Plugin Manager
├── Plugin Registry（插件注册表）
├── Dependency Resolver（依赖解析器）
├── Lifecycle Hooks（生命周期钩子）
└── Plugin Sandbox（插件沙箱，共享进程）
```

### 3.3 数据流

```
1. [Client] --WebSocket--> [API Layer]
2. [API Layer] --> [Session Manager] --> [Event Bus]
3. [Event Bus] --> [Plugin Chain]（按优先级执行）
4. [Plugin Chain] --> [Message Router]
5. [Message Router] --> [流式缓冲] --> [完整消息]
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
│  │  - on_load()                                           │ │
│  │  - on_message(message) -> Optional[Message]            │ │
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
class Plugin:
    """插件接口"""
    
    name: str = "plugin-name"
    version: str = "1.0.0"
    dependencies: List[str] = []
    
    def on_load(self):
        """插件加载时调用"""
        pass
    
    def on_message(self, message: "Message") -> Optional["Message"]:
        """消息拦截处理，返回 None 则继续传递"""
        return None
    
    def on_before_send(self, message: "Message") -> "Message":
        """消息发送前处理"""
        return message
    
    def on_unload(self):
        """插件卸载时调用"""
        pass
```

### 4.4 事件总线

```python
# 插件间通信示例
event_bus.emit("message.received", message, sender=self)
event_bus.on("message.received", self.handle_message)

# 异步事件
await event_bus.emit_async("agent.response", response)
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

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| username | VARCHAR(50) | 用户名 |
| display_name | VARCHAR(100) | 显示名 |
| user_type | VARCHAR(20) | human / agent |
| metadata | JSONB | 扩展元数据 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### conversations

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| type | VARCHAR(20) | direct / group |
| name | VARCHAR(255) | 群聊名称 |
| owner_id | UUID | 创建者 |
| metadata | JSONB | 扩展元数据 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### conversation_members

| 字段 | 类型 | 说明 |
|------|------|------|
| conversation_id | UUID | 外键 |
| user_id | UUID | 外键 |
| role | VARCHAR(20) | owner / admin / member |
| nickname | VARCHAR(100) | 群内昵称 |
| joined_at | TIMESTAMPTZ | 加入时间 |
| is_active | BOOLEAN | 是否激活 |

#### messages

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| conversation_id | UUID | 外键 |
| sender_id | UUID | 发送者 |
| content | TEXT | 消息内容 |
| content_type | VARCHAR(20) | text / markdown / image / file |
| metadata | JSONB | 扩展元数据 |
| reply_to | UUID | 回复的消息ID |
| created_at | TIMESTAMPTZ | 创建时间 |
| edited_at | TIMESTAMPTZ | 编辑时间 |
| deleted_at | TIMESTAMPTZ | 删除时间 |

### 5.3 分层存储策略

```
Redis（热数据）
├── 最近 7 天的消息（按会话分桶）
├── 在线用户状态
├── 离线消息队列
└── 会话缓存

PostgreSQL（冷数据）
├── 历史消息（7 天以上）
├── 用户信息
├── 会话信息
└── 文件元数据
```

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
GET    /api/v1/conversations/:id    会话详情
PUT    /api/v1/conversations/:id    更新会话
DELETE /api/v1/conversations/:id    删除会话

消息：
GET    /api/v1/conversations/:id/messages     消息列表
POST   /api/v1/conversations/:id/messages     发送消息
PUT    /api/v1/messages/:id                   编辑消息
DELETE /api/v1/messages/:id                   删除消息

成员：
GET    /api/v1/conversations/:id/members     成员列表
POST   /api/v1/conversations/:id/members     添加成员
DELETE /api/v1/conversations/:id/members/:uid 移除成员
```

### 6.2 WebSocket API

```
连接：
WS /ws/{token}

客户端 → 服务端：
- subscribe { conversation_id }   订阅会话
- unsubscribe { conversation_id } 取消订阅
- message { conversation_id, content, mentions } 发送消息

服务端 → 客户端：
- message { ... }                 新消息
- event { type, data }            事件通知
- ack { message_id }              消息确认
- error { code, message }         错误通知
```

---

## 7. 目录结构

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
│       │   └── sandbox.py       # 插件沙箱
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

## 8. 配置示例

```yaml
# config.yaml

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
  password: ""

redis:
  host: localhost
  port: 6379
  db: 0

websocket:
  ping_interval: 30
  ping_timeout: 10

storage:
  hot_ttl_days: 7
  file_dir: ./data/files

plugins:
  dir: ./plugins
  auto_load: true
```

---

## 9. 下一步计划

1. **项目初始化**：搭建 FastAPI 项目骨架
2. **核心模块实现**：Session Manager、Event Bus、Plugin Manager
3. **插件系统完善**：实现插件热拔插机制
4. **API 实现**：REST API + WebSocket 消息通道
5. **测试**：单元测试 + 集成测试

---

*文档由司康编写，基于主人确认的架构选型~🍪*
