# V3.08 发布线 — 2026-06-26

## 元信息
- 涉及版本：V3.08
- 活跃代码：`v3/`
- 当前进度：5/5
- 发布提交：`690e8bc release: V3.08`
- 发布 tag：`v3.08`
- 只读快照：`snapshots/v3.08/`

## ① verification-before-completion — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex
**运行了什么**：
- `cd /Users/andy/Andy-agent/video-subtitle/v3`
- `.venv/bin/python -m pytest test/ -q`

### 结论（下一步只看这里）
发布前完整全量测试通过：`633 passed, 8 warnings in 93.75s`。

这次测试使用提升权限运行，因为 P207/P212 真实模型测试需要访问本机模型缓存和 Metal 设备路径。普通沙盒环境此前会卡在 HuggingFace/Metal 路径，不能作为发布验证环境。

## ② 版本号 bump — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex

### 结论（下一步只看这里）
`v3/pyproject.toml` 已从 `3.07` 改为 `3.08`。

`/api/health` 通过 `_detect_version()` 从 `pyproject.toml` 读取版本，所以后端健康接口会跟随这个版本号，不再需要额外硬编码修改。

### 涉及文件列表
- `v3/pyproject.toml:7` — `version = "3.08"`
- `v3/backend/main.py:320-338` — `/api/health` 读取版本

## ③ git commit + tag — 2026-06-26
**依赖类型**：📋 看记录
**执行人**：Codex

### 结论（下一步只看这里）
已按显式文件清单提交 V3.08 相关代码、测试、管道和计划记录，没有使用 `git add .`。

- commit：`690e8bc release: V3.08`
- tag：`v3.08`

提交排除了工作区里已有的无关删除：`v1.2/video_subber/subtitler.py`。

## ④ 创建只读快照 — 2026-06-26
**依赖类型**：📋 看记录
**执行人**：Codex

### 结论（下一步只看这里）
已创建只读快照：`snapshots/v3.08/`。

快照从 `v3/` 复制，排除了 `.venv/`、`__pycache__/`、`.pytest_cache/`、`.mypy_cache/` 和 `*.pyc`。

验证：
- `du -sh snapshots/v3.08` → `5.2M`
- `find snapshots/v3.08 -maxdepth 1 -type d -name .venv -print` → 无输出
- `snapshots/v3.08/pyproject.toml` 显示 `version = "3.08"`
- `snapshots/v3.08`、`snapshots/v3.08/backend`、`snapshots/v3.08/pyproject.toml` 均为只读权限

## ⑤ planning-with-files-zh 同步记录 — 2026-06-26
**依赖类型**：📋 看记录
**执行人**：Codex

### 结论（下一步只看这里）
已同步发布状态到：
- `/Users/andy/Andy-agent/video-subtitle/progress.md`
- `/Users/andy/Andy-agent/video-subtitle/task_plan.md`
- `/Users/andy/Andy-agent/video-subtitle/problems.md`
- `/Users/andy/task_plan.md`

V3.08 当前状态：已发布。下一条主线是 V3.09 PyWebView 桌面壳 + 精度报告。
