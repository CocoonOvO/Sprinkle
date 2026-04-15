# Sprinkle 开发任务管理

> 版本：v0.1  
> 更新日期：2026-04-15  
> 状态：📋 待开发

---

## 📊 任务总览

| 阶段 | 模块 | 优先级 | 预估工时 | 状态 |
|------|------|--------|----------|------|
| **Phase 1** | 项目初始化 | P0 | 1h | ⏳ 待开始 |
| **Phase 2** | 核心基础设施 | P0 | 4-6h | ⏳ 待开始 |
| **Phase 3** | 插件系统 | P0 | 3-4h | ⏳ 待开始 |
| **Phase 4** | API 层 | P1 | 4-5h | ⏳ 待开始 |
| **Phase 5** | WebSocket & SSE | P1 | 2-3h | ⏳ 待开始 |
| **Phase 6** | 业务逻辑 | P1 | 3-4h | ⏳ 待开始 |
| **Phase 7** | 测试 | P2 | 3-4h | ⏳ 待开始 |

---

## Phase 1：项目初始化

**目标**：搭建 FastAPI 项目骨架，配置依赖，建立目录结构

### 1.1 初始化项目结构
```
Sprinkle/
├── src/sprinkle/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── kernel/
│   ├── plugins/
│   ├── models/
│   ├── api/
│   └── storage/
├── tests/
├── docs/
└── plugins/
```

### 1.2 配置管理
- 创建 `config.py`，支持 YAML 配置读取
- 环境变量覆盖机制
- 类型安全的配置模型（Pydantic）

### 1.3 依赖安装
- fastapi, uvicorn
- sqlalchemy, asyncpg, psycopg2-binary
- redis
- pydantic, pydantic-settings
- python-jose, passlib
- websockets, sse-starlette
- pytest, pytest-asyncio

### 1.4 数据库连接
- SQLAlchemy 2.0 异步引擎
- PostgreSQL 连接池
- Redis 连接管理

**交付物**：项目骨架可运行 `uv run sprinkle run`

---

## Phase 2：核心基础设施

**目标**：实现核心 Kernel 模块

### 2.1 数据模型（models/）
- [ ] `user.py` - 用户模型
- [ ] `conversation.py` - 会话模型（含 conversation_members）
- [ ] `message.py` - 消息模型
- [ ] `file.py` - 文件模型

### 2.2 Session Manager（kernel/session.py）
- [ ] 连接池管理
- [ ] 会话存储（Redis + 内存）
- [ ] 心跳机制（ping_interval: 30s, ping_timeout: 10s）
- [ ] 断线重连处理
- [ ] 消息补发机制

### 2.3 Event Bus（kernel/event.py）
- [ ] 事件注册表
- [ ] 同步/异步分发器
- [ ] 事件链深度检测（max_depth: 10）
- [ ] 事件处理超时保护（5s）
- [ ] 循环检测机制

### 2.4 Message Router（kernel/message.py）
- [ ] Stream Buffer（chunk_size: 64KB, max_buffer: 10MB）
- [ ] 完整消息判断（EOS 标记 | 超时 5s）
- [ ] 消息队列（Redis）
- [ ] 分发器

### 2.5 Auth Service（kernel/auth.py）
- [ ] JWT Token 生成与验证
- [ ] 密码加密（bcrypt）
- [ ] 用户认证

**交付物**：核心模块单元测试通过

---

## Phase 3：插件系统

**目标**：实现插件热拔插机制

### 3.1 Plugin Manager（plugins/manager.py）
- [ ] 插件注册表
- [ ] 依赖解析器
- [ ] 生命周期管理（load/unload）
- [ ] 准热拔插实现（importlib）

### 3.2 Plugin 接口（plugins/plugin.py）
- [ ] Plugin 基类定义
- [ ] 生命周期钩子（on_load, on_message, on_before_send, on_unload）
- [ ] DropMessage 异常

### 3.3 事件总线集成
- [ ] 插件事件订阅
- [ ] 插件优先级链
- [ ] 错误处理与隔离

### 3.4 内置插件
- [ ] Hello World 示例插件
- [ ] 消息日志插件

**交付物**：插件可动态加载/卸载

---

## Phase 4：API 层

**目标**：实现 REST API

### 4.1 Auth API（api/auth.py）
- [ ] `POST /api/v1/auth/register` - 注册
- [ ] `POST /api/v1/auth/login` - 登录
- [ ] `POST /api/v1/auth/refresh` - 刷新Token

### 4.2 User API（api/users.py）
- [ ] `GET /api/v1/users/me` - 当前用户
- [ ] `PUT /api/v1/users/me` - 更新用户

### 4.3 Conversation API（api/conversations.py）
- [ ] `GET /api/v1/conversations` - 会话列表
- [ ] `POST /api/v1/conversations` - 创建会话
- [ ] `GET /api/v1/conversations/{id}` - 会话详情
- [ ] `PUT /api/v1/conversations/{id}` - 更新会话
- [ ] `DELETE /api/v1/conversations/{id}` - 删除会话

### 4.4 Message API（api/messages.py）
- [ ] `GET /api/v1/conversations/{id}/messages` - 消息列表（分页）
- [ ] `POST /api/v1/conversations/{id}/messages` - 发送消息
- [ ] `PUT /api/v1/messages/{id}` - 编辑消息
- [ ] `DELETE /api/v1/messages/{id}` - 删除消息（软删除）

### 4.5 Member API（api/members.py）
- [ ] `GET /api/v1/conversations/{id}/members` - 成员列表
- [ ] `POST /api/v1/conversations/{id}/members` - 添加成员
- [ ] `DELETE /api/v1/conversations/{id}/members/{uid}` - 移除成员

### 4.6 File API（api/files.py）
- [ ] `POST /api/v1/files/upload` - 上传文件
- [ ] `GET /api/v1/files/{id}` - 下载文件
- [ ] `DELETE /api/v1/files/{id}` - 删除文件

**交付物**：REST API 可通过 Swagger 文档测试

---

## Phase 5：WebSocket & SSE

**目标**：实现实时通信

### 5.1 WebSocket Handler（api/websocket.py）
- [ ] 连接认证（token 验证）
- [ ] 分帧协议解析（JSON）
- [ ] 订阅/取消订阅
- [ ] 普通消息发送
- [ ] 流式消息（start/chunk/end 三阶段）
- [ ] 心跳处理
- [ ] ack & error 响应

### 5.2 SSE Handler（api/events.py）
- [ ] `GET /api/v1/events` - SSE 端点
- [ ] 事件类型（member_joined, member_left, conversation_updated, message_sent）
- [ ] Last-Event-ID 断线重连
- [ ] 心跳（30s）

### 5.3 Session 集成
- [ ] WebSocket 连接与会话管理集成
- [ ] SSE 连接与会话管理集成
- [ ] 消息分发到所有订阅者

**交付物**：客户端可通过 WebSocket 和 SSE 收发消息

---

## Phase 6：业务逻辑

**目标**：实现业务规则

### 6.1 权限控制
- [ ] Owner/Admin/Member/Agent 权限矩阵
- [ ] Agent Admin 特殊权限
- [ ] API 层权限检查中间件

### 6.2 分层存储
- [ ] Redis 写入（热数据）
- [ ] PostgreSQL 写入（双写）
- [ ] 定时迁移任务（每日 03:00）
- [ ] 离线消息队列

### 6.3 会话服务（kernel/conversation_service.py）
- [ ] 创建群聊/私聊
- [ ] 邀请/移除成员
- [ ] 角色变更（设置 Admin）
- [ ] 群信息更新

### 6.4 消息服务（kernel/message_service.py）
- [ ] 消息发送（含流式）
- [ ] 消息编辑
- [ ] 消息软删除
- [ ] 回复引用

**交付物**：完整业务逻辑可用

---

## Phase 7：测试

**目标**：保证代码质量

### 7.1 单元测试
- [ ] 数据模型测试
- [ ] 核心模块测试（Session, Event, Message）
- [ ] 插件系统测试
- [ ] API 端点测试

### 7.2 集成测试
- [ ] WebSocket 连接测试
- [ ] SSE 连接测试
- [ ] 完整消息流程测试

### 7.3 性能测试
- [ ] 并发连接测试
- [ ] 消息吞吐量测试

**交付物**：测试覆盖率 > 70%

---

## 📋 任务进度看板

### 待办（Todo）
- [ ] Phase 1：项目初始化
- [ ] Phase 2：核心基础设施
- [ ] Phase 3：插件系统
- [ ] Phase 4：API 层
- [ ] Phase 5：WebSocket & SSE
- [ ] Phase 6：业务逻辑
- [ ] Phase 7：测试

### 进行中（In Progress）
- [ ] 

### 完成（Done）
- [ ] 架构文档设计

---

## 📝 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-04-15 | v0.1 | 初始任务拆分 |

---

*任务管理文档由司康维护~🍪*
