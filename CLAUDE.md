# video-subtitle 项目指引

## 活跃代码位置

**所有活跃代码在 `v2/` 目录下。** 这是唯一需要读写的目录。

```
v2/
├── backend/          # FastAPI 后端（活跃）
├── frontend/         # 单页 HTML 前端（V3 阶段1 临时测试用，阶段2 换 PyQt5）
├── desktop.py        # 桌面入口（活跃）
├── pyproject.toml    # 依赖配置（活跃）
├── v2.10/            # V2.10 只读快照
├── v2.11/            # V2.11 只读快照
├── v2.12/            # V2.12 只读快照
└── ...
```

## 版本规则

- **当前活跃版本：V3.0**（版本号 `"3.0"`，以 V2.12 为框架）
- `v2/` 下的 `v2.XX/` 子目录是**只读快照**，回滚时参考，永远不修改
- `v1/` `v1.1/` `v1.2/` `v1.3/` 是 V1 线归档，只读
- 凭据系统（credential_manager）已删除，百度翻译已砍掉
- 免费翻译引擎：Google / Bing（逆向 Edge）/ 有道（逆向 demo API），全部无需 Key
- post-commit hook 已删除，不会再自动生成 "chore: sync" 提交

## 开发规则

- 修改代码 → 改 `v2/` 下的文件
- 需要回滚 → 从 `v2/v2.XX/` 快照复制到 `v2/`
- 新版本发布 → 把当前 `v2/`（不含子版本文件夹）复制为 `v2/v2.YY/`
- 主档案 → 项目根 `video-subtitle.md`，架构/规划变更同步更新

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
