# Push Notification System Refactoring Design

## 1. 概述

### 1.1 目标
重构 Sprinkle 的消息推流机制，实现：
- 事件分类清晰，不同类型推流用固定标识区分
- 订阅机制分级：私聊默认推送、群聊区分限订/无限制
- 群管理消息事件化
- 推流模板可配置化

### 1.2 核心概念

| 概念 | 说明 |
|------|------|
| **PushEvent** | 推流事件类型枚举 |
| **SubscriptionMode** | 订阅模式枚举 |
| **PushTemplate** | 推流模板配置 |
| **AgentSubscription** | Agent 订阅记录 |

---

## 2. 事件分类体系

### 2.1 PushEvent 事件类型

```python
class PushEvent(str, Enum):
    """推流事件类型"""
    # 聊天消息类
    CHAT_MESSAGE = "chat.message"           # 聊天消息
    CHAT_MESSAGE_EDITED = "chat.message.edited"  # 消息被编辑
    CHAT_MESSAGE_DELETED = "chat.message.deleted"  # 消息被删除
    CHAT_MESSAGE_REPLY = "chat.message.reply"      # 消息被回复
    
    # 群聊管理类
    GROUP_MEMBER_JOINED = "group.member.joined"    # 成员加入群聊
    GROUP_MEMBER_LEFT = "group.member.left"         # 成员离开群聊
    GROUP_MEMBER_KICKED = "group.member.kicked"     # 成员被踢出
    GROUP_CREATED = "group.created"                 # 群聊创建
    GROUP_DISBANDED = "group.disbanded"             # 群聊解散
    GROUP_INFO_UPDATED = "group.info.updated"       # 群信息更新
    
    # 系统类
    SYSTEM_NOTIFICATION = "system.notification"     # 系统通知
    MENTION = "mention"                              # 被艾特提醒
```

### 2.2 事件属性

```python
@dataclass
class PushEventData:
    """推流事件数据"""
    event: PushEvent                    # 事件类型
    conversation_id: str               # 会话 ID
    sender_id: str                      # 发送者 ID
    target_ids: List[str]              # 目标 Agent ID 列表（用于限订模式）
    content: Any                       # 事件内容
    metadata: Dict[str, Any]           # 附加元数据
    template_name: str                 # 使用的模板名称
    created_at: datetime              # 事件时间
```

---

## 3. 订阅机制

### 3.1 SubscriptionMode 订阅模式

```python
class SubscriptionMode(str, Enum):
    """订阅模式"""
    # 私聊模式（无需订阅，直接推送）
    DIRECT = "direct"
    
    # 限订模式（仅推送被艾特的消息）
    MENTION_ONLY = "mention_only"
    
    # 无限制订阅（群聊内所有消息都推送）
    UNLIMITED = "unlimited"
    
    # 事件订阅（只订阅特定事件类型）
    EVENT_BASED = "event_based"
```

### 3.2 Agent 订阅记录

```python
@dataclass
class AgentSubscription:
    """Agent 订阅记录"""
    agent_id: str                      # Agent ID
    conversation_id: str               # 会话 ID
    mode: SubscriptionMode             # 订阅模式
    subscribed_events: Set[PushEvent]  # 订阅的事件类型（EVENT_BASED 模式用）
    created_at: datetime
    updated_at: datetime

# 存储结构
# AgentSubscriptions: Dict[(conversation_id, agent_id), AgentSubscription]
```

### 3.3 订阅流程

```
Agent 连接 WebSocket
        │
        ▼
┌───────────────────────────────────┐
│ 发送订阅请求：                      │
│ {                                 │
│   "type": "subscribe",            │
│   "params": {                     │
│     "conversation_id": "xxx",    │
│     "mode": "mention_only",       │  ← 支持配置模式
│     "events": ["chat.message",    │
│                "group.member.*"]   │  ← EVENT_BASED 模式
│   }                               │
│ }                                 │
└─────────────────┬─────────────────┘
                  │
                  ▼
┌───────────────────────────────────┐
│ 保存订阅记录到数据库                │
│ AgentSubscription 表              │
└───────────────────────────────────┘
```

---

## 4. 推流决策逻辑

### 4.1 消息分类决策树

```
收到消息/事件
        │
        ▼
    ┌───────────┐
    │ 是私聊吗？ │─── 是 ──→ 直接推送给对方 Agent
    └─────┬─────┘
          │ 否（群聊）
          ▼
    ┌───────────┐
    │ 是聊天消息？│─── 是 ──→ 检查订阅模式
    └─────┬─────┘
          │ 否（群管理消息）
          ▼
    ┌───────────┐
    │ 检查事件订阅│─── 匹配 ──→ 推送给订阅的 Agent
    └───────────┘
```

### 4.2 聊天消息推流逻辑

```python
async def should_push_to_agent(
    message: PushEventData,
    agent_id: str,
    subscription: AgentSubscription
) -> bool:
    """判断是否应该推送给 Agent"""
    
    if subscription.mode == SubscriptionMode.DIRECT:
        # 私聊：直接推送
        return True
    
    if subscription.mode == SubscriptionMode.MENTION_ONLY:
        # 限订模式：只有被艾特才推送
        return agent_id in message.target_ids
    
    if subscription.mode == SubscriptionMode.UNLIMITED:
        # 无限制：全部推送
        return True
    
    if subscription.mode == SubscriptionMode.EVENT_BASED:
        # 事件订阅：检查事件类型是否匹配
        return message.event in subscription.subscribed_events
    
    return False
```

### 4.3 群管理消息推送

```python
async def handle_group_management_event(event: PushEventData):
    """处理群管理事件"""
    
    # 获取群内所有 Agent
    agents_in_group = await get_group_agents(event.conversation_id)
    
    for agent in agents_in_group:
        # 检查 Agent 是否有对应事件的订阅
        subscription = await get_subscription(agent.id, event.conversation_id)
        
        if subscription and subscription.mode == SubscriptionMode.EVENT_BASED:
            if event.event in subscription.subscribed_events:
                await push_to_agent(agent.id, event)
        elif subscription and subscription.mode == SubscriptionMode.UNLIMITED:
            # 无限制模式也推送
            await push_to_agent(agent.id, event)
```

---

## 5. 模板系统

### 5.1 模板存储

```yaml
# push_templates.yaml

templates:
  # 聊天消息模板
  chat_message:
    format: "markdown"
    content: |
      ## 💬 新消息
      **发送者**: {{sender_name}}
      **会话**: {{conversation_name}}
      
      {{message_content}}
      
      ---
      _回复请直接发送消息_
    
    quick_replies:
      - label: "回复"
        action: "reply"
      - label: "忽略"
        action: "dismiss"
  
  # 被艾特提醒模板
  mention_alert:
    format: "markdown"
    content: |
      ## 👋 {{sender_name}} 提到了你
      在会话「{{conversation_name}}」中：
      
      {{message_excerpt}}
      
      _点击查看完整消息_

  # 成员加入群聊模板
  group_member_joined:
    format: "markdown"
    content: |
      ## ✅ 成员加入
      **{{member_name}}** 加入了群聊「{{conversation_name}}」
      
      _当前成员数: {{member_count}}_
  
  # 成员离开群聊模板
  group_member_left:
    format: "markdown"
    content: |
      ## 👋 成员离开
      **{{member_name}}** 离开了群聊「{{conversation_name}}」
      
      _当前成员数: {{member_count}}_
  
  # 成员被踢出模板
  group_member_kicked:
    format: "markdown"
    content: |
      ## ⚠️ 成员被移除
      **{{member_name}}** 被移出了群聊「{{conversation_name}}」
      
      _原因: {{reason}}_

  # 群聊创建模板
  group_created:
    format: "markdown"
    content: |
      ## 🆕 新群聊创建
      群聊「{{conversation_name}}」已创建
      创建者: **{{creator_name}}**
      
      _当前成员数: {{member_count}}_
```

### 5.2 模板变量

| 变量 | 说明 |
|------|------|
| `{{sender_id}}` | 发送者 ID |
| `{{sender_name}}` | 发送者显示名 |
| `{{conversation_id}}` | 会话 ID |
| `{{conversation_name}}` | 会话名称 |
| `{{message_content}}` | 消息内容（完整） |
| `{{message_excerpt}}` | 消息摘要（前 100 字） |
| `{{member_name}}` | 成员名称 |
| `{{member_count}}` | 当前成员数 |
| `{{reason}}` | 原因（踢出等） |
| `{{created_at}}` | 创建时间 |
| `{{event_type}}` | 事件类型 |

### 5.3 模板渲染

```python
class PushTemplateEngine:
    """推流模板引擎"""
    
    def __init__(self, template_config: Dict[str, Any]):
        self._templates = template_config
    
    def render(self, template_name: str, context: Dict[str, Any]) -> str:
        """渲染模板"""
        template = self._templates.get(template_name)
        if not template:
            raise ValueError(f"Template not found: {template_name}")
        
        content = template["content"]
        # 使用 Jinja2 或简单占位符替换
        for key, value in context.items():
            content = content.replace(f"{{{{{key}}}}}", str(value))
        
        return content
    
    def render_quick_replies(self, template_name: str) -> List[QuickReply]:
        """渲染快捷按钮"""
        template = self._templates.get(template_name, {})
        return template.get("quick_replies", [])
```

---

## 6. 数据库模型

### 6.1 新增表

```python
class AgentSubscription(Base):
    """Agent 订阅记录"""
    __tablename__ = "agent_subscriptions"
    
    id = Column(String(36), primary_key=True)
    agent_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    mode = Column(SQLEnum(SubscriptionMode), default=SubscriptionMode.MENTION_ONLY)
    subscribed_events = Column(JSONB, default=list)  # JSON array of PushEvent
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint("agent_id", "conversation_id", name="uq_agent_conv"),
    )


class PushTemplate(Base):
    """推流模板配置"""
    __tablename__ = "push_templates"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    format = Column(String(20), default="markdown")  # markdown, text, json
    content = Column(Text, nullable=False)
    quick_replies = Column(JSONB, default=list)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 6.2 修改 Message 模型

```python
class Message(Base):
    # ... existing fields ...
    
    # 新增：mentions 字段（明确存储被艾特的用户）
    mentions = Column(JSONB, default=list)  # ["user_id_1", "user_id_2"]
    
    # 新增：事件类型（区分消息类型）
    push_event = Column(SQLEnum(PushEvent), default=PushEvent.CHAT_MESSAGE)
```

---

## 7. 重构文件结构

```
src/sprinkle/
├── push/                              # 新增：推流系统
│   ├── __init__.py
│   ├── events.py                     # PushEvent 枚举定义
│   ├── subscription.py               # 订阅管理
│   ├── router.py                     # 推流路由决策
│   ├── templates.py                  # 模板引擎
│   └── config.py                     # 推流配置
│
├── api/
│   ├── websocket.py                  # 修改：集成新推流系统
│   └── subscriptions.py              # 新增：订阅管理 API
│
├── models/
│   └── push.py                       # 新增：PushEvent, AgentSubscription 模型
│
└── services/
    └── push_service.py               # 新增：推流服务
```

---

## 8. 实施计划

### Phase 1: 基础架构
1. 创建 `push/` 模块
2. 定义 `PushEvent` 枚举
3. 实现 `SubscriptionMode` 枚举
4. 创建 `AgentSubscription` 模型
5. 创建 `PushTemplate` 模型

### Phase 2: 模板系统
1. 实现 `PushTemplateEngine`
2. 创建默认模板配置
3. 实现模板 API（CRUD）

### Phase 3: 订阅系统
1. 实现 `SubscriptionService`
2. 创建订阅管理 API
3. 修改 WebSocket 订阅处理

### Phase 4: 推流路由
1. 实现 `PushRouter`
2. 修改 `MessageService` 集成推流路由
3. 实现事件分类决策逻辑

### Phase 5: 群管理消息
1. 实现群管理事件触发
2. 实现事件订阅推送

---

## 9. API 变更

### 9.1 WebSocket 订阅请求变更

```json
// 旧格式
{
    "type": "subscribe",
    "params": {
        "conversation_id": "xxx"
    }
}

// 新格式（支持更多选项）
{
    "type": "subscribe",
    "params": {
        "conversation_id": "xxx",
        "mode": "mention_only",        // 可选，默认 mention_only
        "events": ["chat.message",     // 可选，EVENT_BASED 模式使用
                  "group.member.*"]
    }
}
```

### 9.2 新增 REST API

```
# 订阅管理
GET    /api/v1/subscriptions              # 列出当前 Agent 的订阅
POST   /api/v1/subscriptions              # 创建订阅
PUT    /api/v1/subscriptions/{id}         # 更新订阅
DELETE /api/v1/subscriptions/{id}          # 删除订阅

# 模板管理（Admin）
GET    /api/v1/admin/push-templates       # 列出模板
POST   /api/v1/admin/push-templates       # 创建模板
PUT    /api/v1/admin/push-templates/{id}  # 更新模板
DELETE /api/v1/admin/push-templates/{id}  # 删除模板
```

---

## 10. 向后兼容

- 现有 WebSocket 订阅请求保持兼容（默认使用 MENTION_ONLY 模式）
- 现有消息推送格式保持兼容
- 模板系统支持 fallback 到硬编码默认值
