# P303 Bug 修复流水线 — 2026-06-26

## 元信息
- 涉及问题：P303
- 涉及文件：`v3/backend/main.py`、`v3/frontend/index.html`、`v3/test/test_harden_v307_sprint2.py`
- 当前进度：⑩/⑩
- 范围锁定：只修旧 `queued`/`interrupted` 历史任务反复通过 `/api/active-jobs` 恢复成页面卡片的问题；不新增功能，不重构前端。

## ① investigate 根因与修复方案 — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**读了什么**：
- `/Users/andy/task_plan.md:3` — 当前项目栏
- `task_plan.md:1-260` — V3.08 范围和边界
- `progress.md:1861-1872` — P303 现象和根因补充
- `problems.md:6543-6560` — P303 问题库记录
- `pipelines/harden-v308-2026-06-26.md:369-424` — 用户真实短视频 E2E 和 P303 发现记录
- `/Users/andy/.video-subber/jobs/cc7d373829/meta.json:1` — 旧 URL 安全测试任务，磁盘状态 `queued`
- `/Users/andy/.video-subber/jobs/e25373d4d3/meta.json:1` — 旧 URL 安全测试任务，磁盘状态 `queued`
- `v3/backend/main.py:223-254` — 启动扫描历史 job 并把旧 `queued/running` 推断为内存 `interrupted`
- `v3/backend/main.py:847-866` — `/api/active-jobs` 返回 `queued/running/interrupted`
- `v3/backend/main.py:884-898` — `/api/jobs/{job_id}` 优先返回内存状态
- `v3/frontend/index.html:1044-1057` — 前端把 `interrupted` 渲染成橙色卡片
- `v3/frontend/index.html:1294-1296` — 本地 `activeJobs` 为空时拉取 `/api/active-jobs`

### 结论（下一步只看这里）
P303 的根因不是当前任务还在跑，而是两个旧安全测试 job 仍留在磁盘上：
- `cc7d373829`：`source_url=http://192.168.1.1/video.mp4`，`video_filename=video.mp4`，磁盘 `status=queued`，目录里只有 `config.json` 和 `meta.json`，没有 `input.*`，没有 `output.srt`。
- `e25373d4d3`：`source_url=http://127.0.0.1:8766/api/config`，`video_filename=config`，磁盘 `status=queued`，目录里只有 `config.json` 和 `meta.json`，没有 `input.*`，没有 `output.srt`。

当前服务启动时，`main.py:238-242` 会把这些历史 `queued/running` 且无 `output.srt` 的任务只在内存里改成 `interrupted`。状态没有写回磁盘，所以重启后会再次发生。

当前 `/api/active-jobs` 实测返回：
- `cc7d373829`：`status=interrupted`
- `e25373d4d3`：`status=interrupted`

前端在本地 `localStorage.activeJobs` 为空时会拉取 `/api/active-jobs`，把两个 id 写回本地，然后 `reconnectJobs()` 调 `/api/jobs/{job_id}`，拿到内存里的 `interrupted`，最终渲染两个橙色进度条。

建议修法：
- 不删除旧 job 目录，不改清缓存规则。
- 后端给 `/api/active-jobs` 加一个“可恢复任务”过滤。
- 对 `interrupted` 任务，只有满足以下任一条件才返回给首页自动恢复：
  1. job 目录里有 `input.*`，说明这是上传/本地文件任务，有真实输入可恢复。
  2. config 里有 `source_url`，且 `_verify_safe_url(source_url)` 通过，说明这是安全 URL 任务，可重新提交恢复。
- 这两个旧安全测试任务的 URL 是内网/本机地址，会被 `_verify_safe_url()` 拦掉，因此不再出现在 `/api/active-jobs`。
- `queued/running` 仍保留为活跃任务；本轮只处理启动后被推断成 `interrupted` 的历史恢复列表。

### 涉及文件列表
- `v3/backend/main.py:46-66` — 已有 URL 安全判断 `_verify_safe_url()`
- `v3/backend/main.py:223-254` — 历史 job 启动扫描
- `v3/backend/main.py:847-866` — 本次主要修复点 `/api/active-jobs`
- `v3/frontend/index.html:1044-1057` — `interrupted` 卡片渲染
- `v3/frontend/index.html:1294-1296` — 自动恢复入口
- `v3/test/test_harden_v307_sprint2.py` — 建议追加 P303 回归测试

## ② 双签审查：plan-eng-review + steve-jobs-perspective — 2026-06-26
**依赖类型**：📋 看记录
**执行人**：Codex
**前置阅读**：① 结论；未重新读代码。

### 结论（下一步只看这里）
plan-eng-review 结论：方案成立，但必须守住两个边界：
- 不能把所有 `interrupted` 都从 `/api/active-jobs` 里删掉。真实中断任务仍需要用户看到“可重新提交恢复”的提示。
- 不能只把启动扫描时的内存状态写回磁盘。即使写回 `interrupted`，前端仍会继续恢复旧卡片，问题不消失。

推荐方案是“只返回可恢复的 interrupted”。判断标准放在后端，因为后端知道 job 目录有没有 `input.*`，也能复用 `_verify_safe_url()`。

steve-jobs-perspective 结论：这是一个减法问题。页面首页不该替用户展示不可恢复的旧测试垃圾。不要做新按钮、不要改 UI 文案、不要新增清理面板；直接让后端别把不可恢复的旧任务交给前端。

通过条件：
- 旧内网 URL / 本机 URL interrupted job 不再出现在 `/api/active-jobs`。
- 有真实 `input.*` 的 interrupted job 仍会出现在 `/api/active-jobs`。
- 有安全公网 URL 的 interrupted job 仍会出现在 `/api/active-jobs`。
- 现有任务创建、去重、恢复提交逻辑不改。

### 涉及文件列表
- `v3/backend/main.py:847-866` — 过滤位置
- `v3/backend/main.py:750-783` — 用户重新提交同一 interrupted job 时的恢复逻辑，不能破坏
- `v3/test/test_harden_v307_sprint2.py` — TDD 覆盖点

## ③ 双签影响面：ln-021-codegraph + bill-gates — 2026-06-26
**依赖类型**：📖🌐 读全项目
**执行人**：Codex
**读了什么**：
- `command -v ln-021-codegraph` — 当前 shell 无可执行命令，按 Skill 降级规则改用静态扫描
- `rg "get_active_jobs|/api/active-jobs|GLOBAL_HASH_INDEX|JOB_ID_TO_HASH|_persist_job_status|_handle_existing_job"` — 定位后端索引、恢复和 API 影响面
- `rg "activeJobs|reconnectJobs|interrupted|source_url|_verify_safe_url"` — 定位前端恢复链路和 URL 安全复用点
- `v3/backend/main.py:1-118` — URL 安全 helper 和内存索引结构
- `v3/backend/main.py:560-845` — 持久化状态、去重、恢复、创建任务
- `v3/backend/main.py:1008-1048` — worker 消息同步状态
- `v3/test/test_harden_v307_sprint2.py:120-160` — 现有 main 状态索引测试写法
- `v3/test/test_p193_p194_p195.py:135-170` — worker 回调状态测试写法

### 结论（下一步只看这里）
`ln-021-codegraph` 不可用，本步是降级静态影响面，不等价于真实 codegraph 输出。

影响面集中在一个出口：`v3/backend/main.py:get_active_jobs()`。它只给前端空 `localStorage.activeJobs` 的自动恢复兜底使用。

不应改的地方：
- 不改 `startup()` 的历史扫描。它还负责重建 `GLOBAL_HASH_INDEX`，也服务任务去重。
- 不改 `_handle_existing_job()`。用户重新提交同一中断任务时，它负责恢复 worker。
- 不改前端 `reconnectJobs()`。它还要处理用户本地已有的活跃任务列表。
- 不删除 `.video-subber/jobs/*` 旧目录。用户没有要求清缓存。

系统层面风险：
- 如果过滤太狠，会让真实中断任务无法在首页自动恢复。
- 如果只过滤 URL，不管 `input.*`，上传任务会受影响。
- 如果引入新状态，比如 `abandoned`，会扩大数据契约，不符合本轮“不新增功能”。

最小安全改法：
- 新增一个后端小 helper，例如 `_is_recoverable_active_job(job_id, entry)`。
- `queued/running` 直接返回。
- `interrupted` 必须有 `input.*` 或安全 `source_url` 才返回。
- 测试只打 `get_active_jobs()`，不启动 worker、不碰真实用户 job 目录。

### 涉及文件列表
- `v3/backend/main.py:46-66` — `_verify_safe_url()`
- `v3/backend/main.py:847-866` — `/api/active-jobs`
- `v3/backend/main.py:750-783` — interrupted 重新提交恢复逻辑
- `v3/frontend/index.html:1294-1296` — 唯一自动拉取 `/api/active-jobs` 的前端入口
- `v3/test/test_harden_v307_sprint2.py:120-160` — 可复用的临时 `JOBS_DIR` / 全局索引恢复写法

## ④ 双签 diff + 简化审查：review + elon-musk — 2026-06-26
**依赖类型**：📖🌐 读全项目
**执行人**：Codex
**读了什么**：
- `git diff -- v3/backend/main.py v3/test/test_harden_v307_sprint2.py`
- `v3/backend/main.py:621-636` — 新增 `_is_recoverable_active_job()`
- `v3/backend/main.py:865-884` — `/api/active-jobs` 过滤接入点
- `v3/test/test_harden_v307_sprint2.py:165-240` — P303 回归测试

### 结论（下一步只看这里）
review 结论：未发现阻塞问题。
- 修复没有改 `startup()` 扫描逻辑，没有改 `_handle_existing_job()` 重新提交恢复逻辑。
- `/api/active-jobs` 对 `queued/running` 仍直接返回；只过滤 `interrupted` 里不可恢复的历史垃圾。
- `interrupted` 上传任务有 `input.*` 时仍返回，避免真实用户任务丢失。
- 内网/本机 URL 复用 `_verify_safe_url()`，不会绕过 P301 的安全边界。

elon-musk 简化结论：当前改法已经接近最小件数。更简单的“直接不返回所有 interrupted”会误伤真实中断任务；“删除旧 job 目录”是清缓存，不是代码修复；“新增状态 abandoned”会扩大数据契约。保留当前 helper 是合适的。

### 涉及文件列表
- `v3/backend/main.py:621-636` — 可恢复判断
- `v3/backend/main.py:865-884` — active jobs 出口过滤
- `v3/test/test_harden_v307_sprint2.py:165-240` — P303 红绿测试

## ⑤ 设计一致性走查 — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**读了什么**：
- `v3/backend/main.py:223-254` — 历史 job 扫描
- `v3/backend/main.py:621-636` — 可恢复判断
- `v3/backend/main.py:865-884` — active jobs 返回
- `v3/frontend/index.html:1044-1057` — interrupted 卡片显示
- `v3/frontend/index.html:1294-1296` — 空 localStorage 自动恢复入口

### 结论（下一步只看这里）
设计步骤对照：

| 设计步骤 | 代码对应 | 一致？ |
|----------|----------|--------|
| 服务启动扫描历史 job，重建内存索引 | `main.py:223-254` | 一致 |
| `/api/active-jobs` 只给首页自动恢复可继续处理的任务 | `main.py:865-884` + `_is_recoverable_active_job()` | 一致 |
| `interrupted` 上传任务可恢复，因为有 `input.*` | `main.py:629-633` | 一致 |
| `interrupted` URL 任务可恢复，但必须是安全 URL | `main.py:635-636` | 一致 |
| 前端只按后端返回的 id 恢复卡片 | `index.html:1294-1296` → `reconnectJobs()` | 一致 |

偏差：无。

### 涉及文件列表
- `v3/backend/main.py:621-636` — 本轮设计核心
- `v3/backend/main.py:865-884` — 后端恢复列表出口
- `v3/frontend/index.html:1294-1296` — 前端恢复入口

## ⑥ cso 安全审查 — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**读了什么**：
- `v3/backend/main.py:46-66` — `_verify_safe_url()`
- `v3/backend/main.py:621-636` — 新增可恢复判断
- `v3/backend/main.py:865-884` — `/api/active-jobs`
- `v3/test/test_harden_v307_sprint2.py:197-216` — 私网 URL 不返回测试

### 结论（下一步只看这里）
安全审查通过。
- 本轮没有新增外部请求，只是在返回列表前复用 `_verify_safe_url()` 做判断。
- 私网 URL、本机 URL 不会继续被首页自动恢复。
- 没有把 `source_url` 写入 `State.settings`，前端已有过滤仍保留。
- 没有新增文件删除、路径拼接下载、shell 调用。

### 涉及文件列表
- `v3/backend/main.py:46-66` — URL 安全判断
- `v3/backend/main.py:621-636` — 安全 URL 复用点

## ⑦ test-driven-development — 2026-06-26
**依赖类型**：📖 读指定文件
**执行人**：Codex
**运行了什么**：
- `.venv/bin/python -m pytest test/test_harden_v307_sprint2.py::TestP303ActiveJobsRecovery -q`

### 结论（下一步只看这里）
TDD 红绿完成。
- 红灯：新增测试 `test_active_jobs_skips_unrecoverable_interrupted_private_url` 失败，当前代码会把旧私网 URL interrupted job 返回给 `/api/active-jobs`。
- 修复：新增 `_is_recoverable_active_job()`，并在 `get_active_jobs()` 里过滤不可恢复 interrupted job。
- 绿灯：P303 目标测试 `2 passed, 2 warnings`。

### 涉及文件列表
- `v3/backend/main.py:621-636` — 新增 helper
- `v3/backend/main.py:865-884` — 接入过滤
- `v3/test/test_harden_v307_sprint2.py:165-240` — 红绿测试

## ⑧ verification-before-completion — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex
**运行了什么**：
- `.venv/bin/python -m pytest test/test_harden_v307_sprint2.py::TestP303ActiveJobsRecovery -q`
- `.venv/bin/python -m pytest test/test_harden_v307_sprint2.py test/test_p188_url_validation.py test/test_p193_p194_p195.py -q`
- `.venv/bin/python -m pytest test/ -q`

### 结论（下一步只看这里）
验证通过。
- P303 目标测试：`2 passed, 2 warnings`
- 相关回归：`51 passed, 2 warnings`
- 完整全量测试：`633 passed, 8 warnings in 97.44s`

完整全量测试使用提升权限运行，因为真实模型测试需要访问本机模型缓存/Metal 路径。

### 涉及文件列表
- `v3/test/test_harden_v307_sprint2.py` — P303 + 安全相关测试
- `v3/test/test_p188_url_validation.py` — URL 安全回归
- `v3/test/test_p193_p194_p195.py` — worker/main 状态链回归

## ⑨ Web QA — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex
**运行了什么**：
- `kill -TERM 88755`
- `screen -dmS video-subtitle-v308-p303-verify /Users/andy/Andy-agent/video-subtitle/v3/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8766`
- `lsof -nP -iTCP:8766 -sTCP:LISTEN`
- `curl -sS http://127.0.0.1:8766/api/health`
- `curl -sS http://127.0.0.1:8766/api/active-jobs`

### 结论（下一步只看这里）
Web 运行态验证通过。
- 新服务已从当前 `v3` 启动，screen 会话 `17117.video-subtitle-v308-p303-verify`，监听 PID `17139`。
- `/api/health` 返回 `{"ok":true,"version":"3.07"}`。版本号未 bump，属于发布线前正常状态。
- `/api/active-jobs` 返回 `{"jobs":[]}`。
- 因为前端空 `localStorage.activeJobs` 时只会根据 `/api/active-jobs` 写入恢复列表，所以这两个旧橙色卡片不会再被自动恢复。

### 涉及文件列表
- `v3/backend/main.py:865-884` — 运行态验证的 API 出口

## ⑩ 端到端实测 — 2026-06-26
**依赖类型**：🏃 跑应用
**执行人**：Codex

### 结论（下一步只看这里）
本轮 P303 是 Web 恢复列表 bug，不改转录、翻译、字幕生成和封装链路。完整全量测试已通过，且前一 HARDEN 步骤中用户真实短视频 standard E2E 已通过：job `c6bcf446ec` 已生成 SRT 和封装视频。

本轮额外验证的是旧任务恢复出口：重启服务后 `/api/active-jobs` 已为空，覆盖 P303 目标。

### 涉及文件列表
- `pipelines/harden-v308-2026-06-26.md:369-424` — 前一轮真实短视频 E2E 证据
- `v3/backend/main.py:865-884` — 本轮 P303 目标验证
