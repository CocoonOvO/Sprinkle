# Phase 6: 业务逻辑实现 设计文档

> 版本：v1.0  
> 更新日期：2026-04-15  
> 状态：📐 设计完成

---

## 1. 概述

### 1.1 目标

实现 Sprinkle 的业务逻辑层，包括：
- 权限控制系统（Owner/Admin/Member/Agent 权限矩阵）
- 分层存储系统（Redis 热数据 + PostgreSQL 冷数据）
- 会话服务（创建、邀请、移除、角色变更）
- 消息服务（发送、编辑、删除、回复引用）

### 1.2 范围

在 `src/sprinkle/` 目录下实现：
- `kernel/permission.py` - 权限控制
- `storage/layered.py` - 分层存储
- `services/conversation_service.py` - 会话服务
- `services/message_service.py` - 消息服务

### 1.3 与其他模块的关系

```
Phase 5 (WebSocket & SSE)
    │
    ├── kernel/session.py (会话管理)
    ├── kernel/auth.py (认证服务)
    ├── api/websocket.py (WebSocket 处理)
    └── api/events.py (SSE 事件)
            │
            ▼
Phase 6 (业务逻辑) ← 依赖 Phase 1-5 的所有接口
    │
    ├── kernel/permission.py (权限检查)
    ├── storage/layered.py (分层存储)
    ├── services/conversation_service.py (会话业务)
    └── services/message_service.py (消息业务)
```

---

## 2. 权限控制 (kernel/permission.py)

### 2.1 权限矩阵

根据 ARCHITECTURE.md 第 7 节定义：

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

### 2.2 Agent Admin 特殊权限

- Agent 默认权限受限，只能发送消息
- Agent 被 Owner 设置为 Admin 后，拥有与 Admin 完全相同的权限
- 通过 `role: "admin"` 在 conversation_members 表中标识

### 2.3 数据结构

```python
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional

class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"

class Action(str, Enum):
    SEND_MESSAGE = "send_message"
    EDIT_OWN_MESSAGE = "edit_own_message"
    DELETE_OWN_MESSAGE = "delete_own_message"
    DELETE_ANY_MESSAGE = "delete_any_message"
    VIEW_CONVERSATION = "view_conversation"
    EDIT_CONVERSATION = "edit_conversation"
    ADD_MEMBER = "add_member"
    REMOVE_MEMBER = "remove_member"
    SET_ADMIN = "set_admin"
    DELETE_CONVERSATION = "delete_conversation"
    TRANSFER_OWNERSHIP = "transfer_ownership"
```

### 2.4 PermissionService 接口

```python
class PermissionService:
    """权限服务"""
    
    async def check_permission(
        self,
        user_id: str,
        conversation_id: str,
        action: Action,
    ) -> bool:
        """检查用户是否有权限执行某操作"""
        
    async def get_user_role(
        self,
        user_id: str,
        conversation_id: str,
    ) -> Optional[Role]:
        """获取用户在会话中的角色"""
        
    async def is_agent_admin(
        self,
        user_id: str,
        conversation_id: str,
    ) -> bool:
        """检查 Agent 用户是否被设置为 Admin"""
        
    async def get_member_permissions(
        self,
        user_id: str,
        conversation_id: str,
    ) -> List[Action]:
        """获取用户在会话中的所有权限"""
```

### 2.5 API 层权限中间件

```python
class PermissionMiddleware:
    """API 权限检查中间件"""
    
    def __init__(self, permission_service: PermissionService):
        self._permission_service = permission_service
    
    async def require_permission(
        self,
        user_id: str,
        conversation_id: str,
        action: Action,
    ) -> None:
        """检查权限，不通过则抛出 HTTPException"""
```

---

## 3. 分层存储 (storage/layered.py)

### 3.1 存储策略

根据 ARCHITECTURE.md 第 5.4 节定义：

```
Redis（热数据）
├── 最近 7 天的消息
│   └── Key: messages:{conversation_id}:{date}
│   └── TTL: 8 天
├── 在线用户状态
│   └── Key: online:{user_id}
│   └── TTL: 5 分钟
├── 离线消息队列
│   └── Key: offline:{user_id}
│   └── TTL: 30 天
└── 会话缓存
    └── Key: conv:{conversation_id}
    └── TTL: 1 小时

PostgreSQL（冷数据）
├── 历史消息（7 天以上）
├── 用户信息
├── 会话信息
└── 文件元数据
```

### 3.2 数据结构

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
import json

@dataclass
class MessageRecord:
    """消息记录"""
    id: str
    conversation_id: str
    sender_id: str
    content: str
    content_type: str = "text"
    metadata: Dict[str, Any] = None
    mentions: List[str] = None
    reply_to: Optional[str] = None
    is_deleted: bool = False
    created_at: datetime
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

@dataclass
class ConversationRecord:
    """会话记录"""
    id: str
    type: str  # "direct" | "group"
    name: str
    owner_id: str
    metadata: Dict[str, Any] = None
    created_at: datetime
    updated_at: datetime

@dataclass
class MemberRecord:
    """成员记录"""
    conversation_id: str
    user_id: str
    role: str  # "owner" | "admin" | "member"
    nickname: Optional[str] = None
    joined_at: datetime
    left_at: Optional[datetime] = None
    is_active: bool = True
```

### 3.3 LayeredStorageService 接口

```python
class LayeredStorageService:
    """分层存储服务"""
    
    def __init__(self, redis_client, db_session):
        self._redis = redis_client
        self._db = db_session
    
    # ========== 消息操作 ==========
    
    async def save_message(self, message: MessageRecord) -> None:
        """保存消息（双写 Redis + PostgreSQL）"""
        
    async def get_message(self, message_id: str) -> Optional[MessageRecord]:
        """获取单条消息（优先 Redis，fallback PostgreSQL）"""
        
    async def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[MessageRecord]:
        """获取会话消息"""
        
    async def soft_delete_message(self, message_id: str) -> None:
        """软删除消息"""
        
    # ========== 会话操作 ==========
    
    async def save_conversation(self, conversation: ConversationRecord) -> None:
        """保存会话"""
        
    async def get_conversation(self, conversation_id: str) -> Optional[ConversationRecord]:
        """获取会话"""
        
    async def get_user_conversations(self, user_id: str) -> List[ConversationRecord]:
        """获取用户所在的所有会话"""
        
    # ========== 成员操作 ==========
    
    async def add_member(self, member: MemberRecord) -> None:
        """添加成员"""
        
    async def remove_member(self, conversation_id: str, user_id: str) -> None:
        """移除成员"""
        
    async def get_member(self, conversation_id: str, user_id: str) -> Optional[MemberRecord]:
        """获取成员信息"""
        
    async def get_conversation_members(self, conversation_id: str) -> List[MemberRecord]:
        """获取会话所有成员"""
        
    async def update_member_role(
        self,
        conversation_id: str,
        user_id: str,
        new_role: str,
    ) -> None:
        """更新成员角色"""
```

### 3.4 定时迁移任务

```python
class StorageMigrationTask:
    """存储迁移任务 - 每日 03:00 执行"""
    
    async def run(self) -> MigrationResult:
        """执行迁移：Redis -> PostgreSQL"""
        # 1. 查找 Redis 中 created_at < 8 天前的消息
        # 2. 同步到 PostgreSQL
        # 3. 从 Redis 删除已迁移的消息
        # 4. 清理过期的离线消息队列
        
@dataclass
class MigrationResult:
    migrated_count: int
    deleted_count: int
    errors: List[str]
```

---

## 4. 会话服务 (services/conversation_service.py)

### 4.1 ConversationService 接口

```python
class ConversationService:
    """会话服务"""
    
    def __init__(
        self,
        storage: LayeredStorageService,
        permission: PermissionService,
        event_bus: PluginEventBus,
    ):
        ...
    
    async def create_conversation(
        self,
        creator_id: str,
        type: str,  # "direct" | "group"
        name: Optional[str],
        member_ids: List[str],
        metadata: Optional[Dict] = None,
    ) -> ConversationRecord:
        """创建会话"""
        
    async def invite_member(
        self,
        conversation_id: str,
        inviter_id: str,
        user_id: str,
    ) -> MemberRecord:
        """邀请成员"""
        
    async def remove_member(
        self,
        conversation_id: str,
        remover_id: str,
        user_id: str,
    ) -> None:
        """移除成员"""
        
    async def update_member_role(
        self,
        conversation_id: str,
        updater_id: str,
        user_id: str,
        new_role: str,  # "admin" | "member"
    ) -> MemberRecord:
        """更新成员角色（设置 Admin）"""
        
    async def update_conversation(
        self,
        conversation_id: str,
        updater_id: str,
        name: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> ConversationRecord:
        """更新会话信息"""
        
    async def delete_conversation(
        self,
        conversation_id: str,
        deleter_id: str,
    ) -> None:
        """删除会话"""
        
    async def get_conversation(
        self,
        conversation_id: str,
        requester_id: str,
    ) -> Optional[ConversationRecord]:
        """获取会话详情"""
        
    async def list_conversations(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ConversationRecord]:
        """获取会话列表"""
```

### 4.2 业务规则

1. **创建会话**
   - `group` 类型必须提供 `name`
   - 创建者自动成为 `owner`
   - 初始成员被添加为 `member`

2. **邀请成员**
   - 只有 `admin` 或 `owner` 可以邀请
   - 不能重复邀请已存在的活跃成员
   - Agent 用户可以被邀请

3. **移除成员**
   - `owner` 可以移除任何人（包括其他 admin）
   - `admin` 可以移除 `member`，不能移除 `owner` 或其他 `admin`
   - 不能移除自己（需要先离开会话）

4. **角色变更**
   - 只有 `owner` 可以设置 `admin`
   - `owner` 不能被降级为 `admin`
   - Agent 用户可以被设置为 `admin`

5. **删除会话**
   - 只有 `owner` 可以删除会话
   - 删除前先清理所有成员

---

## 5. 消息服务 (services/message_service.py)

### 5.1 MessageService 接口

```python
class MessageService:
    """消息服务"""
    
    def __init__(
        self,
        storage: LayeredStorageService,
        permission: PermissionService,
        event_bus: PluginEventBus,
        ws_manager: ConnectionManager,  # 来自 api/websocket.py
    ):
        ...
    
    async def send_message(
        self,
        sender_id: str,
        conversation_id: str,
        content: str,
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> MessageRecord:
        """发送消息"""
        
    async def send_stream_message(
        self,
        sender_id: str,
        conversation_id: str,
        chunks: List[str],
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> MessageRecord:
        """发送流式消息（合并多个 chunk）"""
        
    async def edit_message(
        self,
        message_id: str,
        editor_id: str,
        new_content: str,
    ) -> MessageRecord:
        """编辑消息"""
        
    async def delete_message(
        self,
        message_id: str,
        deleter_id: str,
    ) -> None:
        """删除消息（软删除）"""
        
    async def get_message(
        self,
        message_id: str,
        requester_id: str,
    ) -> Optional[MessageRecord]:
        """获取消息"""
        
    async def get_conversation_messages(
        self,
        conversation_id: str,
        requester_id: str,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[MessageRecord]:
        """获取会话消息"""
```

### 5.2 业务规则

1. **发送消息**
   - 发送者必须是会话成员
   - 如果指定 `reply_to`，被回复的消息必须存在于同一会话
   - 消息同时写入 Redis（热数据）和 PostgreSQL（冷数据）
   - 通过 EventBus 发布 `message.sent` 事件

2. **编辑消息**
   - 只有消息发送者或 `admin`/`owner` 可以编辑
   - 普通 Agent 不能编辑自己的消息（除非是 Agent Admin）
   - 编辑后更新 `edited_at` 时间戳
   - 不能编辑已删除的消息

3. **删除消息**
   - 软删除：设置 `is_deleted=True` 和 `deleted_at`
   - 规则同编辑
   - 删除后消息内容不再显示

4. **回复引用**
   - `reply_to` 字段存储被回复消息的 ID
   - 前端根据此字段渲染回复引用样式

### 5.3 流式消息处理

```python
async def send_stream_message(
    self,
    sender_id: str,
    conversation_id: str,
    chunks: List[str],
    content_type: str = "text",
    mentions: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
) -> MessageRecord:
    """流式消息发送 - 合并多个 chunk 后统一存储和分发"""
    # 1. 验证发送权限
    # 2. 合并所有 chunks 为完整内容
    # 3. 创建消息记录
    # 4. 双写存储
    # 5. 通过 EventBus 和 WSManager 分发
    pass
```

---

## 6. 事件定义

### 6.1 事件类型

```python
# 会话相关事件
EVENT_CONVERSATION_CREATED = "conversation.created"
EVENT_CONVERSATION_UPDATED = "conversation.updated"
EVENT_CONVERSATION_DELETED = "conversation.deleted"

# 成员相关事件
EVENT_MEMBER_JOINED = "member.joined"
EVENT_MEMBER_LEFT = "member.left"
EVENT_MEMBER_ROLE_CHANGED = "member.role_changed"

# 消息相关事件
EVENT_MESSAGE_SENT = "message.sent"
EVENT_MESSAGE_EDITED = "message.edited"
EVENT_MESSAGE_DELETED = "message.deleted"
```

### 6.2 事件载荷

```python
# member.joined
{
    "conversation_id": str,
    "user_id": str,
    "member": {
        "user_id": str,
        "role": str,
        "nickname": Optional[str],
    }
}

# message.sent
{
    "id": str,
    "conversation_id": str,
    "sender_id": str,
    "content": str,
    "content_type": str,
    "mentions": List[str],
    "reply_to": Optional[str],
    "created_at": str,  # ISO format
}
```

---

## 7. 目录结构

```
src/sprinkle/
├── kernel/
│   ├── __init__.py
│   ├── session.py        # Phase 2
│   ├── message.py        # Phase 2
│   ├── event.py          # Phase 2
│   ├── auth.py           # Phase 2
│   └── permission.py     # Phase 6 (NEW)
├── storage/
│   ├── __init__.py
│   ├── database.py       # Phase 2
│   └── layered.py        # Phase 6 (NEW)
├── services/
│   ├── __init__.py       # Phase 6 (NEW)
│   ├── conversation_service.py  # Phase 6 (NEW)
│   └── message_service.py       # Phase 6 (NEW)
├── api/
│   ├── __init__.py
│   ├── conversations.py   # Phase 4
│   ├── messages.py       # Phase 4
│   ├── websocket.py      # Phase 5
│   └── events.py         # Phase 5
├── plugins/
│   └── ...
└── models/
    └── ...
```

---

## 8. 实现顺序

1. **kernel/permission.py** - 权限服务（其他模块依赖）
2. **storage/layered.py** - 分层存储（服务层依赖）
3. **services/conversation_service.py** - 会话服务
4. **services/message_service.py** - 消息服务

---

## 9. 测试策略

### 9.1 单元测试

- `tests/test_permission.py` - 权限矩阵测试
- `tests/test_layered_storage.py` - 分层存储测试
- `tests/test_conversation_service.py` - 会话服务测试
- `tests/test_message_service.py` - 消息服务测试

### 9.2 测试覆盖率目标

> 80%

### 9.3 验证要点

- 权限矩阵所有组合覆盖
- 双写一致性（Redis + PostgreSQL）
- 软删除正确性
- 事件发布正确性

---

## 10. 依赖接口

### 10.1 来自 Phase 2

```python
from sprinkle.kernel.auth import AuthService, UserCredentials
from sprinkle.kernel.session import SessionManager
from sprinkle.kernel.event import PluginEventBus
```

### 10.2 来自 Phase 5

```python
from sprinkle.api.websocket import ConnectionManager
```

### 10.3 来自 Phase 2 (storage/database.py)

```python
from sprinkle.storage.database import get_async_engine, get_async_session
```

---

*设计文档由司康编写~🍪*
