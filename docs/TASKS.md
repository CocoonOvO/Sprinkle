# Sprinkle 开发任务管理

> 版本：v0.2  
> 更新日期：2026-04-15  
> 状态：📋 开发中

---

## 📊 任务总览

| 阶段 | 模块 | 优先级 | 预估工时 | 状态 | 依赖 |
|------|------|--------|----------|------|------|
| **Phase 1** | 项目初始化 | P0 | 1h | ✅ 完成 | 无 |
| **Phase 2** | 核心基础设施 | P0 | 4-6h | ✅ 完成 | Phase 1 |
| **Phase 3** | 插件系统 | P0 | 3-4h | 🔄 进行中 | Phase 1, Phase 2 |
| **Phase 4** | API 层 | P1 | 4-5h | ⏳ 待开始 | Phase 1, Phase 2 |
| **Phase 5** | WebSocket & SSE | P1 | 2-3h | ⏳ 待开始 | Phase 1, Phase 2, Phase 3, Phase 4 |
| **Phase 6** | 业务逻辑 | P1 | 3-4h | ⏳ 待开始 | Phase 1-5 |
| **Phase 7** | 测试 | P2 | 3-4h | ⏳ 待开始 | Phase 1-6 |

---

## 📋 任务依赖关系图

```
Phase 1（项目初始化）
    │
    ├── Phase 2（核心基础设施）→ Phase 3（插件系统）
    │                                    │
    │                                    ├── Phase 4（API 层）
    │                                    │         │
    │                                    │         └── Phase 5（WebSocket & SSE）
    │                                    │                      │
    └── Phase 6（业务逻辑）←──────────────────────┘
                        │
                        └── Phase 7（测试）
```

---

## Phase 1：项目初始化 ✅

**目标**：搭建 FastAPI 项目骨架，配置依赖，建立目录结构

**依赖**：无

**交付物**：项目骨架可运行 `uv run sprinkle run`

---

## Phase 2：核心基础设施 ✅

**目标**：实现核心 Kernel 模块

**依赖**：Phase 1

**交付物**：核心模块单元测试通过

---

## Phase 3：插件系统 🔄

**目标**：实现插件热拔插机制

**依赖**：Phase 1, Phase 2

**前置条件**：
- 必须阅读 `ARCHITECTURE.md` 确认插件设计
- 必须了解 Phase 1/2 的模块接口
- 必须保证和 Phase 2 的 Event Bus 对接

**交付物**：插件可动态加载/卸载

---

## Phase 4：API 层

**目标**：实现 REST API

**依赖**：Phase 1, Phase 2

**前置条件**：
- 必须阅读 `ARCHITECTURE.md` 确认 API 设计
- 必须了解 Phase 1 的配置管理接口
- 必须了解 Phase 2 的 Auth Service 接口
- 必须了解 Phase 3 的插件接口（插件作为中间件）

**交付物**：REST API 可通过 Swagger 文档测试

---

## Phase 5：WebSocket & SSE

**目标**：实现实时通信

**依赖**：Phase 1, Phase 2, Phase 3, Phase 4

**前置条件**：
- 必须阅读 `ARCHITECTURE.md` 确认 WebSocket/SSE 设计
- 必须了解 Phase 2 的 Session Manager 接口
- 必须了解 Phase 3 的事件总线接口
- 必须了解 Phase 4 的 API 认证接口

**交付物**：客户端可通过 WebSocket 和 SSE 收发消息

---

## Phase 6：业务逻辑

**目标**：实现业务规则

**依赖**：Phase 1, Phase 2, Phase 3, Phase 4, Phase 5

**前置条件**：
- 必须阅读 `ARCHITECTURE.md` 确认业务逻辑设计
- 必须了解 Phase 2 的所有核心模块接口
- 必须了解 Phase 3 的插件系统接口
- 必须了解 Phase 4 的所有 API 接口
- 必须了解 Phase 5 的实时通信接口

**交付物**：完整业务逻辑可用

---

## Phase 7：测试

**目标**：保证代码质量

**依赖**：Phase 1, Phase 2, Phase 3, Phase 4, Phase 5, Phase 6

**前置条件**：
- 所有 Phase 必须完成并合并到 develop

**交付物**：测试覆盖率 > 70%

---

## 📋 任务进度看板

### 待办（Todo）
- [ ] Phase 4：API 层
- [ ] Phase 5：WebSocket & SSE
- [ ] Phase 6：业务逻辑
- [ ] Phase 7：测试

### 进行中（In Progress）
- [ ] Phase 3：插件系统

### 完成（Done）
- [ ] 架构文档设计
- [ ] Phase 1：项目初始化
- [ ] Phase 2：核心基础设施

---

## 📝 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-04-15 | v0.1 | 初始任务拆分 |
| 2026-04-15 | v0.2 | 补充任务依赖关系 |

---

*任务管理文档由司康维护~🍪*
