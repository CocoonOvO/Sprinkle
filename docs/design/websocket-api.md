# WebSocket API 设计

## 概述

Sprinkle 使用 WebSocket 实现实时消息推送，支持：
- 新消息推送
- 消息编辑/删除推送
- 群组事件推送
- 文件上传状态推送（异步上传场景）

## 连接

```
WS /ws?token={jwt_token}
```

### 认证方式

通过 URL query parameter 传入 JWT token：
```
ws://host:port/ws?token=xxx
```

## 消息格式

### 客户端发送
```json
{
  "type": "event_type",
  "data": {...}
}
```

### 服务端推送
```json
{
  "event": "event_type",
  "data": {...},
  "conversation_id": "uuid",
  "sender_id": "uuid",
  "created_at": "ISO8601"
}
```

## 事件类型

### 消息事件
| 事件 | 说明 | data |
|------|------|------|
| `chat.message` | 新消息 | `{id, content, content_type, sender_id, message_metadata, created_at, ...}` |
| `chat.message.edited` | 消息编辑 | `{id, content, edited_at, ...}` |
| `chat.message.deleted` | 消息删除 | `{id, deleted_at, ...}` |

### 群组事件
| 事件 | 说明 | data |
|------|------|------|
| `group.member.joined` | 成员加入 | `{user_id, conversation_id, role, ...}` |
| `group.member.left` | 成员离开 | `{user_id, conversation_id, ...}` |
| `group.member.kicked` | 成员被踢 | `{user_id, conversation_id, ...}` |
| `group.created` | 会话创建 | `{conversation_id, type, name, ...}` |
| `group.disbanded` | 会话解散 | `{conversation_id, ...}` |
| `group.info.updated` | 会话信息更新 | `{conversation_id, name, avatar_url, ...}` |

### 文件上传事件
| 事件 | 说明 | data |
|------|------|------|
| `file.upload.progress` | 分片上传进度 | `{file_id, message_id, chunk_index, total_chunks, received_chunks, progress_percent, status}` |
| `file.upload.completed` | 文件上传完成 | `{file_id, message_id, file_name, file_size, mime_type}` |
| `file.upload.failed` | 文件上传失败 | `{file_id, message_id, error}` |

## 异步文件上传场景

异步文件上传使用 WebSocket 实现多端状态同步：

```
用户A 选择文件 → 创建消息（file_ids） → WebSocket 广播（uploading 状态）
用户B 收到消息 → 显示"上传中"
用户A 后台上传 → 上传完成 → WebSocket 推送 file.upload.completed
用户B 收到事件 → 更新消息状态为"完成"
```

## 实现文件

- WebSocket 入口：`src/sprinkle/api/websocket.py`
- 事件定义：`src/sprinkle/push/events.py`
- 连接管理：`ConnectionManager` 类

## 前端示例

```javascript
const ws = new WebSocket('ws://localhost:8003/ws?token=' + token);

ws.onopen = () => console.log('Connected');
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  switch(msg.event) {
    case 'chat.message':
      console.log('新消息:', msg.data);
      break;
    case 'file.upload.completed':
      console.log('文件上传完成:', msg.data);
      break;
    case 'file.upload.failed':
      console.log('文件上传失败:', msg.data.error);
      break;
  }
};
ws.onclose = () => console.log('Disconnected');
```