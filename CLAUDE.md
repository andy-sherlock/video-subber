# video-subtitle 项目指引

## 活跃代码位置

**所有活跃代码在 `v3/` 目录下。** 这是唯一需要读写的目录。

```
v3/
├── backend/          # FastAPI 后端（活跃）
├── frontend/         # 单页 HTML 前端（V3 阶段1 临时测试用，阶段2 换 PyWebView）
├── desktop.py        # 桌面入口（活跃）
├── pyproject.toml    # 依赖配置（活跃）
├── .venv/            # 唯一活跃虚拟环境
└── test/             # 测试目录
```

## 版本规则

- **当前活跃版本：V3.08**
- 只读快照统一放在 `snapshots/` 目录下（v2.10~v3.07），回滚时参考，永远不修改
- `v1/` `v1.1/` `v1.2/` `v1.3/` 是 V1 线归档，只读
- 凭据系统（credential_manager）已删除，百度翻译已砍掉
- 免费翻译引擎：Google / Bing（逆向 Edge）/ 有道（逆向 demo API），全部无需 Key
- post-commit hook 已删除，不会再自动生成 "chore: sync" 提交

## 开发规则

- 修改代码 → 改 `v3/` 下的文件
- 需要回滚 → 从 `snapshots/` 对应版本复制到 `v3/`
- 新版本发布 → 把当前 `v3/`（不含 .venv）复制为 `snapshots/v3.XX/`
- 主档案 → 项目根 `video-subtitle.md`，架构/规划变更同步更新

## Skill routing

参见全局 CLAUDE.md（`~/.claude/CLAUDE.md`）中的 gstack Skill 路由表。所有项目共用同一份路由规则，不在此处重复。
