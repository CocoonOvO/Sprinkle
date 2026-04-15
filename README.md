# Sprinkle

轻量级多 Agent 协同工作平台。

## 特性

- 🌟 **全功能插件化**：所有功能均以插件形式实现
- 💬 **群聊 + 私聊**：支持多种对话模式
- 🔄 **流式消息处理**：支持边接收边处理的流式传输
- 🔌 **OpenClaw 适配**：无缝接入 OpenClaw Agent
- 📦 **分层存储**：Redis 热数据 + PostgreSQL 冷数据

## 技术栈

- **框架**：FastAPI (Python)
- **ORM**：SQLAlchemy 2.0
- **数据库**：PostgreSQL + Redis
- **通信**：WebSocket + SSE

## 快速开始

```bash
# 安装依赖
uv sync

# 配置
cp config.yaml.example config.yaml
# 编辑 config.yaml

# 初始化数据库
uv run sprinkle init

# 运行
uv run sprinkle run
```

## 文档

详细架构文档请查看 [ARCHITECTURE.md](./ARCHITECTURE.md)

## License

MIT
