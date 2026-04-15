# Phase 7: 集成测试设计文档

> 版本：v1.0  
> 更新日期：2026-04-15  
> 状态：📐 设计完成

---

## 1. 概述

### 1.1 目标

基于 Sprinkle 项目实际使用场景，设计并实现全面的集成测试，覆盖：
- 用户认证流程（注册、登录、Token 刷新）
- 会话管理流程（创建、邀请成员、角色变更）
- 消息流程（发送、编辑、删除、分页获取）
- 插件系统流程（加载、事件订阅、消息处理）
- WebSocket/SSE 流程（连接认证、订阅、收发消息）
- 权限流程（Owner/Admin/Member/Agent 权限矩阵）
- 存储流程（Redis + PostgreSQL 双写）

### 1.2 测试覆盖率目标

> **集成测试覆盖率 > 70%**（整体项目覆盖率目标 74%）

---

## 2. 测试环境

### 2.1 依赖服务

| 服务 | 状态 | 用途 |
|------|------|------|
| PostgreSQL | ✅ 运行中 | 持久化存储 |
| Redis | ⚠️ 使用 fakeredis | 热数据存储（测试环境） |

### 2.2 测试配置

使用 `fakeredis` 模拟 Redis，PostgreSQL 使用本地实例。

---

## 3. 测试用例设计

### 3.1 用户注册登录流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| AUTH_01 | 用户注册 → 登录 → 获取 Token | 注册成功，登录成功，返回 access_token |
| AUTH_02 | Token 刷新 | 旧 token 刷新后获取新 token |
| AUTH_03 | 错误密码登录 | 返回 401 错误 |
| AUTH_04 | 重复用户名注册 | 返回 400 错误 |

### 3.2 会话管理流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| CONV_01 | 创建私聊会话（direct） | 创建成功，返回会话信息 |
| CONV_02 | 创建群聊会话（group） | 创建成功，creator 为 owner |
| CONV_03 | 创建群聊不提供名称 | 返回 400 错误 |
| CONV_04 | 邀请成员（admin） | 成员添加成功 |
| CONV_05 | 普通成员邀请成员 | 返回 403 权限错误 |
| CONV_06 | 移除成员（admin） | 成员移除成功 |
| CONV_07 | Owner 设置 Agent 为 Admin | 角色变更成功 |
| CONV_08 | 转让所有权 | 所有权转让成功 |

### 3.3 消息流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| MSG_01 | 发送文本消息 | 消息发送成功 |
| MSG_02 | 发送 Markdown 消息 | 消息发送成功，content_type 为 markdown |
| MSG_03 | 回复消息 | reply_to 字段正确设置 |
| MSG_04 | 编辑自己的消息 | 编辑成功，edited_at 更新 |
| MSG_05 | 普通 Agent 编辑自己的消息 | 返回 403 权限错误 |
| MSG_06 | 删除自己的消息（软删除） | is_deleted 标记为 true |
| MSG_07 | 分页获取消息 | 返回正确数量的消息和分页 cursor |
| MSG_08 | 引用不存在的消息 | 返回 400 错误 |

### 3.4 插件流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| PLUGIN_01 | 加载内置 HelloWorld 插件 | 插件加载成功 |
| PLUGIN_02 | 加载内置 MessageLogger 插件 | 插件加载成功 |
| PLUGIN_03 | 消息经过插件处理 | 插件 on_message 被调用 |
| PLUGIN_04 | 插件事件订阅 | 事件触发后插件收到通知 |

### 3.5 WebSocket/SSE 流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| WS_01 | WebSocket 连接认证 | 连接成功 |
| WS_02 | WebSocket 无效 Token 连接 | 连接被拒绝 |
| WS_03 | 订阅会话 | 订阅成功 |
| WS_04 | 通过 WebSocket 发送消息 | 消息发送成功 |
| WS_05 | 接收订阅会话的消息 | 消息正确接收 |
| WS_06 | SSE 连接认证 | 连接成功 |
| WS_07 | SSE 事件接收 | 事件正确接收 |

### 3.6 权限流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| PERM_01 | 普通成员不能添加成员 | 返回 403 |
| PERM_02 | Admin 可以添加成员 | 添加成功 |
| PERM_03 | Admin 不能设置管理员 | 返回 403 |
| PERM_04 | Owner 可以设置管理员 | 设置成功 |
| PERM_05 | Agent 普通成员不能编辑消息 | 返回 403 |
| PERM_06 | Agent Admin 可以编辑消息 | 编辑成功 |

### 3.7 存储流程

| 测试用例 ID | 描述 | 预期结果 |
|------------|------|----------|
| STOR_01 | 消息双写 Redis + PostgreSQL | 两处都有数据 |
| STOR_02 | 从 Redis 读取热数据 | 读取成功 |
| STOR_03 | 离线消息队列 | 消息被加入离线队列 |
| STOR_04 | 软删除消息 | PostgreSQL 中 is_deleted=true |

---

## 4. 测试文件结构

```
tests/
├── test_phase1_init.py          # 单元测试
├── test_phase2_kernel.py        # 单元测试
├── test_phase3_plugins.py       # 单元测试
├── test_phase4_api.py           # 单元测试
├── test_phase5_websocket.py    # 单元测试
├── test_phase6_business.py      # 单元测试
└── test_integration.py         # 集成测试 (NEW)
```

---

## 5. 测试实现要点

### 5.1 使用 fakeredis 模拟 Redis

```python
import fakeredis.aioredis

@pytest.fixture
async def redis_client():
    """Create fake Redis client for testing."""
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.close()
```

### 5.2 使用 FastAPI TestClient

```python
from fastapi.testclient import TestClient
from sprinkle.main import app

@pytest.fixture
def client():
    return TestClient(app)
```

### 5.3 实际 HTTP 请求测试

集成测试使用实际的 HTTP 请求，不使用 mock：

```python
def test_register_and_login(client):
    # 注册
    register_response = client.post("/api/v1/auth/register", json={...})
    assert register_response.status_code == 201
    
    # 登录
    login_response = client.post("/api/v1/auth/login", json={...})
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()
```

### 5.4 WebSocket 测试

```python
def test_websocket_connect(client, auth_headers):
    with client.websocket_connect("/ws?token=xxx") as websocket:
        # 订阅会话
        websocket.send_json({"type": "subscribe", "params": {...}})
        # 发送消息
        websocket.send_json({"type": "message", "params": {...}})
        # 接收消息
        data = websocket.receive_json()
```

---

## 6. 验证要点

### 6.1 响应状态码验证
- 成功操作返回 2xx 状态码
- 权限错误返回 403
- 资源不存在返回 404
- 参数错误返回 400

### 6.2 数据一致性验证
- 数据库记录与返回数据一致
- 双写数据一致性（Redis + PostgreSQL）
- 软删除标记正确

### 6.3 事件触发验证
- WebSocket 消息推送
- SSE 事件通知
- 插件钩子调用

---

## 7. 执行方式

```bash
# 运行所有测试
python3 -m pytest tests/ -v --cov=sprinkle

# 仅运行集成测试
python3 -m pytest tests/test_integration.py -v

# 生成覆盖率报告
python3 -m pytest tests/ --cov=sprinkle --cov-report=html
```

---

## 8. 预期结果

- 所有集成测试用例通过
- 整体测试覆盖率保持 > 70%
- 无关键级别错误

---

*设计文档由司康编写~🍪*
