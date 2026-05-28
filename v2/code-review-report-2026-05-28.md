# Code Review Report -- Video Subber V2.11

**审查日期**: 2026-05-28
**审查范围**: `backend/main.py`, `backend/worker.py`, `backend/worker_manager.py`, `backend/ws_manager.py`, `backend/config_manager.py`, `backend/key_manager.py`, `backend/subtitle_style.py`, `backend/env_check.py`, `backend/core/checkpoint.py`, `backend/usage_reporter.py`, `backend/translation/orchestrator.py`, `desktop.py`, `frontend/index.html`

---

## 🔴 Critical

### C1: FROZEN 模式下所有进度更新丢失

- **文件**: `desktop.py` 行 81-90, `backend/worker.py` 行 538-545
- **问题**: FROZEN/exe 模式下，`desktop.py` 替换了 `_worker.emit` 使其通过 `asyncio.run_coroutine_threadsafe` 直连 WebSocket。但 `worker.py` 中的 `stderr_progress()` 函数（行 538）**不调用 `emit()`**，而是直接写入模块级变量 `_real_stdout`。在 FROZEN 模式下没有父进程读取 stdout，因此所有中间进度消息（extract / transcribe / translate / subtitle / burn 的 percent 和 message）全部丢失，前端进度条始终停留在 0%。

  ```python
  # worker.py 行 538 -- 绕过 emit，直接写 stdout
  def stderr_progress(stage, percent, message, detail=""):
      _real_stdout.write(msg + "\n")  # <-- FROZEN 下无人读取
  ```

  `stderr_progress` 被调用了 17 次（行 315-477），每次都是 pipeline 的关键进度节点。
- **建议**: 将 `stderr_progress` 也纳入 `_direct_emit` 的替换范围，或者在 `stderr_progress` 内部检测 FROZEN 模式并调用 `_direct_emit`。最干净的做法是在 `desktop.py` 中同样替换 `_worker.stderr_progress`（它也是模块级函数）。

### C2: 前端单任务 UI 残留 DOM 元素（5 个死元素）

- **文件**: `frontend/index.html` 行 353-361
- **问题**: `progressSection` (line 353), `progressFill` (line 354), `progressStep` (line 355), `resultSection` (line 357), `failedDetail` (line 361) -- 这 5 个 DOM 元素是旧版单任务 UI 的进度/结果展示区域。V2.11 已全面迁移到多任务卡片 UI（`job-card` 组件），这些元素在全部 JS 代码中**从未被设置 `active` 类名**，始终处于 `display:none` 状态，不会被用户看到。
- **建议**: 删除这些死 DOM 元素及相关 CSS 规则（行 139-157），减小 HTML 体积。

### C3: `showError()` 函数从未调用

- **文件**: `frontend/index.html` 行 973
- **问题**: `showError(msg)` 函数定义存在但全代码库中没有任何调用点。错误展示现在通过 job-card 内嵌的 `err-{jid}` 元素处理，不再使用全局 error overlay。`errorOverlay` 本身仅由 `closeError()` 关闭（行 974）。
- **建议**: 删除 `showError()` 函数；若确认 `errorOverlay` 也不再需要，一并删除。

---

## 🟡 Warning

### W1: Tier 判断未使用后端返回值

- **文件**: `frontend/index.html` 行 510-531, `backend/main.py` 行 451-464
- **问题**: `updateBackendLists(data)` 中判断 tier 使用的是 `State.settings.tier`（前端本地状态），而**不使用 `data.current_tier`**（后端 `_effective_tier()` 解析后的权威值）。在正常操作中两者一致，但若 URL 传入无效 tier（如 `?tier=gold`），后端会降级返回 `"free"`，前端 UI 却可能显示付费功能。当前因为 `isDev` 门禁和 TIER_FEATURES 白名单，未造成实际 bug，但属于 fragile code。

  ```js
  // 行 510 -- 应该用 data.current_tier 而非 State.settings.tier
  if(isDev && State.settings.tier==='paid_3'){
  ```
- **建议**: 将三处 tier 判断替换为 `data.current_tier`，确保前端展示与后端实际生效的 tier 一致。

### W2: 字幕字体列表仅含 macOS 字体

- **文件**: `frontend/index.html` 行 278
- **问题**: 样式字体 `<select>` 硬编码了 5 个 macOS 字体（PingFang SC, Noto Sans CJK SC, Heiti SC, Songti SC, Kaiti SC），Windows 用户无法选择 Microsoft YaHei、SimHei 等平台原生字体。后端 `config_manager.py` 行 12 有平台判断逻辑但仅在 DEFAULT_CONFIG 中生效，前端下拉列表不受其影响。
- **建议**: 前端根据 `navigator.platform` 动态构建字体列表，或至少添加 `"Microsoft YaHei"` 和 `"SimHei"`。

### W3: `onModelChange()` 在 `custom` 平台切换后错误显示自定义模型输入框

- **文件**: `frontend/index.html` 行 664-669, 672
- **问题**: 当用户在上一个 LLM 平台选择了"其他模型..."（`__custom__`），然后切换到 `custom` 平台时：
  1. `onPlatformChange()` 隐藏 `apiModel` 并 remove `customModel` 的 visible 类（正确）
  2. `onModelChange()` 随后执行，检测到 `apiModel.value === '__custom__'`（残留值），重新将 visible 加回 `customModel`（错误）

  结果：`#apiCustomModel` 输入框在不该出现时闪现。用户在 `custom` 平台下既看到 `apiModelName`（正确）又看到 `apiCustomModel`（错误）。

  ```js
  // 行 672 -- onModelChange 无条件跟在 onPlatformChange 后面
  $('apiPlatform').addEventListener('change',()=>{onPlatformChange();onModelChange()});
  ```
- **建议**: 在 `onPlatformChange()` 返回前重置 `apiModel.value = ''`，或在 `onModelChange()` 中加 `if (getComputedStyle(modelSel).display === 'none') return;` 防护。

### W4: `usage_reporter.replay_fallback()` 从未调用

- **文件**: `backend/usage_reporter.py` 行 77-90
- **问题**: `replay_fallback()` 用于启动时重放上次失败的用量上报记录。函数定义存在但 **没有被任何代码调用**（`main.py` 的 `startup()` 中没有调用它）。失败的用量记录会永久保存在 `pending_usage.json` 中而不被重试。
- **建议**: 在 `startup()` 中添加 `threading.Thread(target=replay_fallback, daemon=True).start()`，或在确认 V3 之前不需要此功能时添加 TODO 注释并标记为 dead code。

### W5: 后端 `DEFAULT_CONFIG` 缺少多个前端使用的字段

- **文件**: `backend/config_manager.py` 行 7-22
- **问题**: `DEFAULT_CONFIG` 缺少 `burn`, `subtitles`, `contextAware` 字段。这些字段由前端 `saveConfig()` 写入并在后续加载时可用，但首次启动（用户从未保存过配置时）`load_config()` 返回的对象不包含它们。当前因为 `loadConfig()` 的条件 `c.translationMode` 为真时才合并，且前端 `State.settings` 有自身默认值，未造成实际故障，但字段集的不一致增加了维护风险。
- **建议**: 在 `DEFAULT_CONFIG` 中补充 `"burn": True, "subtitles": "no", "contextAware": False`，与前端初始值对齐。

### W6: `_llmFallbackPending` 声明但未使用

- **文件**: `frontend/index.html` 行 950
- **问题**: `let _llmFallbackPending=null;` 定义后从未被引用。实际的 LLM fallback 逻辑使用 `window._pendingFallbackJob`（行 946, 953, 957）。
- **建议**: 删除 `_llmFallbackPending` 变量声明。

### W7: `handleCardMsg` 未处理 `token_usage` 类型消息

- **文件**: `frontend/index.html` 行 938-948
- **问题**: Worker 会发出 `{"type": "token_usage", "usage": {...}}` 消息，后端 `_tracking_send` 将其存入 JOBS dict（`main.py` 行 872-873），但前端 `handleCardMsg` 没有对应的 case 分支。消息被静默忽略。当前这被视为"by design"（token 用量不向用户展示），但 `j["token_usage"]` 也因此从未被任何 REST API 返回。
- **建议**: 明确设计意图 -- 如果 token_usage 完全不需要实时展示，考虑移除 WebSocket 推送直接写入磁盘日志；如果未来需要前端展示，添加对应的 case 分支。

### W8: `GET /api/jobs/{job_id}` fallback 路径返回字段不完整

- **文件**: `backend/main.py` 行 750-774
- **问题**: 当 JOBS dict 未命中、从磁盘 meta.json fallback 时（服务重启后），返回的 JSON 不包含 `token_usage` 和 `failed_segments` 字段。虽然主路径（JOBS dict 命中时）同样不返回这些字段（仅在 done 消息中通过 WebSocket 推送），但 fallback 分支与主分支返回结构不完全一致。
- **建议**: 若有前端需要 consume 这些字段，在 fallback 分支中补充；否则确认当前设计无影响后关闭。

---

## 🟢 Info / OK

### I1: 前后端 API 字段一致性 -- 全部匹配

- `POST /api/dev/activate` 的 `{code}` 与后端 `payload.get("code")` ✅
- `POST /api/jobs/{job_id}/llm-fallback` 的 `{decision}` 与后端 `payload.get("decision")` ✅
- `GET /api/jobs/{job_id}` 返回的 `process_alive` 字段前端正确使用（行 925） ✅
- `GET /api/active-jobs` 返回的 `data.jobs[].job_id` 前端正确消费（行 988） ✅
- WebSocket 消息 type 枚举（`progress`, `done`, `error`, `llm_failed`, `token_usage`）前后端完全一致 ✅
- Worker `emit()` 和 `stderr_progress()` 发出的 JSON 结构与 `_tracking_send` 消费的字段完全对齐 ✅
- `POST /api/jobs` 返回的 `{found, job_id, status, config}` 前端 `handleFoundJob` 和 `handleCardMsg` 正确消费 ✅

### I2: pywebview 线程安全 -- 正确

- uvicorn 服务器运行在 daemon 线程，`webview.create_window()` 和 `webview.start()` 在主线程调用，符合 pywebview 要求 ✅
- `asyncio.run_coroutine_threadsafe` 用法正确，loop 引用在 startup 时存储 ✅
- FROZEN 模式下 Worker 线程为 daemon=True，主进程退出时自动清理 ✅

### I3: 后端所有路由已挂载 -- 16 个端点全部激活

- 3 个 GET（health, env, backends, config, active-jobs, jobs/{id}, download/video, download/srt）
- 2 个 POST（jobs, reset, dev/activate, llm-fallback）
- 1 个 PUT（config）
- 1 个 DELETE（jobs/{id}）
- 1 个 WebSocket（/ws/{job_id}）

全部检查通过，无未挂载路由 ✅

### I4: 前端 DOM ID 引用检查 -- 核心 ID 全部存在

以下 38 个前端 ID 全部存在对应 DOM 元素：`dz`, `fileInput`, `dzFile`, `localPathInput`, `localPathGuide`, `startBtn`, `subToggle`, `burnSelect`, `burnWarn`, `styleSubSection`, `styleFont`, `styleSize`, `styleColor`, `styleOutline`, `styleOutlineWidth`, `stylePreview`, `styleConfirmBtn`, `styleConfirmed`, `modelSection`, `modelDots`, `modelCurrent`, `transSection`, `transModeTitle`, `modeRow`, `modeToggle`, `priorityList`, `apiTip`, `apiRow`, `apiPlatform`, `apiModel`, `apiCustomModel`, `apiModelName`, `apiName`, `apiKeyInput`, `apiEndpoint`, `apiAddBtn`, `apiKeyList`, `modelHint`, `advCard`, `advToggle`, `advBody`, `ctxToggle`, `jobCards`, `devToolbar`, `devTierBadge`, `devLoginOverlay`, `devLoginTrigger`, `devCodeInput`, `devLoginError`, `llmFallbackOverlay`, `llmFallbackMsg`, `savePathDisplay`, `savePathBtn`, `envBar`, `errorOverlay`, `errorText`, `devNoFfmpeg`, `devNoLibass`, `devNoWhisper` ✅

### I5: FROZEN 模式 V1.3 路径修正 -- 正确

- `desktop.py` 行 34-39: 将 `BUNDLE_DIR / "v1.3"` 注入 sys.path ✅
- `backend/main.py` 中的 FRONTEND 路径 (`Path(__file__).parent.parent / "frontend"`) 在打包后自然正确 ✅

### I6: `_tracking_send` 猴子补丁 -- 安全

- `ws_manager.send` 在 `main.py` 行 890 被替换为 `_tracking_send`，后者内部调用 `_original_send` ✅
- `worker_manager._forward` 在每次调用时重新 `from .ws_manager import ws_manager`，通过实例属性查找获取当前绑定的方法（动态分发），正确调用 `_tracking_send` ✅
- 不存在递归调用风险 ✅

### I7: `cancel_job` 的 `time.sleep(0.1)` -- 可接受

- `main.py` 行 811: 在 `worker_manager.cancel()` 后等待 0.1s，目的是给 OS 一点时间完成进程清理后再删除日志目录。非关键路径，可接受 ✅

### I8: `done_job_config` 去重逻辑 -- 完整覆盖三种状态

- 已完成 (done): 复用结果，提示"修改设置后可重跑" ✅
- 运行中 (running/queued): 复用卡片，连接 WebSocket 恢复进度 ✅
- 中断 (interrupted/error): 恢复任务，重新启动 worker ✅

### I9: `saveConfig` localStorage 安全脱敏 -- 正确

- `frontend/index.html` 行 739: 写入 localStorage 的配置显式剔除了 `apiKeys[].key` 字段，仅保留元数据 ✅
- 服务端 `config_manager.py` 使用 Fernet 加密存储完整 API Key ✅

### I10: 配置变更增量缓存失效 -- 逻辑正确

- `backend/worker.py` 行 290-313: `SETTING_STAGE` 映射了每个设置影响的 pipeline 阶段，配置变更时从受影响阶段起删除所有后续缓存 ✅
- 字幕阶段特殊处理（行 271-276）: 同时清除 `.srt` 和 `.ass`，避免旧 ASS 被复用 ✅

---

## 统计

| 级别 | 数量 | 说明 |
|------|------|------|
| 🔴 Critical | 3 | C1(FROZEN进度丢失), C2(5个死DOM元素), C3(showError从未调用) |
| 🟡 Warning | 8 | W1-W8 |
| 🟢 Info | 10 | 确认无误的项目 |

**建议优先修复**: C1 (FROZEN 模式进度丢失) -- 这会导致打包后的 exe 版本在用户等待期间完全没有任何进度反馈，体验极差且可能被误认为卡死。
