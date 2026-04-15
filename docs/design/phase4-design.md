# Phase 4: REST API 层设计文档

> 版本：v0.1  
> 更新日期：2026-04-15  
> 状态：📐 设计中

---

## 1. 概述

### 1.1 目标

实现 Sprinkle 的 REST API 层，提供：
- 用户认证（注册、登录、Token 刷新）
- 用户信息管理
- 会话（Conversations）管理
- 消息管理（含分页）
- 成员管理
- 文件上传/下载/删除

### 1.2 范围

在 `src/sprinkle/api/` 目录下实现：
- `auth.py` - 认证 API
- `users.py` - 用户 API
- `conversations.py` - 会话 API
- `messages.py` - 消息 API
- `members.py` - 成员 API
- `files.py` - 文件 API
- `dependencies.py` - 通用依赖（认证、安全等）

### 1.3 与其他模块的关系

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ api/auth.py        - 认证 API（注册、登录、刷新）        │ │
│  │ api/users.py       - 用户 API                          │ │
│  │ api/conversations.py - 会话 API                       │ │
│  │ api/messages.py    - 消息 API                         │ │
│  │ api/members.py     - 成员 API                          │ │
│  │ api/files.py       - 文件 API                          │ │
│  │ api/dependencies.py - 通用依赖                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                 │
│                            ▼                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ kernel/auth.py     - AuthService（依赖）               │ │
│  │ kernel/message.py  - Message 模型（依赖）               │ │
│  │ storage/database.py - 数据库连接（依赖）                 │ │
│  │ plugins/base.py    - Plugin 基类（参考）               │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 技术实现

### 2.1 目录结构

```
src/sprinkle/api/
├── __init__.py          # 模块导出
├── dependencies.py      # 通用依赖（Auth、DB Session）
├── auth.py             # 认证 API
├── users.py           # 用户 API
├── conversations.py   # 会话 API
├── messages.py        # 消息 API
├── members.py         # 成员 API
└── files.py          # 文件 API
```

### 2.2 认证依赖（dependencies.py）

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.kernel.auth import AuthService, TokenData, UserCredentials
from sprinkle.storage.database import get_async_session

# Bearer Token 认证
security = HTTPBearer()

# 全局 AuthService 实例（延迟初始化）
_auth_service: Optional[AuthService] = None

def get_auth_service() -> AuthService:
    """获取或创建 AuthService 实例"""
    global _auth_service
    if _auth_service is None:
        from sprinkle.config import settings
        _auth_service = AuthService(settings)
    return _auth_service

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserCredentials:
    """获取当前认证用户"""
    token = credentials.credentials
    user = await auth_service.authenticate_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return user

async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
    auth_service: AuthService = Depends(get_auth_service),
) -> Optional[UserCredentials]:
    """可选的当前用户（用于公开 endpoint）"""
    if not credentials:
        return None
    return await auth_service.authenticate_token(credentials.credentials)
```

### 2.3 API Router 注册（__init__.py）

```python
from fastapi import APIRouter

from sprinkle.api.auth import router as auth_router
from sprinkle.api.users import router as users_router
from sprinkle.api.conversations import router as conversations_router
from sprinkle.api.messages import router as messages_router
from sprinkle.api.members import router as members_router
from sprinkle.api.files import router as files_router

# API v1 router
api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(conversations_router, tags=["conversations"])
api_router.include_router(messages_router, tags=["messages"])
api_router.include_router(members_router, tags=["members"])
api_router.include_router(files_router, prefix="/files", tags=["files"])
```

---

## 3. API 详细设计

### 3.1 Auth API（auth.py）

#### POST /api/v1/auth/register - 注册

**Request:**
```json
{
  "username": "string",
  "password": "string",
  "display_name": "string (optional)",
  "is_agent": false
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "username": "string",
  "display_name": "string",
  "user_type": "human",
  "created_at": "datetime"
}
```

**Errors:**
- 400: Username already exists
- 422: Validation error

#### POST /api/v1/auth/login - 登录

**Request:**
```json
{
  "username": "string",
  "password": "string"
}
```

**Response (200):**
```json
{
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Errors:**
- 401: Invalid credentials

#### POST /api/v1/auth/refresh - 刷新Token

**Request:**
```json
{
  "refresh_token": "string"
}
```

**Response (200):**
```json
{
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Errors:**
- 401: Invalid refresh token

### 3.2 User API（users.py）

#### GET /api/v1/users/me - 当前用户

**Response (200):**
```json
{
  "id": "uuid",
  "username": "string",
  "display_name": "string",
  "user_type": "human",
  "metadata": {},
  "created_at": "datetime"
}
```

#### PUT /api/v1/users/me - 更新用户

**Request:**
```json
{
  "display_name": "string (optional)",
  "metadata": {} (optional)
}
```

**Response (200):**
```json
{
  "id": "uuid",
  "username": "string",
  "display_name": "string",
  "user_type": "human",
  "metadata": {},
  "created_at": "datetime"
}
```

### 3.3 Conversation API（conversations.py）

#### GET /api/v1/conversations - 会话列表

**Query Parameters:**
- `limit`: int = 50 (max 100)
- `offset`: int = 0

**Response (200):**
```json
{
  "items": [
    {
      "id": "uuid",
      "type": "direct/group",
      "name": "string",
      "owner_id": "uuid",
      "metadata": {},
      "created_at": "datetime",
      "updated_at": "datetime",
      "member_count": 5
    }
  ],
  "total": 100,
  "limit": 50,
  "offset": 0
}
```

#### POST /api/v1/conversations - 创建会话

**Request:**
```json
{
  "type": "direct/group",
  "name": "string (required for group)",
  "member_ids": ["uuid"],
  "metadata": {}
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "type": "direct/group",
  "name": "string",
  "owner_id": "uuid",
  "metadata": {},
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

#### GET /api/v1/conversations/{id} - 会话详情

**Response (200):**
```json
{
  "id": "uuid",
  "type": "direct/group",
  "name": "string",
  "owner_id": "uuid",
  "metadata": {},
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

**Errors:**
- 404: Conversation not found
- 403: Not a member

#### PUT /api/v1/conversations/{id} - 更新会话

**Request:**
```json
{
  "name": "string (optional)",
  "metadata": {} (optional)
}
```

**Response (200):**
```json
{
  "id": "uuid",
  "type": "direct/group",
  "name": "string",
  "owner_id": "uuid",
  "metadata": {},
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

**Errors:**
- 403: Only owner/admin can update
- 404: Conversation not found

#### DELETE /api/v1/conversations/{id} - 删除会话

**Response (204):** No content

**Errors:**
- 403: Only owner can delete
- 404: Conversation not found

### 3.4 Message API（messages.py）

#### GET /api/v1/conversations/{id}/messages - 消息列表（分页）

**Query Parameters:**
- `limit`: int = 50 (max 100)
- `before`: datetime (optional, for pagination)
- `after`: datetime (optional, for pagination)

**Response (200):**
```json
{
  "items": [
    {
      "id": "uuid",
      "conversation_id": "uuid",
      "sender_id": "uuid",
      "content": "string",
      "content_type": "text/markdown/image/file",
      "metadata": {},
      "mentions": ["uuid"],
      "reply_to": "uuid",
      "is_deleted": false,
      "created_at": "datetime",
      "edited_at": "datetime"
    }
  ],
  "next_cursor": "timestamp",
  "has_more": true
}
```

#### POST /api/v1/conversations/{id}/messages - 发送消息

**Request:**
```json
{
  "content": "string",
  "content_type": "text/markdown/image/file",
  "mentions": ["uuid"],
  "reply_to": "uuid"
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "conversation_id": "uuid",
  "sender_id": "uuid",
  "content": "string",
  "content_type": "text",
  "metadata": {},
  "mentions": [],
  "reply_to": null,
  "is_deleted": false,
  "created_at": "datetime",
  "edited_at": null
}
```

#### PUT /api/v1/messages/{id} - 编辑消息

**Request:**
```json
{
  "content": "string"
}
```

**Response (200):**
```json
{
  "id": "uuid",
  "conversation_id": "uuid",
  "sender_id": "uuid",
  "content": "string",
  "content_type": "text",
  "metadata": {},
  "mentions": [],
  "reply_to": null,
  "is_deleted": false,
  "created_at": "datetime",
  "edited_at": "datetime"
}
```

**Errors:**
- 403: Only sender can edit
- 404: Message not found

#### DELETE /api/v1/messages/{id} - 删除消息（软删除）

**Response (204):** No content

**Errors:**
- 403: Only sender (or admin) can delete
- 404: Message not found

### 3.5 Member API（members.py）

#### GET /api/v1/conversations/{id}/members - 成员列表

**Response (200):**
```json
{
  "items": [
    {
      "user_id": "uuid",
      "conversation_id": "uuid",
      "role": "owner/admin/member",
      "nickname": "string",
      "joined_at": "datetime",
      "is_active": true
    }
  ],
  "total": 5
}
```

#### POST /api/v1/conversations/{id}/members - 添加成员

**Request:**
```json
{
  "user_id": "uuid",
  "role": "admin/member",
  "nickname": "string (optional)"
}
```

**Response (201):**
```json
{
  "user_id": "uuid",
  "conversation_id": "uuid",
  "role": "admin/member",
  "nickname": "string",
  "joined_at": "datetime",
  "is_active": true
}
```

**Errors:**
- 403: Only admin+ can add members
- 404: Conversation or user not found

#### DELETE /api/v1/conversations/{id}/members/{uid} - 移除成员

**Response (204):** No content

**Errors:**
- 403: Only admin+ can remove members
- 404: Member not found

### 3.6 File API（files.py）

#### POST /api/v1/files/upload - 上传文件

**Request:** multipart/form-data
- `file`: binary (required)
- `conversation_id`: uuid (optional)

**Response (201):**
```json
{
  "id": "uuid",
  "file_name": "string",
  "file_size": 12345,
  "mime_type": "image/png",
  "conversation_id": "uuid",
  "created_at": "datetime"
}
```

#### GET /api/v1/files/{id} - 下载文件

**Response (200):**
- Content-Type: application/octet-stream
- Content-Disposition: attachment; filename="xxx"

**Errors:**
- 404: File not found

#### DELETE /api/v1/files/{id} - 删除文件

**Response (204):** No content

**Errors:**
- 403: Only uploader can delete
- 404: File not found

---

## 4. 数据模型

### 4.1 内存存储（In-Memory Store）

由于 Phase 4 是 API 层，不包含数据库集成，使用内存存储：

```python
# users 存储
_users: Dict[str, UserCredentials] = {}

# conversations 存储
class ConversationStore:
    id: str
    type: str  # "direct" / "group"
    name: str
    owner_id: str
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

_conversations: Dict[str, ConversationStore] = {}

# conversation_members 存储
class MemberStore:
    conversation_id: str
    user_id: str
    role: str  # "owner" / "admin" / "member"
    nickname: Optional[str]
    joined_at: datetime
    is_active: bool

_members: Dict[Tuple[str, str], MemberStore] = {}

# messages 存储
class MessageStore:
    id: str
    conversation_id: str
    sender_id: str
    content: str
    content_type: str
    metadata: Dict[str, Any]
    mentions: List[str]
    reply_to: Optional[str]
    is_deleted: bool
    created_at: datetime
    edited_at: Optional[datetime]
    deleted_at: Optional[datetime]

_messages: Dict[str, MessageStore] = {}

# files 存储
class FileStore:
    id: str
    uploader_id: str
    conversation_id: Optional[str]
    file_name: str
    file_path: str
    file_size: int
    mime_type: str
    created_at: datetime
    deleted_at: Optional[datetime]

_files: Dict[str, FileStore] = {}
```

### 4.2 分页响应格式

```python
class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    limit: int
    offset: int
    next_cursor: Optional[str] = None
    has_more: bool = False
```

---

## 5. 错误处理

### 5.1 HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 204 | 删除成功（无内容） |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 422 | 验证错误 |
| 500 | 服务器内部错误 |

### 5.2 错误响应格式

```json
{
  "detail": "Error message"
}
```

---

## 6. 权限矩阵（来自 ARCHITECTURE.md）

| 操作 | Owner | Admin | Agent（普通） | Agent（Admin） |
|------|-------|-------|-----------|--------------|
| 发送消息 | ✅ | ✅ | ✅ | ✅ |
| 编辑自己的消息 | ✅ | ✅ | ❌ | ✅ |
| 删除自己的消息 | ✅ | ✅ | ❌ | ✅ |
| 删除他人的消息 | ✅ | ✅ | ❌ | ✅ |
| 查看会话信息 | ✅ | ✅ | ✅ | ✅ |
| 修改会话名称 | ✅ | ✅ | ❌ | ✅ |
| 修改群公告 | ✅ | ✅ | ❌ | ✅ |
| 添加成员 | ✅ | ✅ | ❌ | ✅ |
| 移除成员 | ✅ | ✅ | ❌ | ✅ |
| 设置管理员 | ✅ | ❌ | ❌ | ❌ |
| 删除会话 | ✅ | ❌ | ❌ | ❌ |
| 转让所有权 | ✅ | ❌ | ❌ | ❌ |

---

## 7. main.py 集成

```python
from fastapi import FastAPI
from sprinkle.config import settings
from sprinkle.api import api_router

app = FastAPI(
    title=settings.app.name,
    debug=settings.app.debug,
)

# Include API router
app.include_router(api_router)

@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.app.name}", "status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
```

---

## 8. 测试策略

### 8.1 单元测试覆盖率目标

| 模块 | 覆盖率目标 |
|------|-----------|
| auth.py | > 85% |
| users.py | > 80% |
| conversations.py | > 80% |
| messages.py | > 80% |
| members.py | > 80% |
| files.py | > 80% |

### 8.2 测试项

1. **Auth API**
   - 注册成功/失败
   - 登录成功/失败
   - Token 刷新成功/失败

2. **User API**
   - 获取当前用户
   - 更新当前用户

3. **Conversation API**
   - CRUD 操作
   - 权限检查（owner/admin/member）

4. **Message API**
   - 发送消息
   - 分页查询
   - 编辑/删除权限

5. **Member API**
   - 成员列表
   - 添加/移除成员
   - 权限检查

6. **File API**
   - 上传/下载/删除
   - 权限检查

---

## 9. 异常类型

| 异常 | HTTP 状态码 | 说明 |
|------|-------------|------|
| `HTTPException(401)` | 401 | 未认证 |
| `HTTPException(403)` | 403 | 无权限 |
| `HTTPException(404)` | 404 | 资源不存在 |
| `HTTPException(422)` | 422 | 验证错误 |

---

*设计文档由司康编写~🍪*
