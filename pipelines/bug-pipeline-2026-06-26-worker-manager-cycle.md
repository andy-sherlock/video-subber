# WorkerManager 循环依赖 bug 修复流水线 — 2026-06-26

## 元信息
- 涉及问题：worker_manager 反向导入 main.JOBS，形成 `main -> worker_manager -> main` 循环依赖
- 涉及文件：`v3/backend/main.py`, `v3/backend/worker_manager.py`, `v3/backend/ws_manager.py`
- 当前进度：①/⑩
- 范围锁定：只修循环依赖；不新增功能，不顺手整理其他架构债

## ① investigate — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**读了什么**：
- `v3/backend/main.py:11-14` — `main.py` 顶部导入 `worker_manager`
- `v3/backend/main.py:31` — `JOBS` 是主服务内存状态
- `v3/backend/main.py:198-263` — startup 里把主事件循环注册给 `worker_manager`
- `v3/backend/main.py:1013-1047` — 现有 `_tracking_send()` 已经通过包一层 `ws_manager.send` 同步 `JOBS`
- `v3/backend/worker_manager.py:116-127` — `_forward()` 运行时反向导入 `.main.JOBS` 并重复同步进度
- `v3/backend/ws_manager.py:19-27` — `ws_manager.send()` 只负责 WebSocket 推送

### 结论（下一步只看这里）
根因是职责边界放错了：`worker_manager` 本该只负责 worker 子进程生命周期、读取 stdout、把 JSON 消息推给 WebSocket；但它在 `_forward()` 里又反向导入 `main.JOBS`，直接修改主服务状态。这样依赖方向变成：

`main.py -> worker_manager.py -> main.py`

这会带来两个问题：
1. 导入顺序风险：热重载、测试单独导入 `worker_manager`、或启动顺序变化时，`worker_manager` 可能拿到尚未初始化完整的 `main` 模块。
2. 职责重复：`main.py:1018-1047` 已经用 `_tracking_send()` 同步 `JOBS`，而 `worker_manager.py:123-127` 又同步一次 progress，属于同一件事放在两处。

推荐修法保留用户提出的方向，但做成最小改动：
- 给 `WorkerManager` 增加 `set_progress_callback(callback)`，名字沿用“进度回调”，实际接收每条 worker 消息。
- `worker_manager._forward()` 只做两件事：调用回调同步主服务状态，再调用 `ws_manager.send()` 推送 WebSocket。
- `main.py` 把现有 `_tracking_send()` 的状态同步逻辑拆成 `_sync_job_from_worker_message(job_id, message)`，注册给 `worker_manager.set_progress_callback(...)`。
- 移除 `worker_manager.py` 内部的 `from .main import JOBS`，让依赖方向保持单向：`main.py -> worker_manager.py`。
- 不改 worker 启动、stdout 读取、WebSocket 协议、任务状态字段含义。

### 涉及文件列表
- `v3/backend/worker_manager.py:12-20` — 需要新增回调字段和注册方法
- `v3/backend/worker_manager.py:116-127` — 需要删掉 `.main.JOBS` 反向导入，改为调用回调
- `v3/backend/main.py:1013-1047` — 需要把 `_tracking_send()` 里的状态同步逻辑改成普通回调，并取消 monkeypatch `ws_manager.send`
- `v3/backend/ws_manager.py:19-27` — 只读确认，预期不改
- `v3/test/test_p193_p194_p195.py:53-99` — 现有 worker_manager 错误转发测试，可作为附近测试位置参考

### 关键代码
```python
# v3/backend/worker_manager.py:120-127 当前问题点
from .ws_manager import ws_manager
from .main import JOBS
await ws_manager.send(job_id, msg)
if msg.get("type") == "progress" and job_id in JOBS:
    JOBS[job_id]["progress"] = msg.get("percent", 0)
    JOBS[job_id]["stage"] = msg.get("stage", "")
    JOBS[job_id]["message"] = msg.get("message", "")
```

## ② 双签审查 — 2026-06-26
**依赖类型**：📋 看记录
**执行人**：Codex
**前置阅读**：只读 ① 结论，未继续读代码

### plan-eng-review 结论
方案技术方向通过。这个 bug 的根因不是“某一行 import 写错”，而是模块边界错了：`worker_manager` 是底层 worker 生命周期模块，不应该反向知道主服务的内存状态。

推荐把修法收敛成一个明确接口：
- `WorkerManager.set_progress_callback(callback)`：主服务注册状态同步函数。
- `_forward(job_id, msg)`：先调用 callback，再调用 `ws_manager.send(job_id, msg)`。
- `main.py`：把现在 `_tracking_send()` 里的 `JOBS` 更新逻辑挪到 `_sync_job_from_worker_message()`，然后注册给 `worker_manager`。

测试要求：
- 先写一个失败测试，证明 `WorkerManager._forward()` 可以在 `backend.main` 被阻止导入时正常转发消息。
- 再写或复用一个行为测试，确认 progress/done/error/token_usage 仍会更新 `main.JOBS`。

边界：
- 不改 `start()`、`_read_stdout()`、`_read_stderr()`。
- 不改 WebSocket 消息格式。
- 不改任务状态字段。

### steve-jobs-perspective 结论
这个修法应该做减法。当前有两条同步路径：`worker_manager` 直接写 `JOBS`，`main` 又包一层 `ws_manager.send` 写 `JOBS`。两条路做同一件事，复杂度来自这里。

最小且干净的产品判断：
- 删掉 `worker_manager` 对 `main` 的认识。
- 删掉 `main` 对 `ws_manager.send` 的 monkeypatch。
- 保留一个入口：主服务注册一个回调，worker 消息到达时只走这一条状态同步路。

不建议只删 `from .main import JOBS` 后完全依赖现有 monkeypatch。那虽然改动更少，但会留下“谁调用 `ws_manager.send` 谁就顺便改 JOBS”的隐式规则。这个规则不直观，后面再维护还会踩。

### 结论（下一步只看这里）
双签通过。执行方案锁定为“回调注册 + 取消反向 import + 取消 `ws_manager.send` monkeypatch”。这不是新增功能，是把现有状态同步从隐式 monkeypatch 改成显式注册，行为保持不变。

### 涉及文件列表
- `v3/backend/worker_manager.py:12-20` — 加 `_progress_callback` 字段和 `set_progress_callback()`
- `v3/backend/worker_manager.py:116-127` — `_forward()` 改为回调 + WebSocket 推送，不再 import `main`
- `v3/backend/main.py:1013-1047` — `_tracking_send()` 改为 `_sync_job_from_worker_message()` 并注册给 `worker_manager`
- `v3/test/test_p193_p194_p195.py:53-99` — 附近已有 worker_manager 测试，可追加架构回归测试

## ③ 双签影响面 — 2026-06-26
**依赖类型**：📖🌐 读全项目
**执行人**：Codex
**读了什么**：
- `which ln-021-codegraph` — 当前环境没有该命令
- `.hex-skills/codegraph/index.db` — 当前项目内未发现可复用索引
- `rg` 全扫 `v3/backend` 和 `v3/test` 中的 `worker_manager` / `ws_manager` / `JOBS` / `from .main import`

### ln-021-codegraph 结论
`ln-021-codegraph` 工具本轮不可用，按该 Skill 的 fallback 规则使用手工调用图兜底。置信度中等偏高，因为本次目标是 Python import 和少量函数调用，`rg` 能完整覆盖文本级依赖。

手工调用图：
- `main.py:13` 导入 `worker_manager`
- `main.py:263` 调 `worker_manager.set_main_loop()`
- `main.py:776,808` 调 `worker_manager.start()`
- `main.py:876,880,897` 调 `worker_manager.is_alive()`
- `main.py:943` 调 `worker_manager.cancel()`
- `worker_manager.py:121` 反向导入 `.main.JOBS`
- `worker_manager.py:122` 调 `ws_manager.send()`
- `main.py:1015-1047` 当前 monkeypatch `ws_manager.send`

唯一真实循环依赖点就是 `worker_manager.py:121`。没有发现其他 backend 模块反向导入 `main`。

### bill-gates 结论
从系统角度看，这不是大范围架构重写，而是去掉一个错误控制点。`main.py` 是主服务入口，拥有 `JOBS` 这种全局状态；`worker_manager.py` 是底层执行器，不应该直接写入口模块的状态。

风险评估：
- 可扩展性：回调方案比反向 import 好，后续如果 `JOBS` 从内存 dict 迁到数据库，`worker_manager` 不需要改。
- 二阶影响：取消 monkeypatch 后，`ws_manager.send()` 恢复单一职责，只发 WebSocket；状态同步由 `main` 显式注册，调试路径更短。
- 主要风险：如果注册回调时机晚于 worker 消息到达，状态不会同步。当前 `main.py` 模块导入阶段就可以注册回调，比 startup 里只注册 event loop 更早，因此可控。

### 结论（下一步只看这里）
影响面窄，推荐继续。需要守住两件事：
1. 回调注册放在 `main.py` 模块初始化阶段，而不是只放 startup，避免测试或特殊调用路径下漏注册。
2. `_forward()` 中 callback 和 `ws_manager.send()` 都应保留；callback 只更新 `JOBS`，WebSocket 推送仍由 `ws_manager` 完成。

### 涉及文件列表
- `v3/backend/main.py:13` — `main -> worker_manager` 正向依赖保留
- `v3/backend/main.py:1013-1047` — 状态同步逻辑迁移为注册回调
- `v3/backend/worker_manager.py:116-127` — 删除唯一反向导入点
- `v3/backend/ws_manager.py:19-27` — 恢复为纯 WebSocket 推送，不被 main monkeypatch

## ④ 双签 diff + 简化 — 2026-06-26
**依赖类型**：📖🌐 读全项目
**执行人**：Codex
**读了什么**：
- `git status --short` — 当前只有本管道文件未跟踪
- `git diff --stat` — 生产代码尚未修改，无代码 diff
- ①-③ 管道结论

### /review 结论
本步骤按流水线顺序执行时，尚未进入 TDD 和生产代码修改，所以没有真实代码 diff 可审。当前只能审查“计划中的改动边界”：
- 范围合理：目标文件应限于 `v3/backend/worker_manager.py`、`v3/backend/main.py`、一个测试文件。
- 不应修改：`v3/backend/ws_manager.py`、worker 启动参数、WebSocket 协议、任务状态字段。
- 修后必须补做一次实际 diff 扫雷，因为这一步没有代码 diff，不能替代真实 `/review`。

### elon-musk 结论
用第一性原理看，这个系统只需要一条状态同步路径。现在有两条：`worker_manager` 写 `JOBS`，`main` monkeypatch `ws_manager.send` 也写 `JOBS`。删掉一条，并把剩下的一条变成显式回调。

最小可行修法：
1. `worker_manager` 增加一个 callback 槽位。
2. `main` 注册 callback。
3. 删除反向 import 和 monkeypatch。

不要顺手拆 `JOBS`，不要把状态搬数据库，不要重写 WebSocket 管理器。

### 结论（下一步只看这里）
简化审查通过。允许进入设计一致性走查和 TDD，但修完后必须再做一次真实代码 diff 审查。

### 涉及文件列表
- `v3/backend/worker_manager.py:12-20`
- `v3/backend/worker_manager.py:116-127`
- `v3/backend/main.py:1013-1047`
- `v3/test/test_p193_p194_p195.py:53-99`

## ⑤ 设计一致性走查 — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**设计图**：小 bug 无单独架构图，使用现有模块职责作为设计图：

```
worker.py 子进程 stdout(JSON消息)
  -> worker_manager._read_stdout()
  -> worker_manager._forward()
  -> main 注册的状态同步回调（更新 JOBS）
  -> ws_manager.send()（只推 WebSocket）
  -> 前端 / 轮询 API 读取最新状态
```

箭头格式：
- `worker.py -> worker_manager`：stdout 文本行，解析成 dict
- `worker_manager -> main callback`：`job_id + message dict`
- `worker_manager -> ws_manager`：`job_id + message dict`
- `main callback -> JOBS`：内存 dict 状态更新

### 逐步骤对照

| 设计步骤 | 当前代码对应 | 一致？ |
|----------|-------------|--------|
| worker_manager 读取 worker stdout | `worker_manager.py:71-88` | ✅ 一致 |
| worker_manager 转发 WebSocket | `worker_manager.py:116-122` | ✅ 一致 |
| main 维护 JOBS | `main.py:31`, `main.py:1018-1047` | ✅ 一致 |
| worker_manager 不认识 main.JOBS | `worker_manager.py:121` 反向 import `.main.JOBS` | ❌ 多了 |
| ws_manager 只负责 WebSocket | `ws_manager.py:19-27` 本体一致，但 `main.py:1047` monkeypatch 让它顺带同步 JOBS | ⚠️ 设计外多了一只手 |

### 数据流追踪

| 步骤 | 数据包 | 手（函数） | 读/改/扔 | 设计图上？ |
|------|--------|------------|----------|------------|
| worker 输出 | JSON line | `_read_stdout()` | 读 + parse | ✅ |
| 已解析消息 | dict | `_forward()` | 转发 | ✅ |
| 已解析消息 | dict | `worker_manager` 直接 import `main.JOBS` | 改 JOBS | ❌ |
| 已解析消息 | dict | `ws_manager.send` 被 `_tracking_send` 包住 | 改 JOBS + 推送 | ⚠️ 隐式 |
| 已解析消息 | dict | `ws_manager.send()` 本体 | 推送 WebSocket | ✅ |

### 偏差汇总

| # | 类型 | 描述 | 决定 |
|---|------|------|------|
| 1 | 多了 | `worker_manager._forward()` 反向导入 `main.JOBS` | 删掉，改为 main 注册回调 |
| 2 | 多了 | `main.py` monkeypatch `ws_manager.send`，让 WebSocket 管理器间接承担状态同步入口 | 取消 monkeypatch，状态同步改为显式回调 |

### 结论（下一步只看这里）
走查通过，偏差已有明确处置。目标设计是单向依赖：`main -> worker_manager -> ws_manager`，`worker_manager` 不再反向依赖 `main`。

## ⑥ 安全判断 — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**前置阅读**：①-⑤ 结论

### 结论（下一步只看这里）
本轮不跑 cso/security-review。原因：改动只涉及 Python 模块依赖方向和内存状态同步入口，不涉及以下安全面：
- 认证 / 登录 / 权限
- 用户输入校验
- 文件路径校验
- 外部 URL 请求
- API Key / token / 环境变量
- 字幕正文或日志脱敏策略

安全风险主要是“状态同步是否漏掉”，属于功能回归风险，交给 TDD 和全量测试覆盖。

## ⑦ test-driven-development — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**读了什么**：
- `v3/test/test_p193_p194_p195.py:53-99` — 附近已有 worker_manager 测试
- `v3/backend/worker_manager.py:12-127` — 待修目标
- `v3/backend/main.py:1013-1047` — 待迁移状态同步逻辑

### RED：先写失败测试
新增两个测试：
- `TestWorkerManagerNoMainImport.test_forward_uses_registered_callback_without_importing_main`
- `TestMainWorkerProgressCallback.test_sync_job_from_worker_message_updates_jobs`

红灯命令：
```bash
cd v3 && ./.venv/bin/python -m pytest test/test_p193_p194_p195.py -q
```

红灯结果：
```text
2 failed, 4 passed, 2 warnings
AttributeError: 'WorkerManager' object has no attribute 'set_progress_callback'
AttributeError: module 'backend.main' has no attribute '_sync_job_from_worker_message'
```

### GREEN：最小修复
改动：
- `v3/backend/worker_manager.py`：
  - 新增 `_progress_callback`
  - 新增 `set_progress_callback()`
  - `_forward()` 删除 `from .main import JOBS`
  - `_forward()` 改为调用回调后再 `ws_manager.send()`
- `v3/backend/main.py`：
  - 删除 `_original_send` / `_tracking_send` / `ws_manager.send = _tracking_send`
  - 新增 `_sync_job_from_worker_message()`
  - 模块初始化时执行 `worker_manager.set_progress_callback(_sync_job_from_worker_message)`

绿灯命令：
```bash
cd v3 && ./.venv/bin/python -m pytest test/test_p193_p194_p195.py -q
```

绿灯结果：
```text
6 passed, 2 warnings
```

### 修后 diff 扫雷
命令：
```bash
git diff -- v3/backend/worker_manager.py v3/backend/main.py v3/test/test_p193_p194_p195.py
rg -n "from \\.main import JOBS|from \\.main import|from backend\\.main import JOBS|ws_manager\\.send =|_tracking_send|_original_send" v3/backend v3/test
git diff --check
```

结论：
- 生产代码 diff 只碰 `main.py` 和 `worker_manager.py`
- 测试 diff 只追加架构回归测试
- 静态扫描确认没有剩余 `.main.JOBS` 反向导入，也没有 `ws_manager.send` monkeypatch
- `git diff --check` 通过

### 结论（下一步只看这里）
TDD 红绿通过。循环依赖已从代码层移除，行为同步路径保留为 `main` 注册的显式回调。下一步跑全量测试。

## ⑧ verification-before-completion — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex

### 全量测试
命令：
```bash
cd v3 && ./.venv/bin/python -m pytest test/ -q
```

结果：
```text
626 passed, 8 warnings in 121.47s (0:02:01)
```

说明：
- 上轮记录是 624 passed，本轮新增 2 个回归测试，所以变为 626 passed。
- 8 个 warning 与既有依赖/弃用警告同类，不是本轮新增失败。

### 结论（下一步只看这里）
全量测试通过。当前代码层验证已满足进入 QA 判断的条件。

## ⑨ Web QA 判断 — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex

### 判断
本轮没有改前端页面、按钮、CSS、WebSocket 协议或 API 契约；改动只在后端内部模块依赖和 `JOBS` 状态同步入口。因此不跑完整浏览器 QA。用后端运行态检查替代：
- 端口监听
- 健康接口
- 服务重启到新代码

### 服务状态
发现旧服务：
```text
PID 78466, STARTED Sun Jun 21 14:01:22 2026
COMMAND ... Python -m uvicorn backend.main:app --host 127.0.0.1 --port 8766
```

旧服务无 `--reload`，不会加载本轮改动，已停止。

新服务：
```text
screen 会话：25302.video-subtitle-v308-cycle
监听进程：PID 25324
端口：*:8766
健康接口：{"ok":true,"version":"3.07"}
```

### 结论（下一步只看这里）
Web QA 按需完成：无需浏览器全流程；后端新服务已启动并可访问。用户可以刷新 `http://localhost:8766` 复测。

## ⑧ 补充复验 — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex

### 背景
修后 diff 扫雷时发现测试还可以更完整：原新增测试覆盖了 `progress` 消息，但 `main.py` 里迁移出来的 `_sync_job_from_worker_message()` 同时负责 `token_usage`、`done`、`error` 三类状态同步。为避免漏测，追加一条测试覆盖这些消息。

### 追加测试
文件：
- `v3/test/test_p193_p194_p195.py` — 新增 `test_sync_job_from_worker_message_preserves_done_error_and_token_usage`

覆盖内容：
- `token_usage` 消息会更新 `JOBS[job_id]["token_usage"]`
- `done` 消息会更新 `status/progress/result_video/result_srt/failed_segments/segment_count/success_count`
- `error` 消息会更新 `status/error`

### 目标测试
命令：
```bash
cd v3 && ./.venv/bin/python -m pytest test/test_p193_p194_p195.py -q
```

结果：
```text
7 passed, 2 warnings in 0.31s
```

### 静态扫描
命令：
```bash
rg -n "from \\.main import JOBS|from \\.main import|from backend\\.main import JOBS|ws_manager\\.send =|_tracking_send|_original_send" v3/backend v3/test
git diff --check
```

结果：
- 反向导入和 monkeypatch 扫描无匹配。
- `git diff --check` 通过。

### 全量测试
命令：
```bash
cd v3 && ./.venv/bin/python -m pytest test/ -q
```

结果：
```text
627 passed, 8 warnings in 130.25s (0:02:10)
```

### 结论（下一步只看这里）
P300 修复完成。循环依赖已移除，状态同步四类消息都有回归测试覆盖，全量测试通过。下一步可以提交本修复，然后进入 V3.08 版本收尾 HARDEN 流程。
