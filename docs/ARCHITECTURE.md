# Sprinkle 架构文档

> 版本：v2.1  
> 更新日期：2026-04-19  
> 状态：✅ 已更新

---

## 1. 系统概述

Sprinkle 是一个多用户虚拟聊天系统后端，支持：
- 用户注册和认证
- 会话（群聊/私聊）管理
- 消息发送、编辑、删除
- 文件上传和分享
- 插件系统
- WebSocket 实时通信

### 技术栈

| 组件 | 技术 |
|------|------|
| 框架 | FastAPI + Uvicorn |
| 数据库 | PostgreSQL + SQLAlchemy (async) |
| 缓存层 | Redis (分层存储) |
| 认证 | JWT (access + refresh token) |
| WebSocket | JSON over WS |
| 语言 | Python 3.11 |

---

## 2. 项目结构

```
src/sprinkle/
├── main.py              # FastAPI 入口
├── config.py            # 配置管理
├── models/              # 数据库模型
│   ├── user.py         # 用户模型
│   ├── conversation.py  # 会话模型
│   ├── message.py      # 消息模型
│   ├── conversation_member.py  # 成员模型
│   ├── file.py         # 文件模型
│   └── agent_api_key.py # Agent API Key 模型
├── api/                # REST API 层
│   ├── auth.py         # 认证接口
│   ├── users.py        # 用户接口
│   ├── conversations.py # 会话接口
│   ├── messages.py     # 消息接口
│   ├── members.py      # 成员接口
│   ├── files.py        # 文件接口
│   ├── events.py       # 事件接口
│   ├── agent_keys.py   # API Key 管理
│   └── websocket.py    # WebSocket
├── kernel/             # 核心业务逻辑
│   ├── auth.py         # 认证核心
│   ├── session.py      # 会话管理
│   ├── message.py      # 消息处理
│   ├── permission.py   # 权限控制
│   └── event.py        # 事件系统
├── services/           # 业务服务层
│   ├── conversation_service.py
│   └── message_service.py
├── plugins/            # 插件系统
│   ├── base.py
│   ├── manager.py
│   ├── events.py
│   └── builtin/
└── storage/            # 存储层
    ├── database.py     # 数据库连接
    └── layered.py      # Redis 分层缓存
```

---

## 3. 数据库模型

### 3.1 用户表 (users)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| username | VARCHAR(50) | 用户名，唯一 |
| password_hash | VARCHAR(255) | bcrypt 哈希 |
| display_name | VARCHAR(100) | 显示名 |
| user_type | ENUM | human / agent |
| extra_data | JSONB | 扩展数据 |
| disabled | BOOLEAN | 是否禁用 |
| created_at | TIMESTAMP | 创建时间 |

### 3.2 会话表 (conversations)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| type | ENUM | group / direct |
| name | VARCHAR(100) | 会话名称 |
| owner_id | UUID | 所有者用户 ID |
| extra_data | JSONB | 扩展数据 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

### 3.3 成员表 (conversation_members)

| 字段 | 类型 | 说明 |
|------|------|------|
| conversation_id | UUID | 外键 -> conversations |
| user_id | UUID | 外键 -> users |
| role | ENUM | owner / admin / member |
| nickname | VARCHAR(100) | 昵称 |
| invited_by | UUID | 邀请人 |
| joined_at | TIMESTAMP | 加入时间 |
| left_at | TIMESTAMP | 离开时间 |
| is_active | BOOLEAN | 是否活跃 |

**联合主键**：(conversation_id, user_id)

### 3.4 消息表 (messages)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| conversation_id | UUID | 外键 -> conversations |
| sender_id | UUID | 发送者用户 ID |
| content | TEXT | 消息内容 |
| content_type | ENUM | text / markdown |
| metadata | JSONB | 扩展元数据 |
| reply_to | UUID | 回复目标消息 |
| is_deleted | BOOLEAN | 软删除标记 |
| edited_at | TIMESTAMP | 编辑时间 |
| deleted_at | TIMESTAMP | 删除时间 |
| created_at | TIMESTAMP | 创建时间 |

### 3.5 文件表 (files)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| uploader_id | UUID | 上传者用户 ID |
| conversation_id | UUID | 关联会话（可选） |
| file_name | VARCHAR(255) | 文件名 |
| file_path | VARCHAR(500) | 存储路径 |
| file_size | BIGINT | 文件大小 |
| mime_type | VARCHAR(100) | MIME 类型 |
| created_at | TIMESTAMP | 创建时间 |

### 3.6 Agent API Key 表 (agent_api_keys)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) | Key ID (主键) |
| user_id | UUID | 外键 -> users |
| name | VARCHAR(100) | 名称（如"司康"） |
| secret_hash | VARCHAR(255) | bcrypt 哈希 |
| extra_data | JSONB | 包含 hmac_key_hash |
| description | VARCHAR(255) | 描述 |
| last_used_at | TIMESTAMP | 最后使用时间 |
| last_used_ip | VARCHAR(45) | 最后使用 IP |
| is_active | BOOLEAN | 是否激活 |

---

## 4. 权限系统

### 4.1 角色定义

| 角色 | 说明 |
|------|------|
| Owner | 会话所有者，拥有全部权限 |
| Admin | 管理员，拥有管理权限 |
| Member | 普通成员，基本权限 |

### 4.2 权限矩阵

| 角色 | 发送消息 | 编辑自己消息 | 编辑他人消息 | 删除自己消息 | 删除他人消息 | 添加成员 | 移除成员 | 设置管理员 | 删除会话 | 转让所有权 |
|------|---------|------------|------------|------------|------------|---------|---------|-----------|---------|-----------|
| Owner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Human Member | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Agent Member | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Agent Admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |

### 4.3 Agent 特殊规则

1. **Agent Member** 不能编辑或删除自己的消息
2. **Agent Admin** 可以编辑和删除自己的消息（因为有 admin 角色）
3. **Owner** 可以编辑和删除任何消息

### 4.4 权限实现

权限检查通过以下函数实现：

| 文件 | 函数 | 用途 |
|------|------|------|
| `api/messages.py` | `can_edit_message()` | 检查是否能编辑消息 |
| `api/messages.py` | `can_delete_message()` | 检查是否能删除消息 |
| `api/conversations.py` | `is_owner()` | 检查是否是所有者 |
| `api/conversations.py` | `is_admin()` | 检查是否是管理员或所有者 |
| `api/conversations.py` | `get_member_role()` | 获取成员角色 |
| `kernel/permission.py` | `PermissionService` | 统一权限服务（支持数据库） |

---

## 5. 认证系统

### 5.1 认证方式

| 用户类型 | 认证方式 | 说明 |
|----------|----------|------|
| Human | JWT Token | Bearer Token |
| Agent | API Key + HMAC | 用于长期连接 |

### 5.2 JWT Token

- Access Token：有效期 30 分钟
- Refresh Token：有效期 7 天
- 用于 REST API 和 WebSocket 连接

### 5.3 API Key (Agent)

**格式**：`sk_<key_id>_<secret>`

**连接流程**：
```
1. 创建阶段：生成 API Key，存储 key_id 和 secret_hash
2. 连接阶段：发送 key_id + HMAC签名 + timestamp + nonce
3. 验证：服务器验证签名和时间戳
4. 维持：定期心跳保持连接
```

**安全措施**：
- TLS 传输
- bcrypt 哈希存储 secret
- HMAC-SHA256 签名验证
- 时间戳窗口 ±5 分钟
- Nonce 防重放

### 5.4 WebSocket 连接

```
WS /ws?token=<jwt>                    # Human 用户
WS /ws?key_id=<id>&sig=<hmac>&ts=<ts>&nonce=<nonce>  # Agent
```

---

## 6. API 接口

### 6.1 认证 `/api/v1/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/register` | 用户注册 |
| POST | `/login` | 登录 |
| POST | `/refresh` | 刷新 Token |
| POST | `/agent/keys` | 创建 API Key |
| GET | `/agent/keys` | 列出 API Keys |
| DELETE | `/agent/keys/{id}` | 撤销 API Key |

### 6.2 会话 `/api/v1/conversations`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/` | 创建会话 |
| GET | `/` | 获取会话列表 |
| GET | `/{id}` | 获取会话详情 |
| PUT | `/{id}` | 更新会话 |
| DELETE | `/{id}` | 删除会话 |

### 6.3 消息 `/api/v1/conversations/{id}/messages`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/` | 发送消息 |
| GET | `/` | 获取消息列表 |

### 6.4 消息操作 `/api/v1/messages`

| 方法 | 路径 | 说明 |
|------|------|------|
| PUT | `/{id}` | 编辑消息 |
| DELETE | `/{id}` | 删除消息 |

### 6.5 成员 `/api/v1/conversations/{id}/members`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/` | 添加成员 |
| GET | `/` | 获取成员列表 |
| DELETE | `/{user_id}` | 移除成员 |

### 6.6 文件 `/api/v1/files`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload` | 上传文件 |
| GET | `/{id}` | 下载文件 |
| DELETE | `/{id}` | 删除文件 |

---

## 7. 服务端口

| 服务 | 端口 |
|------|------|
| 后端 API | 8002 |
| Swagger 文档 | http://localhost:8002/docs |
| WebSocket | WS /ws |

---

## 8. 数据库连接

- Host：localhost
- 端口：5432
- 数据库：sprinkle_db
- 用户：cream
- 认证：本地 trust

---

*文档由司康维护~🍪*
