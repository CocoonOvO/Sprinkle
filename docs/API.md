# Sprinkle API 文档

## 基础信息

- **Base URL**: `http://localhost:8002`
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
Body: {"content": "xxx", "content_type": "text"|"markdown", "reply_to_id": "uuid"}
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

## WebSocket

```
WS /ws?token={jwt_token}
```

## 动态文档

启动服务后访问：
- Swagger UI: http://localhost:8002/docs
- ReDoc: http://localhost:8002/redoc
