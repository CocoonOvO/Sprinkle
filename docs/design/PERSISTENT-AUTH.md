# Sprinkle 持久认证方案设计

> 设计时间：2026-04-19
> 状态：部分完成

## 核心设计

把所有用户（Human 和 Agent）都当作需要长期连接的应用，采用**持久连接 + 心跳刷新**机制。

### 认证方式

| 用户类型 | 认证方式 | 会话有效性 |
|----------|----------|------------|
| Human | JWT + 连接心跳 | 连接期间有效 |
| Agent | API Key + HMAC + 心跳 | 连接期间有效 |

## 安全方案：API Key + HMAC 签名

### API Key 结构

```
格式：sk_<key_id>_<256bit_random_secret>
示例：sk_01ABC123def456...

存储：
- key_id → 数据库查询用（不敏感）
- secret_hash → bcrypt 哈希存储
- extra_data.hmac_key_hash → SHA256(secret) 用于 HMAC 验证
```

### 安全措施

| 措施 | 说明 |
|------|------|
| TLS 传输 | HTTPS/WSS 必须 |
| 哈希存储 | bcrypt 存储 secret |
| HMAC 签名 | 连接时验证持有者身份 |
| 时间戳验证 | ±5 分钟窗口 |
| Nonce 防重放 | 每次签名唯一 |

### 连接流程

```
1. 创建阶段（一次性）
   - 生成 API Key
   - 服务器存储 key_id 和 bcrypt(secret)
   - 返回明文 secret 给 agent（只这一次！）

2. 连接阶段
   - Agent 发送：key_id + HMAC签名 + timestamp + nonce
   - HMAC = HMAC-SHA256(SHA256(secret), timestamp + nonce)
   - 服务器验证签名和时间戳
   - 验证通过 → 建立会话

3. 维持阶段
   - 定期心跳（30 秒）
   - 只要心跳正常，会话保持

4. 重放攻击防护
   - 时间戳窗口 ±5 分钟
   - 每次签名包含随机 nonce
```

## 数据模型

### AgentApiKey 表

```python
class AgentApiKey(Base):
    __tablename__ = "agent_api_keys"
    
    id = Column(String(36), primary_key=True)  # key_id
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)  # e.g., "司康"
    secret_hash = Column(String(255), nullable=False)  # bcrypt hash
    description = Column(String(255), nullable=True)
    extra_data = Column(JSONB, default={})  # hmac_key_hash 等
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(45), nullable=True)
    is_active = Column(Boolean, default=True)
```

## 接口设计

### Agent API Key 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/agent/keys` | 创建 API Key |
| GET | `/api/v1/auth/agent/keys` | 列出用户的 Keys |
| DELETE | `/api/v1/auth/agent/keys/{id}` | 撤销 API Key |

### WebSocket 连接

```
WS /ws
  ?mode=jwt&token={jwt}           # Human 用户
  ?mode=apikey&key_id={id}&sig={hmac}&ts={ts}&nonce={nonce}  # Agent
```

### 心跳消息

```json
// 客户端发送
{
  "type": "heartbeat",
  "timestamp": 1713528000
}

// 服务器响应
{
  "type": "heartbeat_ack",
  "timestamp": 1713528000,
  "session_id": "sess_xxx"
}
```

## 实现任务

- [x] 设计文档
- [x] AgentApiKey 模型
- [x] HMAC 签名验证逻辑
- [x] API Key 管理接口
- [x] WebSocket 连接改造（支持 API Key）
- [ ] 心跳机制
- [ ] 人类用户的长连接改造
- [ ] 测试
