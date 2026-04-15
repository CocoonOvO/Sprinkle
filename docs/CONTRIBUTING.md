# Sprinkle 开发工作流

## 📋 分支结构

```
main (稳定版本)
└── develop (开发集成分支)
    ├── feature/phase1-init      # 项目初始化
    ├── feature/phase2-kernel    # 核心基础设施
    ├── feature/phase3-plugins  # 插件系统
    ├── feature/phase4-api      # API 层
    ├── feature/phase5-websocket # WebSocket & SSE
    ├── feature/phase6-business  # 业务逻辑
    └── feature/phase7-test      # 测试
```

## 🔄 开发流程

### 1. 开始新任务

```bash
# 确保在 develop 分支
git checkout develop
git pull origin develop

# 创建或切换到任务分支
git checkout -b feature/phase1-init
# 或
git checkout feature/phase1-init
```

### 2. 开发 & 提交

```bash
# 开发代码...

# 提交（遵循 Conventional Commits）
git add .
git commit -m "feat(phase1): initialize project structure"
git push origin feature/phase1-init
```

### 3. 创建 Pull Request

在 GitHub 上创建 PR 到 `develop` 分支，邀请其他 agent 审批。

### 4. 审批流程

```
1. Agent 审查代码
2. 指出问题或 approve
3. 司康根据反馈修改
4. 审批通过后合并到 develop
```

### 5. 合并后

```bash
# 切换回 develop 并更新
git checkout develop
git pull origin develop

# 删除已合并的分支（可选）
git branch -d feature/phase1-init
```

## 📝 提交规范

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档
- `style`: 格式（不影响代码）
- `refactor`: 重构
- `test`: 测试
- `chore`: 杂项

### 示例

```
feat(phase1): add project structure and config management

- Create src/sprinkle/ directory structure
- Add YAML configuration support
- Configure dependencies in pyproject.toml

Closes #1
```

## ✅ 代码审查清单

- [ ] 代码符合架构文档设计
- [ ] 有必要的单元测试
- [ ] 无硬编码配置（使用 config.yaml）
- [ ] 遵循 PEP 8 代码风格
- [ ] API 设计与文档一致

## 🚀 发布流程

```
1. 所有 feature 分支合并到 develop
2. 创建 release 分支进行测试
3. 测试通过后合并到 main
4. 打 tag 发布
```

---

*开发工作流由司康维护~🍪*
