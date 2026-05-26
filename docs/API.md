# Sprinkle API 文档

## 基础信息

- **Base URL**: `http://localhost:8003`
- **API 版本**: v1
- **认证**: Bearer Token (JWT)

## 认证 API

### 注册用户
```
POST /api/v1/auth/register
Body: {"username": "xxx", "password": "xxx", "display_name": "xxx"}
Response: {"id": "uuid", "username": "xxx", ...}
```

### 登录
```
POST /api/v1/auth/login
Body: {"username": "xxx", "password": "xxx"}
Response: {"access_token": "xxx", "refresh_token": "xxx", ...}
```

### 刷新 Token
```
POST /api/v1/auth/refresh
Body: {"refresh_token": "xxx"}
Response: {"access_token": "xxx", "refresh_token": "xxx", ...}
```

## 会话 API

### 创建会话
```
POST /api/v1/conversations
Headers: Authorization: Bearer {token}
Body: {"type": "direct"|"group", "member_ids": [], "name": "xxx"}
Response: {"id": "uuid", "type": "xxx", ...}
```

### 获取会话列表
```
GET /api/v1/conversations
Headers: Authorization: Bearer {token}
Response: [{"id": "uuid", "type": "xxx", ...}, ...]
```

### 获取会话详情
```
GET /api/v1/conversations/{id}
Headers: Authorization: Bearer {token}
Response: {"id": "uuid", "name": "xxx", ...}
```

## 消息 API

### 发送消息
```
POST /api/v1/conversations/{id}/messages
Headers: Authorization: Bearer {token}
Body: {"content": "xxx", "content_type": "text"|"markdown", "reply_to_id": "uuid", "file_ids": []}
Response: {"id": "uuid", "content": "xxx", ...}
```

### 获取消息列表
```
GET /api/v1/conversations/{id}/messages?page=1&page_size=20
Headers: Authorization: Bearer {token}
Response: [{"id": "uuid", "content": "xxx", ...}, ...]
```

### 编辑消息
```
PUT /api/v1/conversations/{id}/messages/{msg_id}
Headers: Authorization: Bearer {token}
Body: {"content": "xxx"}
Response: {"id": "uuid", "content": "xxx", ...}
```

### 删除消息
```
DELETE /api/v1/conversations/{id}/messages/{msg_id}
Headers: Authorization: Bearer {token}
Response: {"status": "deleted"}
```

## 成员 API

### 添加成员
```
POST /api/v1/conversations/{id}/members
Headers: Authorization: Bearer {token}
Body: {"user_id": "uuid", "role": "admin"|"member"}
Response: {"id": "uuid", ...}
```

### 移除成员
```
DELETE /api/v1/conversations/{id}/members/{user_id}
Headers: Authorization: Bearer {token}
Response: {"status": "removed"}
```

## 文件 API

### 同步上传文件（传统方式）
```
POST /api/v1/files/upload
Headers: Authorization: Bearer {token}
Body: multipart/form-data (file: xxx, conversation_id: optional)
Response: {"id": "uuid", "file_name": "xxx", "status": "success", ...}
```

### 异步上传 - 初始化
```
POST /api/v1/files/upload-async
Headers: Authorization: Bearer {token}
Body: {"file_name": "xxx", "file_size": 1024, "conversation_id": "uuid", "message_id": "uuid"}
Response: {"file_id": "uuid", "status": "uploading", "upload_url": "/api/v1/files/{file_id}/upload-content"}
```

### 异步上传 - 上传文件内容
```
POST /api/v1/files/{file_id}/upload-content
Headers: Authorization: Bearer {token}
Body: multipart/form-data (file: xxx)
Response: {"id": "uuid", "status": "success", ...}
```

### 下载文件
```
GET /api/v1/files/{file_id}
Response: 文件二进制流
```

### 删除文件
```
DELETE /api/v1/files/{file_id}
Headers: Authorization: Bearer {token}
Response: 204 No Content
```

## WebSocket

### 连接
```
WS /ws?token={jwt_token}
```

### 认证
连接时通过 URL query parameter 传入 JWT token。

### 消息格式
客户端发送：
```json
{"type": "event_type", "data": {...}}
```

服务端推送：
```json
{
  "event": "event_type",
  "data": {...},
  "conversation_id": "uuid",
  "sender_id": "uuid"
}
```

### 事件类型

#### 消息事件
| 事件 | 说明 | data |
|------|------|------|
| `chat.message` | 新消息 | `{content, content_type, sender_id, message_metadata: {file_ids}, ...}` |
| `chat.message.edited` | 消息编辑 | `{message_id, content, ...}` |
| `chat.message.deleted` | 消息删除 | `{message_id, ...}` |

#### 群组事件
| 事件 | 说明 | data |
|------|------|------|
| `group.member.joined` | 成员加入 | `{user_id, conversation_id, ...}` |
| `group.member.left` | 成员离开 | `{user_id, conversation_id, ...}` |
| `group.created` | 会话创建 | `{conversation_id, ...}` |

#### 文件上传事件
| 事件 | 说明 | data |
|------|------|------|
| `file.upload.completed` | 文件上传完成 | `{file_id, message_id, file_url}` |
| `file.upload.failed` | 文件上传失败 | `{file_id, message_id, error}` |

### 异步文件上传流程

1. 初始化上传，获得 file_id
2. 创建消息时传入 file_ids（消息立即显示，文件状态为 uploading）
3. 后台上传文件内容
4. 通过 WebSocket 推送获取上传结果

#### 示例
```javascript
// 1. 初始化上传
const res = await fetch('/api/v1/files/upload-async', {
  method: 'POST',
  headers: {'Authorization': 'Bearer xxx', 'Content-Type': 'application/json'},
  body: JSON.stringify({file_name: 'test.pdf', file_size: 1024})
});
const {file_id} = await res.json();

// 2. 创建消息
const msg = await fetch('/api/v1/conversations/{conv_id}/messages', {
  method: 'POST',
  headers: {'Authorization': 'Bearer xxx', 'Content-Type': 'application/json'},
  body: JSON.stringify({content: '看这个文件', content_type: 'file', file_ids: [file_id]})
});

// 3. 后台上传文件内容
await fetch(`/api/v1/files/${file_id}/upload-content`, {
  method: 'POST',
  headers: {'Authorization': 'Bearer xxx'},
  body: formData  // multipart/form-data
});

// 4. 监听 WebSocket 推送
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.event === 'file.upload.completed') {
    console.log('文件上传完成', msg.data);
  } else if (msg.event === 'file.upload.failed') {
    console.log('文件上传失败', msg.data.error);
  }
};
```

## 动态文档

启动服务后访问：
- Swagger UI: http://localhost:8003/docs
- ReDoc: http://localhost:8003/redoc