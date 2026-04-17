# Sprinkle v2.1 架构修复任务

> **主人指令（原文，一字不漏）**：
> "审计sprinkle，细查它的架构，确认开发是否完善"
> "是"（主人确认需要修复）

---

## 问题汇总

### 🚨 严重问题（必须修复）

| # | 问题 | 优先级 | 影响 |
|---|------|--------|------|
| 1 | 混合存储架构（内存 + 数据库双写） | P0 | 数据一致性风险 |
| 2 | Message 模型缺失 metadata/edited_at/deleted_at | P0 | 功能不完整 |
| 3 | ContentType 不完整（缺 image/file/system） | P0 | 消息类型受限 |
| 4 | Files API 纯内存存储，无数据库 | P0 | 文件无法持久化 |
| 5 | 集成测试使用内存存储而非真实数据库 | P0 | 无法验证真实场景 |

### ⚠️ 中等问题

| # | 问题 | 优先级 | 影响 |
|---|------|--------|------|
| 6 | Agent 权限与架构文档不一致 | P1 | 文档与实现不符 |
| 7 | 测试覆盖率 74% < 80% 要求 | P1 | 质量不达标 |
| 8 | 架构文档未更新反映实现 | P1 | 文档过时 |

---

## 修复方案

### 阶段 1：数据模型修复

```
任务 1.1：补全 Message 模型
- 添加 metadata JSONB 字段
- 添加 edited_at DateTime 字段
- 添加 deleted_at DateTime 字段
- 添加 deleted_by 字段（FK -> users.id）

任务 1.2：补全 ContentType 枚举
- 添加 image 类型
- 添加 file 类型
- 添加 system 类型

任务 1.3：补全 User 模型
- 确认 metadata vs extra_data 命名统一
- 确认 JSONB 存储

任务 1.4：更新数据库迁移
- 创建新的 migration 脚本
- 添加新字段到现有表
```

### 阶段 2：存储架构统一

```
任务 2.1：移除 Conversations API 的内存存储
- 保留数据库为唯一数据源
- 移除 _conversations 内存字典
- 移除 _members 内存字典
- 更新测试使用数据库

任务 2.2：移除 Messages API 的内存存储
- 保留数据库为唯一数据源
- 移除 _messages 内存字典
- 更新测试使用数据库

任务 2.3：实现 Files API 数据库持久化
- 创建 files 表
- 实现文件元数据数据库存储
- 保留物理文件存储

任务 2.4：实现 Auth API 数据库完整支持
- 确保 _registered_users 完全被数据库替代
- 移除内存注册用户存储
```

### 阶段 3：权限控制对齐

```
任务 3.1：审查并修正 Agent 权限
- 对比架构文档与代码实现
- 修正不一致之处（选择之一）：
  方案A：修改代码匹配文档
  方案B：更新文档匹配代码
- 确保 Owner/Admin/Agent/Member 权限清晰

任务 3.2：更新架构文档
- 反映实际权限实现
- 更新权限矩阵
```

### 阶段 4：测试质量提升

```
任务 4.1：重写集成测试
- 使用真实 PostgreSQL 数据库
- 移除 mock 数据依赖
- 验证数据准确性而非仅状态码

任务 4.2：提升测试覆盖率至 80%+
- 补充缺失测试用例
- 覆盖边界条件
- 覆盖异常流程
```

### 阶段 5：文档同步

```
任务 5.1：更新 ARCHITECTURE.md
- 反映实际实现（移除混合存储说明）
- 更新数据模型文档
- 更新 API 文档

任务 5.2：更新 README.md
- 更新开发环境说明
- 更新测试说明
```

---

## 执行计划

### 分支策略
- 当前分支：`v2.0`（已发布供审计）
- 修复分支：`v2.1-audit-fix`
- 目标分支：`v2.1`（修复完成后供布莱妮 re-audit）

### 任务分配
| 任务 | 执行者 | 依赖 | 状态 |
|------|--------|------|------|
| 1.1 Message 模型 | subagent | - | ✅ 完成 |
| 1.2 ContentType | subagent | 1.1 | ✅ 完成 |
| 1.3 User 模型 | subagent | - | ✅ 完成 |
| 1.4 数据库迁移 | subagent | 1.1,1.2,1.3 | ✅ 完成 |
| 2.1 Conversations 存储 | subagent | 1.4 | ⏳ |
| 2.2 Messages 存储 | subagent | 1.4 | ⏳ |
| 2.3 Files 数据库 | subagent | 1.4 | ⏳ |
| 2.4 Auth 存储 | subagent | 1.4 | ⏳ |
| 3.1 权限审查 | subagent | 2.1,2.2 | ⏳ |
| 3.2 文档更新 | subagent | 3.1 | ⏳ |
| 4.1 集成测试重写 | subagent | 2.4 | ⏳ |
| 4.2 覆盖率提升 | subagent | 4.1 | ⏳ |
| 5.1 架构文档 | subagent | 4.2 | ⏳ |
| 5.2 README | subagent | 5.1 | ⏳ |

---

## 汇报规则

1. **每个任务完成必须向主人汇报**
2. **每个阶段完成更新本任务文档**
3. **最终提交前必须通过集成测试**
4. **提交 PR 必须经布莱妮审计通过**

---

## v2.1 全面修复任务执行记录（本次 subagent）

**执行时间**：2026-04-17 14:27
**执行者**：司康 subagent

### 已完成修复

| # | 修复项 | 文件 | 状态 |
|---|--------|------|------|
| 1 | ConversationMember 模型修复 | `src/sprinkle/models/conversation_member.py` | ✅ 完成 |
| 2 | File 模型创建 | `src/sprinkle/models/file.py` | ✅ 完成 |
| 3 | models/__init__.py 更新 | `src/sprinkle/models/__init__.py` | ✅ 完成 |
| 4 | Files 表迁移脚本 | `scripts/migration_v2.1_add_file_table.py` | ✅ 完成 |
| 5 | TASKS 文档更新 | `docs/TASKS-v2.1-AUDIT-FIX.md` | ✅ 完成 |

### 修复详情

**1. ConversationMember 模型** (`src/sprinkle/models/conversation_member.py`)
- ✅ 移除单独 `id` 字段，改为联合主键 `(conversation_id, user_id)`
- ✅ 添加 `nickname = Column(String(100), nullable=True)`
- ✅ 添加 `left_at = Column(DateTime, nullable=True)`
- ✅ 添加 `is_active = Column(Boolean, default=True, nullable=False)`
- ✅ MemberRole 枚举保留，字段名与架构文档一致

**2. File 模型** (`src/sprinkle/models/file.py` 新建)
- ✅ `id`: String(36), PK
- ✅ `uploader_id`: String(36), FK -> users.id, NOT NULL
- ✅ `conversation_id`: String(36), FK -> conversations.id, nullable
- ✅ `file_name`: String(255), NOT NULL
- ✅ `file_path`: String(500), NOT NULL
- ✅ `file_size`: BigInteger, NOT NULL
- ✅ `mime_type`: String(100), nullable
- ✅ `created_at`: DateTime, default=utcnow, NOT NULL

**3. models/__init__.py 更新**
- ✅ 添加 `from .file import File` 导入
- ✅ `__all__` 列表添加 `"File"`

**4. Files 数据库迁移脚本** (`scripts/migration_v2.1_add_file_table.py`)
- ✅ 创建 `files` 表（幂等 `CREATE TABLE IF NOT EXISTS`）
- ✅ 为 `conversation_members` 添加 `nickname` 字段
- ✅ 为 `conversation_members` 添加 `left_at` 字段
- ✅ 为 `conversation_members` 添加 `is_active` 字段
- ✅ 更新 `conversation_members.role` CHECK 约束（含 `agent`）
- ✅ 所有改动使用 `ADD COLUMN IF NOT EXISTS` 确保幂等性

**5. 验证**
- ✅ `python3 -c "from sprinkle.models import File, ConversationMember..."` 无导入错误
- ✅ ConversationMember 字段：`['conversation_id', 'user_id', 'role', 'nickname', 'invited_by', 'joined_at', 'left_at', 'is_active']`
- ✅ File 字段：`['id', 'uploader_id', 'conversation_id', 'file_name', 'file_path', 'file_size', 'mime_type', 'created_at']`

---

*创建时间：2026-04-17 14:12*
*最后更新：2026-04-17 14:27*
*司康编制*
