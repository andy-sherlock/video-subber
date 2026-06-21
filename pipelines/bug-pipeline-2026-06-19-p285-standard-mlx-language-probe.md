# P285 标准模式 MLX Whisper 卡死修复流水线 — 2026-06-19

## 元信息
- 涉及问题：P285
- 涉及文件：
  - `v2/backend/transcriber_v2.py`
  - `v2/backend/core/whisper_runner.py`
  - `v2/test/test_p266_mlx_whisper.py`
- 当前进度：①/⑩

## ① investigate — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件
**读了什么**：
- `v2/backend/transcriber_v2.py:1-15`：标准模式设计注释。
- `v2/backend/transcriber_v2.py:851-955`：标准模式实际执行路径。
- `v2/backend/core/whisper_runner.py:39-43`：Mac/Windows Whisper 后端选择。
- `v2/backend/core/whisper_runner.py:88-119`：Mac 端 `mlx-whisper` 完整转录调用。
- `v2/.venv/lib/python3.13/site-packages/mlx_whisper/transcribe.py:149-543`：`mlx_whisper.transcribe()` 的实际行为。
- `9f393b1:v2/backend/transcriber_v2.py:859-903`：ASR 分支旧版标准模式行为。
- `v2/test/test_p266_mlx_whisper.py:22-73`：现有 MLX 测试覆盖。
- `v2/test/test_standard_precise_mode.py:25-32`：现有标准/精准模式测试覆盖（该文件被 `.gitignore` 忽略，不放新增回归）。

### 结论（下一步只看这里）
P285 的根因已经确认：合入主线后，Mac 端标准模式把完整长音频交给了 `mlx-whisper`，而不是只做短音频语言探测。

标准模式原设计是：
1. Whisper 先听一小段，用来判断语言。
2. 如果语言是 `ja/zh/en/ko` 且概率够高，就停止 Whisper。
3. 切到 Qwen 跑完整音频。

旧 ASR 分支能跑通，是因为旧版使用 `faster-whisper`。`model.transcribe()` 返回的是 `segments_iter`，也就是“还没真正完整遍历的转录结果”。旧代码拿到 `info.language` 后，如果决定切 Qwen，就删除 `segments_iter` 和模型，不会完整转录全片。

现在 Mac 端换成了 `mlx-whisper`。`transcribe_with_mlx_whisper()` 直接调用：

```python
mlx_whisper.transcribe(audio_path, word_timestamps=True)
```

这个函数是一次性完整处理。它内部先检测前 30 秒语言，但不会在那里返回；它继续循环完整音频、累计 `all_segments`，最后才返回结果。所以 `MIDA-659.ts` 这种 8191 秒长音频会在“语言识别”阶段实际跑完整 Whisper。

前端一直显示“正在启动语音识别…”的原因也确认了：`worker.py` 在调用 `transcribe_v2()` 前更新一次进度；`transcriber_v2.py` 只有等 `transcribe_whisper()` 返回后，才会开始遍历段落并继续回调进度。MLX 完整转录长时间不返回，前端看起来就像卡死。

Windows 不受这个 bug 直接影响。`choose_whisper_backend()` 在 macOS Apple Silicon 返回 `mlx`，其他平台返回 `faster`。Windows 仍走旧的 `faster-whisper` 后端。

### 修复方向
用最小改法恢复标准模式的真实设计：
1. 在 `whisper_runner.py` 增加“语言探测”入口，只返回 `WhisperInfo`，不返回完整字幕段。
2. Mac/MLX 语言探测必须先抽取 30-60 秒短音频临时文件，再对短音频调用 `mlx_whisper.transcribe(..., word_timestamps=False)`。
3. 不能只传 `clip_timestamps` 给原始长音频，因为本地 `mlx-whisper` 源码会先 `load_audio(audio)` 读完整音频，再做 clip。
4. `transcriber_v2.py` 标准模式先调用语言探测；只有当标准模式决定继续 Whisper 主引擎时，才调用完整 `transcribe_whisper()`。
5. 精准模式保持完整 Whisper + Qwen 路径不变。

### 涉及文件列表
- `v2/backend/transcriber_v2.py:884-893`：当前标准模式直接完整调用 `transcribe_whisper()`，这是 bug 触发点。
- `v2/backend/transcriber_v2.py:898-917`：当前是在完整 Whisper 返回后才决定是否切 Qwen，顺序错了。
- `v2/backend/transcriber_v2.py:918-955`：只有完整 Whisper 返回后才进入段落遍历和进度回调。
- `v2/backend/core/whisper_runner.py:88-119`：MLX 后端一次性完整转录，且开启 `word_timestamps=True`。
- `v2/backend/core/whisper_runner.py:122-157`：Windows/Linux faster 后端，尽量不改动，避免误伤。
- `v2/test/test_p266_mlx_whisper.py:22-73`：需要补 MLX 语言探测测试。
- `v2/test/test_p266_mlx_whisper.py`：需要补标准模式“只探测、不完整 Whisper”的回归测试，避免忽略文件漏进 git。

### 关键代码
```python
# transcriber_v2.py 当前问题点
if use_whisper or use_standard_router:
    whisper_output, info = transcribe_whisper(
        audio_path,
        model_size=model_size,
        language=lang,
        vad_threshold=vad_threshold,
    )
```

```python
# whisper_runner.py 当前 MLX 行为
result = mlx_whisper.transcribe(
    audio_path,
    path_or_hf_repo=repo,
    language=language,
    word_timestamps=True,
)
```

### 下一步输入
第②步需要审查这个修复方向是否正确：
- 是否必须新建语言探测入口。
- 是否应该只在 Mac/MLX 抽短音频，Windows/faster 保持旧路径。
- 是否有必要把进度文案改成“正在检测音频语言…”。
- 测试是否必须覆盖“标准模式 Qwen 路径不能调用完整 Whisper”。

## ② 双签审查 — 2026-06-19
**执行人**：Codex
**依赖类型**：📋 看记录
**前置阅读**：① investigate 结论
**主审**：plan-eng-review
**副审**：steve-jobs-perspective

### 结论（下一步只看这里）
修复方向通过，但必须收窄范围。

工程审查结论：
1. 必须新增“语言探测”入口，不能复用完整 `transcribe_whisper()`。因为完整入口的语义是“返回完整字幕段”，标准模式这里只需要“语言 + 概率”。
2. Mac/MLX 语言探测必须先生成短音频文件，再调用 `mlx_whisper.transcribe()`。只传 `clip_timestamps` 不够安全，因为本地 MLX 源码会先读取完整音频。
3. Windows/faster 路径不应该大改。Windows 目前不触发这个 bug，修复应避免改变已验证行为。
4. 精准模式不应改变。精准模式本来就要完整 Whisper + Qwen。
5. 测试必须覆盖行为，而不是只测函数存在：标准模式选 Qwen 时，完整 Whisper 不得被调用。

乔布斯式减法审查结论：
这不是要加一套新流程，而是删掉一个错误动作：标准模式不应该先完整转录。最好的产品体验是用户上传视频后，很快看到“语言已识别，正在使用 Qwen 转录”，而不是在“启动语音识别”卡半小时。

### 审查决定
- 采用最小修法：`detect_language_with_whisper()` + 标准模式先探测。
- 不改 Qwen 主流程。
- 不改精准模式完整 Whisper 路径。
- 不改 Windows/faster 完整转录路径。
- 可轻微改进标准模式进度文案，让用户知道当前是在“检测语言”。

### 涉及文件列表
- `v2/backend/core/whisper_runner.py:88-119`：新增 MLX 短探测入口，完整转录入口保持原语义。
- `v2/backend/transcriber_v2.py:884-917`：标准模式先探测，再决定 Qwen 或完整 Whisper。
- `v2/test/test_p266_mlx_whisper.py:22-73`：补 MLX 探测不直接跑原始音频的测试。
- `v2/test/test_p266_mlx_whisper.py`：补标准模式路由测试。

## ③ 双签影响面 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖🌐 读全项目
**主审**：ln-021-codegraph
**副审**：bill-gates
**工具状态**：`hex-graph` MCP 未暴露，按 `ln-021-codegraph` fallback 规则用 `rg` 手工查调用点。

### 结论（下一步只看这里）
影响面小，主要集中在两个文件。

手工调用图：
1. `worker.py:518-525` 调用 `transcribe_v2(...)`。
2. `transcriber_v2.py:884-955` 决定是否调用 Whisper 完整转录。
3. `transcriber_v2.py:905-913` 决定是否调用 Qwen。
4. `whisper_runner.py:180-189` 决定 Mac 使用 MLX，其他平台使用 faster。
5. `qwen_asr_runner.py:361-395` 独立 Qwen 可接收完整音频路径和语言参数。

系统层面风险：
1. 如果短探测临时文件不删除，会留下缓存垃圾。修复必须 `finally` 删除。
2. 如果语言探测后立即释放 MLX 模型，标准模式 fallback 到 Whisper 会重新加载模型，慢但安全。考虑用户遇到的是长时间卡死，安全优先。
3. 如果 `ffmpeg` 抽短音频失败，应该让任务明确报错，不要静默回到完整长音频 MLX。否则 bug 会被藏起来。
4. 如果测试只 mock 了函数存在，没有验证“不调用完整 Whisper”，未来还会复发。

### 涉及文件列表
- `v2/backend/worker.py:511-525`：进度文案入口；可以不改，或只由 `transcriber_v2` 用 `progress_callback(-1)` 触发原文案。
- `v2/backend/transcriber_v2.py:884-917`：核心影响面。
- `v2/backend/core/whisper_runner.py:88-119`：核心影响面。
- `v2/backend/core/qwen_asr_runner.py:361-395`：下游 Qwen 调用，接口已支持 `language` 和 `cache_dir`。

### 下一步输入
进入第⑦ TDD 前，先写两个失败测试：
1. MLX 语言探测使用短临时音频，且 `word_timestamps=False`。
2. 标准模式语言探测选 Qwen 时，不调用完整 `transcribe_whisper()`。

## ④ 双签 diff + 简化 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖🌐 读全项目
**主审**：review
**副审**：elon-musk

### 结论（下一步只看这里）
diff 审查通过，未发现必须阻塞的问题。

主要风险检查：
1. 标准模式已经不再先完整调用 `transcribe_whisper()`。
2. 精准模式仍会完整调用 `transcribe_whisper()`，没有改变精准模式设计。
3. Windows/faster 后端仍由 `choose_whisper_backend()` 控制，完整转录入口不变。
4. MLX 探测短音频会在 `finally` 中删除临时文件，避免缓存垃圾。
5. `ffmpeg` 抽短音频失败会直接报错，不会静默退回“完整长音频 MLX 探测”，避免把 P285 藏起来。

简化审查：
- 没有新增复杂调度器。
- 没有改 Qwen 主流程。
- 没有加新的配置项。
- 修法只把“语言探测”和“完整转录”两个动作拆开。

### 涉及文件列表
- `v2/backend/core/whisper_runner.py:160-263`：新增短音频语言探测。
- `v2/backend/transcriber_v2.py:889-987`：标准模式先探语言，fallback 才完整 Whisper。
- `v2/test/test_p266_mlx_whisper.py:75-133`：MLX 探测测试。
- `v2/test/test_p266_mlx_whisper.py:147-238`：标准模式路由与行为测试。

## ⑤ 设计一致性走查 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件
**设计图**：`transcriber_v2.py:1-15` 顶部流程说明
**代码入口**：`transcriber_v2.transcribe()`

### 逐步骤对照

| 设计步骤 | 代码对应 | 一致？ |
|----------|----------|--------|
| 标准模式：Whisper language probe | `transcriber_v2.py:889-900` 调用 `detect_language_with_whisper()` | ✅ 一致 |
| 标准模式：choose Whisper or Qwen primary | `transcriber_v2.py:902-907` 调用 `choose_standard_primary_engine()` | ✅ 一致 |
| 标准模式：Qwen 主引擎 | `transcriber_v2.py:909-918` 调用 `run_qwen_asr()` | ✅ 一致 |
| 标准模式：只有 fallback 才跑完整 Whisper | `transcriber_v2.py:922-987` 仅在 `use_whisper or _standard_primary == "whisper"` 时完整转录 | ✅ 一致 |
| 精准模式：Whisper + Qwen 双引擎 | `transcriber_v2.py:874-885` 并行 Qwen，`922-987` 完整 Whisper | ✅ 一致 |

### 数据流追踪

| 步骤 | 数据包 | 手（函数） | 读/改/扔 | 设计图上？ |
|------|--------|------------|----------|------------|
| 原始音频 | `audio.wav` | `_create_language_probe_clip()` | 改：抽短临时 WAV | ✅ 语言探测需要 |
| 短音频 | `probe.wav` | `mlx_whisper.transcribe()` | 读：只返回语言信息 | ✅ |
| 语言信息 | `WhisperInfo(language, probability)` | `choose_standard_primary_engine()` | 读：决定主引擎 | ✅ |
| 原始音频 | `audio.wav` | `run_qwen_asr()` | 读：Qwen 跑全片 | ✅ |
| 原始音频 | `audio.wav` | `transcribe_whisper()` | 只在 fallback/precise 跑全片 | ✅ |

### 偏差汇总

| # | 类型 | 描述 | 决定 |
|---|------|------|------|
| 1 | 已修 | 旧代码把完整 Whisper 放在语言探测前 | 已拆成短探测入口 |
| 2 | 保留 | `worker.py` 仍显示“正在启动语音识别…” | 暂不阻塞；P285 主卡死已修，未来可单独优化前端文案 |

## ⑥ 安全审查 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件

### 结论（下一步只看这里）
本次不涉及认证、权限、外部 URL、用户账号或敏感数据，安全审查无阻塞项。

唯一需要看的安全点是临时文件：
- 临时文件由 `tempfile.mkstemp()` 创建，不使用用户可控文件名。
- `ffmpeg` 命令用参数列表传入，没有 shell 拼接，不引入命令注入。
- 临时文件在 `finally` 中删除。

## ⑦ TDD — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件

### RED
新增测试后，旧代码失败：
- `test_mlx_language_probe_uses_short_clip_without_word_timestamps` 失败：缺少 `_create_language_probe_clip()` 和 `detect_language_with_whisper()`。
- `test_standard_router_detects_language_before_full_whisper` 失败：标准模式源码里没有 `detect_language_with_whisper`。

### GREEN
修复后通过：
- `v2/backend/core/whisper_runner.py` 新增短探测入口。
- `v2/backend/transcriber_v2.py` 标准模式先探测语言，Qwen 路径不再完整跑 Whisper。
- 补充行为测试 `test_standard_qwen_path_skips_full_whisper`：如果标准模式 Qwen 路径调用完整 Whisper，测试会直接失败。

## ⑧ verification — 2026-06-19
**执行人**：Codex
**依赖类型**：🏃 跑应用

### 验证结果
- `v2/.venv/bin/python -m pytest v2/test/test_p266_mlx_whisper.py v2/test/test_standard_precise_mode.py v2/test/test_p276_standard_language_env.py -q` → `25 passed`。
- `python3 -m py_compile v2/backend/core/whisper_runner.py v2/backend/transcriber_v2.py` → 通过。
- `v2/.venv/bin/python -m pytest v2/test -q` → 当前 Codex/headless 环境收集阶段失败，错误为 `No Metal device available`。这是无 Metal/GPU 环境无法导入 MLX，不是本次 P285 目标测试失败。

## ⑨ QA（Web）— 2026-06-19
**执行人**：Codex
**依赖类型**：🏃 跑应用

### 结论
当前本地 `8766` 服务未响应，无法做浏览器端完整上传 QA。

已完成后台状态复查：
- 未匹配到 `backend.worker`、`ffmpeg`、`mlx_whisper`、`Qwen`、`Whisper` 残留进程。
- 当前高 CPU 主要来自 `WindowServer` 和 Codex，不是视频翻译任务。

## ⑩ 端到端实测 — 2026-06-19
**执行人**：Codex
**依赖类型**：🏃 跑应用

### 结论
当前 Codex/headless 环境没有可用 Metal/GPU，无法真实跑 `MIDA-659.ts` 的 MLX 标准模式端到端。

下一步真实验收：
1. 在普通 Mac 前台环境启动本地服务。
2. 用标准模式重跑 `MIDA-659.ts`。
3. 观察日志应快速出现“标准模式: Whisper 短音频语言探测开始”以及“标准模式: 语言适合 Qwen，停止 Whisper，切换 Qwen”。
4. 前端不应长时间卡在“正在启动语音识别…”。

## 当前流水线状态
- ① investigate：✅
- ② 双签审查：✅
- ③ 双签影响面：✅
- ④ 双签 diff + 简化：✅
- ⑤ 设计一致性走查：✅
- ⑥ 安全审查：✅
- ⑦ TDD：✅
- ⑧ verification：✅ 目标测试通过；全量受 Metal 环境限制
- ⑨ QA：⏳ 本地服务未运行，未做浏览器完整链路
- ⑩ 端到端：⏳ 需普通 Mac 前台环境真实视频验收
