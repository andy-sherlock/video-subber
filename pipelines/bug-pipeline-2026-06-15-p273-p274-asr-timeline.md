# ASR 时间轴专项 Bug 流水线 — 2026-06-15

## 元信息
- 涉及问题：P274, P273；VAD 评估；WhisperJAV 短段文本生成 + FA 对齐 + StableTS 重组方案评估
- 涉及文件：
  - `v2/backend/core/qwen_asr_runner.py:138-211` — Qwen 长视频切片、padding 提取、MLX 时间回填
  - `v2/backend/transcriber_v2.py:202-216` — Qwen words 转字幕段时使用词首/词尾时间
  - `v2/backend/transcriber_v2.py:430-473` — Qwen 主引擎按词组装字幕段
  - `v2/backend/core/vad_filter.py:1-7` — 当前 VAD 只做语音占比健康检查，不做删除
  - `problems.md:3108-3114` — P168 旧路径曾修过同类 5 秒偏移
  - `problems.md:5730-5754` — P274 当前诊断记录
  - `problems.md:325-394` — P013/P015 早期 VAD 误拦真实对话记录
- 当前进度：⑧/⑩（P274 代码已修；验证为相关测试通过，全量仍待真实环境）
- 本流水线的实际修复顺序：
  1. 先修 P274：长音频 chunk padding 时间回填错误
  2. 再修 P273：Qwen 主引擎组装空洞、瞬闪短字幕、最小时长
  3. 再评估 VAD：只能做切片/断句/对齐恢复参考，不能做事前硬删除
  4. 最后评估 WhisperJAV 式大方案：短段文本生成 + FA 对齐 + StableTS 重组

## ① investigate — 2026-06-15
**执行人**：Claude
**依赖类型**：📖 读指定文件
**读了什么**：
- `v2/backend/core/qwen_asr_runner.py:138-211`
- `v2/backend/transcriber_v2.py:202-216`
- `v2/backend/transcriber_v2.py:430-473`
- `problems.md:3108-3114`
- `problems.md:325-394`
- `problems.md:5730-5754`
- `task_plan.md:139-140`
- `/Users/andy/task_plan.md` 当前项目栏
- WhisperJAV 当前源码：
  - `whisperjav/pipelines/qwen_pipeline.py`
  - `whisperjav/modules/subtitle_pipeline/orchestrator.py`
  - `whisperjav/modules/subtitle_pipeline/reconstruction.py`

### 结论（下一步只看这里）

这次不要把四件事混成一个大改。正确顺序是：先修现在确定存在的时间轴 bug，再修字幕组装质量，最后再评估是否引入更大的架构。

第一件事是 P274。它是确定 bug，优先级最高。长视频切片时，代码实际送给 Qwen 的不是 `t_start-t_end`，而是前后各多 5 秒的临时音频。比如 `1200-1500s` 这个 chunk，实际提取的是 `1195-1505s`。Qwen 返回的时间是相对这个临时音频的，所以模型返回 `10s` 时，真实视频时间应该是 `1195+10=1205s`。当前代码用了 `1200+10=1210s`，所以字幕整体晚约 5 秒。用户在 21:39/22:00 看到“人物说话时没字幕，说完字幕才出现”，符合这个错误。

P274 的修复必须做三件事。第一，让 `_extract_audio_chunk()` 把真实提取起点 `extract_start` 和终点 `extract_end` 返回给调用方，或者在 `_transcribe_chunk()` 里用同一套公式重新算出来。第二，MLX 路径里 `result.segments` 的每个 `start/end` 必须加 `extract_start`，不能加 `t_start`。第三，padding 区间要裁掉：真实时间早于 `t_start` 的词属于上一个 chunk，晚于 `t_end` 的词属于下一个 chunk，不能重复进入当前 chunk；跨边界词可以 clamp 到边界。

P274 的测试不能只测短音频。必须覆盖 `t_start=1200`、padding=5、模型返回 `start=10` 时结果为 `1205`；模型返回 `start=3` 时应识别为 padding 区内容并被裁掉或 clamp。还要同时覆盖旧 PyTorch `time_stamps` 路径和当前 MLX `result.segments` 路径，防止 P168 这种问题以后换库又回来。

第二件事是 P273。P273 不是“整体晚 5 秒”的主因，但它会把观感放大得很难受。当前 `_words_to_segment()` 直接把字幕段时间设成第一个词 start 到最后一个词 end；`_assemble_qwen_primary()` 按 pause、句末词、最长跨度、最长字数切段。真实重跑里仍有大量 `<0.2s` 字幕和 `>3s` 空洞，说明组装层还需要做最小时长、短词并入、孤立短字幕保护、空洞诊断。这个要在 P274 修完后做，因为底层词时间没放准之前，组装调参没有意义。

第三件事是 VAD。VAD 的意思是“语音活动检测”，也就是先判断哪些时间段像有人声。我们早期 P013/P015 已经证明：把 VAD 当门卫、在转录前删音频，会误拦真实对话，甚至把前 0-36 秒清晰对白全拦掉。所以 VAD 不能回到“事前硬删除”的角色。但 VAD 仍然可以做参谋：帮我们找静音间隙、给 Qwen 分更小 frame、在对齐崩掉时辅助重分配时间。SenseVoice 和 VAD 不是同一个东西。SenseVoice 更像声音类型标签，告诉我们这是人声、笑声、呼吸、音乐；VAD 更像时间尺，告诉我们哪里可能有人声、哪里可能是静音。后续如果引入，只能作为辅助信号，不能单独决定删字幕。

第四件事是 WhisperJAV 式大方案。WhisperJAV 当前 Qwen pipeline 不是直接相信长音频 ASR 自带时间戳。它先做 safe chunking，把音频切成 12-48 秒左右的短场景；再用 VAD/grouping 做更小的人声 frame；然后 Qwen 只负责生成文本；Qwen ForcedAligner 负责给词级时间；最后用 stable-ts/重组逻辑把词级时间重组成字幕段。也就是说，它不是让 FA 直接吐“句级字幕”，而是先拿词级时间，再通过后处理聚成字幕句。这个方向值得评估，但属于架构升级，不应该和 P274/P273 的确定 bug 修复混在一个小 diff 里。

### 涉及文件列表
- `v2/backend/core/qwen_asr_runner.py:138-160` — `_extract_audio_chunk()` 当前只执行 ffmpeg，不返回 `extract_start/extract_end`
- `v2/backend/core/qwen_asr_runner.py:163-211` — `_transcribe_chunk()` 当前注释和实现都把 segment 时间加 `t_start`
- `v2/backend/transcriber_v2.py:202-216` — `_words_to_segment()` 用词首/词尾决定字幕段时间，P273 会在这里或调用前后加最小时长/短词并入
- `v2/backend/transcriber_v2.py:430-473` — `_assemble_qwen_primary()` 是 Qwen 主引擎组装入口，P273 的主要修复点
- `problems.md:3108-3114` — P168 证明同类 5 秒偏移以前修过
- `problems.md:325-394` — P013/P015 证明 VAD 不能做硬过滤
- `problems.md:5730-5754` — P274 当前根因、影响、修复方案

### 关键代码

```python
extract_start = max(0.0, start_s - _AUDIO_PAD_S)
extract_end = end_s + _AUDIO_PAD_S
```

```python
segments = [
    {"text": s["text"], "start": s["start"] + t_start,
     "end": s["end"] + t_start}
    for s in raw_segments
]
```

这里的问题就是：提取音频用的是 `extract_start`，回填时间却用 `t_start`。

### 下一步输入

进入 ② 双签审查：
- 主审 `plan-eng-review`：审 P274/P273 的修复方案是否技术正确，尤其是 padding 裁剪、chunk 边界、MLX/PyTorch 双路径测试。
- 副审 `steve-jobs-perspective`：审这个四步顺序是否足够简单，是否把确定 bug 和架构升级分开，避免一次改太大。

## ② 双签审查 — 2026-06-15
**执行人**：Claude
**依赖类型**：📋 看记录
**前置阅读**：只读了本管道文件第 ① 步结论，未主动读新代码。

### 主审：plan-eng-review 结论

方案方向通过，但要加三条硬约束，否则 P274 有可能“表面修好，边界仍坏”。

第一条硬约束：P274 必须把“时间换算”和“padding 裁剪”放在同一个函数或同一个小 helper 里。原因是这两个动作不能分开。先把 `raw_start + extract_start` 算成真实视频时间，再判断它是不是落在当前 chunk 的 `[t_start, t_end]` 内。如果先裁剪 raw 时间，或者裁剪时还用 `t_start` 当基准，就会在 chunk 边界继续错。

第二条硬约束：P274 的测试必须覆盖 chunk 交界。只测 `1200 + 10` 改成 `1195 + 10` 不够。还要测三类词：
- padding 前区词：真实时间小于 `t_start`，应丢弃或 clamp
- 正常区词：真实时间在 `[t_start, t_end]` 内，应保留
- padding 后区词：真实时间大于 `t_end`，应丢弃或 clamp

第三条硬约束：不要在 P274 里顺手改 P273。P274 修的是“词在视频时间轴上的绝对位置”。P273 修的是“这些词怎么合成可读字幕”。这两个必须拆成两个 diff、两组测试、两次真实复查。否则真实重跑如果还有问题，会分不清是时间回填没修好，还是组装规则没调好。

P273 的工程审查结论是：可以修，但必须等 P274 后再动。P273 的测试目标要从用户观感倒推：全片 `<0.2s` 字幕数量下降，`>3s` 空洞数量下降，21:39/22:00 局部不再出现“说话时无字幕、说完才出字幕”。单元测试只能证明规则没坏，不能证明真实视频体验过关。

VAD 的工程审查结论是：不能作为 P273 的第一修法。它只能作为后续实验项。引入 VAD 的第一版如果要做，也只能输出“候选静音点/候选人声区间”，不能删除音频、不能删除词、不能单独改字幕时间。

WhisperJAV 式方案的工程审查结论是：这是架构升级，不是 P274/P273 的补丁。要单独开设计任务，先做离线对照实验：同一段 FJIN-141，分别用当前方案和短段 + FA + 重组方案跑，比较时间延迟、短字幕数量、空洞数量、耗时和失败率。没有这个对照，不应直接接入主链。

### 副审：steve-jobs-perspective 结论

这个四步顺序是对的，因为它先砍噪音，再谈架构。

现在用户痛点不是“我们有没有一个很高级的字幕系统”。用户看到的是：人已经说完，字幕才出来。所以第一刀必须砍到时间轴上。P274 是最小、最清楚、最该先修的点。

P273 是第二刀。它解决的是字幕读起来碎、闪、空。它不应该抢在 P274 前面，因为那是在歪时间轴上做排版。

VAD 不要急着加回主链。以前它误删过真实对白，这说明它不能做裁判。它最多做助手。产品上最糟糕的体验不是“我们没有用 VAD”，而是“用户明明听到人说话，却没有字幕”。任何会再次造成误删对白的设计，都应该先挡住。

WhisperJAV 的方案值得学，但不能照搬。它真正有价值的不是“用了 VAD”这四个字，而是它把长视频拆短，把“听文字”和“对时间”分开，再把词时间重组成字幕。我们要学的是这个结构，不是把一堆库一次性塞进项目。

### ② 结论（下一步只看这里）

双签通过，但范围要锁死：

1. 先做 P274，小 diff，只碰 Qwen chunk 时间回填和 padding 裁剪。
2. P274 必须有 MLX/PyTorch 双路径测试、padding 前中后测试、chunk 边界测试。
3. P274 修完后真实重跑 FJIN-141，先看 21:39/22:00 是否从“说完才出字幕”变成基本同步。
4. P273 放在 P274 之后，专门处理短字幕、空洞、最小时长和短词并入。
5. VAD 暂不进入主修复，只做评估项。原则：只辅助，不删除。
6. WhisperJAV 式短段 + FA + StableTS 重组单独作为架构实验，不混入当前 bug 小修。

### 下一步输入

进入 ③ 双签影响面：
- 主审 `ln-021-codegraph`：查 P274/P273 修改会影响哪些调用链，尤其是 Qwen chunk 输出如何流到 transcript、SRT、L1、翻译。
- 副审 `bill-gates`：从系统层面判断这四步是否会引入新复杂度，特别是 VAD 和 WhisperJAV 方案是否应保持为实验分支。

## ③ 双签影响面 — 2026-06-15
**执行人**：Claude
**依赖类型**：📖🌐 读全项目
**读了什么**：
- `v2/backend/core/qwen_asr_runner.py:138-211`
- `v2/backend/core/sensevoice_runner.py:46-88`
- `v2/backend/transcriber_v2.py:570-592`
- `v2/backend/transcriber_v2.py:661-750`
- `v2/backend/transcriber_v2.py:880-894`
- `v2/backend/worker.py:729-746`
- 通过 `rg` 手工追踪 `run_qwen_asr`、`_transcribe_chunk`、`_extract_audio_chunk`、`_smart_assemble`、`build_srt` 调用链。
- 说明：本环境没有可直接调用的 hex-graph MCP 工具，`ln-021-codegraph` 按技能规则走手工 grep/read fallback，因此影响面结论可信度为“手工静态追踪”，不是图数据库输出。

### 主审：ln-021-codegraph 影响面结论

P274 的上游入口是 `run_qwen_asr()`，它在 `transcriber_v2.py` 有两条调用路径：
- 标准路径：`transcriber_v2.py:661-670` 启动 Qwen 独立线程，调用 `run_qwen_asr(audio_path, language=detected_lang)`
- 精确串行 fallback：`transcriber_v2.py:746-750` 在没有 Qwen 线程时串行调用 `run_qwen_asr(audio_path, language=detected_lang)`

P274 的错误发生在 `qwen_asr_runner.py:163-211` 的 `_transcribe_chunk()`。这里把 MLX 返回的 `result.segments` 从 chunk 相对时间变成全片绝对时间。当前用的是 `s["start"] + t_start`，正确基准应是 `extract_start`。这个点一旦修正，所有下游都会拿到更准的 Qwen 词级时间。

P274 的直接下游是 `_smart_assemble()`。`transcriber_v2.py:890-894` 在 Qwen 是主引擎时，调用 `_smart_assemble(_qwen_raw, whisper_segments=_whisper_segments, ...)`，再把结果写回 `results`。也就是说，P274 修的是 `_qwen_raw` 的时间骨架，P273 修的是 `_smart_assemble()` 怎么把这些词组成字幕段。

P274 还会影响 L1。`transcriber_v2.py:880-888` 会把 Qwen chunk 变成 alignment，然后做 L1 双信号判断。如果 Qwen 词时间整体晚 5 秒，L1 的声音标签和文字时间也会错位。修 P274 后，L1 的时间对齐会一起受益。

P274 还会影响最终 SRT，但不是直接调用。路径是：`Qwen chunks -> _smart_assemble -> results -> filter_hallucinations -> 翻译 -> worker.py:731 word_align_segments -> worker.py:743 llm_split_segments -> worker.py:746 build_srt`。所以用户看到的字幕延迟，最终表现是在 SRT，但根因在 ASR 源稿。

最大影响面风险是 `_extract_audio_chunk()` 被 SenseVoice 复用。`sensevoice_runner.py:54` 从 Qwen 模块导入 `_extract_audio_chunk`，`sensevoice_runner.py:87` 直接调用它。如果 P274 选择“修改 `_extract_audio_chunk()` 返回值”，虽然 Python 允许忽略返回值，但这会改变公共 helper 的语义，后续容易混乱。更稳的 P274 小修是：保持 `_extract_audio_chunk()` 现有签名不变，在 `_transcribe_chunk()` 里用同一公式计算 `extract_start/extract_end`，或者新增 `_audio_extract_bounds(start,end)` helper，让 Qwen 和 SenseVoice 都能复用同一个边界计算。

P273 的影响面集中在 `_assemble_qwen_primary()` 和 `_words_to_segment()`。这两个函数只改变 Qwen 主引擎如何组装字幕段，不应改变 Qwen 原始词时间。P273 的下游影响是可读性、短字幕数量、空洞数量、L3 时长异常数量。

VAD 的影响面如果进入主链会很大。它可能影响切片、组装、L1 判断、空洞填补。因为历史 P013/P015 已证明硬过滤有误删风险，所以第一个 VAD 实验只能输出参考区间，不能进入删除逻辑。

WhisperJAV 式方案影响面最大。它会改变主架构：从“Qwen 直接给带时间的 segments”变成“Qwen 生成文本，FA 对齐词时间，再由 stable-ts 重组字幕段”。这不是 P274/P273 的补丁，必须单独设计、单独开实验分支。

### 副审：bill-gates 系统风险结论

从系统角度看，这四步的正确分层是：
- P274 是 correctness bug，也就是基础正确性问题。系统不知道真实时间，后面所有模块都会被污染。
- P273 是 product quality bug，也就是读起来不舒服的问题。它依赖 P274 的正确时间。
- VAD 是 auxiliary signal，也就是辅助信号。它有价值，但误用会制造更大的召回损失。
- WhisperJAV 式方案是 architecture option，也就是架构选项。它可能提高上限，但也会增加依赖、耗时、失败模式和调试成本。

系统风险最大的是把四件事一次做完。那样失败后无法知道是哪一层造成问题。正确做法是每一步都有可量化出口：
- P274 出口：21:39/22:00 不再整体晚约 5 秒；chunk 边界测试通过。
- P273 出口：`<0.2s` 字幕、`>3s` 空洞、最大空洞明显下降；局部人工看起来同步。
- VAD 出口：只证明它能提供有用边界，不能证明就马上接入删除。
- WhisperJAV 出口：离线实验对比当前主链有明显质量收益，且耗时和失败率可接受。

### ③ 结论（下一步只看这里）

影响面审查通过，但 P274 的实现方式要从第 ① 步略微收紧：

1. 不优先修改 `_extract_audio_chunk()` 的返回值，因为 SenseVoice 正在复用这个 helper。
2. 优先新增一个小 helper，例如 `_audio_extract_bounds(start_s, end_s) -> (extract_start, extract_end)`。
3. `_extract_audio_chunk()` 用这个 helper 算 ffmpeg 提取范围。
4. `_transcribe_chunk()` 也用这个 helper 算回填基准。
5. MLX segments 先换成真实视频时间，再按 `[t_start, t_end]` 裁剪 padding。
6. P274 修改不碰 `_assemble_qwen_primary()`。
7. P273 修改等 P274 真实重跑后再做，避免混淆归因。
8. VAD 和 WhisperJAV 只进入评估，不进入当前 P274/P273 修复 diff。

### 下一步输入

进入 ④ 双签 diff + 简化：
- 主审 `/review`：在修改前先准备预期 diff 风险清单；真正有 diff 后必须再跑一次全项目 review。
- 副审 `elon-musk`：审是否还能更小，是否可以只新增 helper + 过滤函数，而不是引入更大结构。

## ④ 双签 diff + 简化（预审）— 2026-06-15
**执行人**：Claude
**依赖类型**：📖🌐 读全项目
**前置阅读**：
- `review` skill
- `elon-musk` skill
- `git diff -- v2/backend/core/qwen_asr_runner.py v2/backend/transcriber_v2.py pipelines/bug-pipeline-2026-06-15-p273-p274-asr-timeline.md`
- `git status --short`

### 状态说明

当前还没有 P274 的业务代码 diff。工作区里已有一批旧业务改动，主要集中在：
- `v2/backend/transcriber_v2.py`：已有 P273/P268/P254 相关组装、L1、智能路由改动
- `v2/backend/core/qwen_asr_runner.py`：已有进度日志和异常日志改动
- 其他翻译、worker、前端、文档文件也有未提交改动

所以现在不能把 `/review` 当成“P274 代码已审过”。如果现在直接审全量 diff，会把之前 P273/P268 的旧改动和即将做的 P274 混在一起，结论会不干净。

### 主审：/review 预审结论

P274 预期 diff 风险清单：

1. 如果直接改 `_extract_audio_chunk()` 返回值，风险是影响 SenseVoice。`sensevoice_runner.py` 复用了这个 helper，虽然忽略返回值不会立刻报错，但 helper 语义会变复杂。
2. 如果只把 `t_start` 改成 `extract_start`，但不裁剪 padding，风险是相邻 chunk 重复字幕，或者把上一个 chunk 的尾音带到当前 chunk。
3. 如果裁剪时丢掉所有跨边界词，风险是 chunk 边界处少字。更稳的是：完全在 padding 外的词丢掉；跨边界词 clamp 到当前边界。
4. 如果只测 MLX，不测 PyTorch，风险是以后 Windows/PyTorch 路径再次踩 P168。
5. 如果 P274 同时改 `_assemble_qwen_primary()`，风险是无法判断真实重跑改善来自时间回填，还是来自组装规则。

正式 `/review` 阻塞条件：必须等 P274 代码 diff 出来后重跑。到时审查范围应至少包括：
- `v2/backend/core/qwen_asr_runner.py`
- 新增/修改的 P274 单元测试
- 如有必要，SenseVoice 相关测试或调用点确认

### 副审：elon-musk 简化结论

最小方案是：

1. 不引入 VAD。
2. 不引入 StableTS。
3. 不改字幕组装。
4. 不改 SenseVoice。
5. 只在 Qwen ASR chunk 层新增一个边界 helper。
6. 只在 MLX segments 回填处改时间基准。
7. 加一个小函数处理 padding 裁剪。
8. 用测试锁住 5 秒偏移和边界行为。

如果这个还不能解决 21:39/22:00 的整体延迟，再看 P273 和更大架构。不要把火箭发动机装到自行车上。当前 bug 是时间原点错了，先把原点改对。

### ④ 结论（下一步只看这里）

第 ④ 步预审通过，但正式 code review 不能算完成。原因是当前没有 P274 代码 diff，且工作区已有旧业务 diff。

下一步进入第 ⑤ 设计一致性走查前，需要先把 P274 的目标设计图写清楚。设计图必须非常小：

```text
chunk 原始范围 [t_start, t_end]
        ↓
提取范围 [extract_start, extract_end] = [t_start-5, t_end+5]
        ↓
Qwen 返回 raw segment time，时间相对 extract_start
        ↓
绝对时间 = raw time + extract_start
        ↓
裁剪到 [t_start, t_end]
        ↓
下游收到真实视频时间轴上的 Qwen words
```

正式 `/review + elon-musk` 必须在 P274 代码 diff 出来后再跑一次，不能省。

### 下一步输入

进入 ⑤ 设计一致性走查：
- 设计图就是上面这条 6 步小流程。
- 代码入口是 `_transcribe_chunk()`。
- 重点问：有没有多做？有没有少做 padding 裁剪？有没有把 `extract_start` 又丢掉？有没有让数据多经过一只不必要的手？

## ⑤ 设计一致性走查 — 2026-06-15
**执行人**：Claude
**依赖类型**：📖 读指定文件
**设计图**：第 ④ 步中的 P274 六步小流程
**代码入口**：`v2/backend/core/qwen_asr_runner.py:163-211` 的 `_transcribe_chunk()`

### 设计图

```text
1. 输入 chunk 原始范围 [t_start, t_end]
2. 计算真实提取范围 [extract_start, extract_end] = [max(0,t_start-5), t_end+5]
3. ffmpeg 提取 [extract_start, extract_end] 临时音频
4. Qwen 返回 raw segment time，时间相对临时音频开头
5. 绝对时间 = raw time + extract_start
6. 裁剪到 [t_start, t_end]，下游只收到真实视频时间轴上的 Qwen words
```

### 逐步骤对照

| 设计步骤 | 当前代码对应 | 一致？ |
|----------|-------------|--------|
| 1. 输入 `[t_start, t_end]` | `_transcribe_chunk(model, audio_path, t_start, t_end, ...)` | ✅ 一致 |
| 2. 计算 `[extract_start, extract_end]` | `_extract_audio_chunk()` 内部计算 `extract_start = max(0.0, start_s - _AUDIO_PAD_S)` | ⚠️ 半一致。计算存在，但只在 helper 内部，调用方拿不到 |
| 3. 提取临时音频 | `_extract_audio_chunk(audio_path, t_start, t_end, chunk_path)` | ✅ 一致 |
| 4. Qwen 返回 raw segment time | `raw_segments = result.segments or []` | ✅ 一致 |
| 5. 绝对时间 = raw time + extract_start | 当前是 `s["start"] + t_start` 和 `s["end"] + t_start` | ❌ 错位。用了原始 chunk 起点，不是真实提取起点 |
| 6. 裁剪到 `[t_start, t_end]` | 当前直接保留全部 `raw_segments` | ❌ 少了。padding 前后区间没有裁剪 |

### 数据流追踪

| 步骤 | 数据包 | 手（函数） | 读/改/扔 | 设计图上？ |
|------|--------|------------|----------|------------|
| chunk 范围 | `t_start/t_end` | `_transcribe_chunk()` | 读 | ✅ |
| 提取范围 | `extract_start/extract_end` | `_extract_audio_chunk()` | 计算但不返回 | ⚠️ 半在图上。数据出生后没有回到调用方 |
| Qwen 输出 | `raw_segments` | `_mlx_transcribe()` | 生成 raw 时间 | ✅ |
| 时间回填 | `segments` | `_transcribe_chunk()` list comprehension | 改，但用错基准 | ❌ |
| 下游组装 | `qwen_chunks` | `_smart_assemble()` | 读 | ✅ |

### 偏差汇总

| # | 类型 | 描述 | 决定 |
|---|------|------|------|
| 1 | 少了 | `_transcribe_chunk()` 没有拿到或重新计算 `extract_start` | P274 修复 |
| 2 | 错位 | Qwen raw time 加了 `t_start`，应加 `extract_start` | P274 修复 |
| 3 | 少了 | padding 前后区间没有裁剪，可能造成 chunk 交界重复或串段 | P274 修复 |
| 4 | 风险 | `_extract_audio_chunk()` 被 SenseVoice 复用，直接改返回语义会扩大影响面 | 用小 helper 共享边界计算，不改调用语义 |

### ⑤ 结论（下一步只看这里）

设计一致性走查未通过当前代码，但通过了 P274 修复设计。也就是说，现有代码和设计图不一致，P274 正是要补齐这两个缺口：

1. 时间回填基准必须从 `t_start` 改为 `extract_start`。
2. 下游接收前必须裁剪 padding。

修复设计保持最小化：

```text
新增 _audio_extract_bounds()
_extract_audio_chunk() 继续负责 ffmpeg
_transcribe_chunk() 用同一个 bounds 回填时间
新增/内联裁剪函数，过滤 padding 外词
```

### 下一步输入

进入 ⑥ security-review 判断：
- P274 不涉及认证、权限、网络输入、文件上传扩展、API key 或用户敏感数据。
- 但涉及 `ffmpeg` 临时音频路径，需确认不改变现有 `tempfile.mkstemp` 和 `subprocess.run(cmd list)` 的安全边界。

## ⑥ security-review — 2026-06-15
**执行人**：Claude
**依赖类型**：📖 读指定文件
**结论**：按规则跳过正式安全审查。

### 跳过原因

P274 只修 Qwen ASR 内部时间换算，不涉及：
- 登录、认证、权限
- API key
- 用户上传类型扩展
- 新网络请求
- 新命令行拼接
- 敏感数据读写

### 保留的安全边界

P274 实现时必须保持：
- 继续使用 `tempfile.mkstemp()` 创建临时音频路径
- 继续用 `subprocess.run(cmd, capture_output=True, check=True)` 的 list 参数形式调用 ffmpeg
- 不把用户输入拼成 shell 字符串
- 不改变 SenseVoice 对 `_extract_audio_chunk()` 的调用语义

### ⑥ 结论（下一步只看这里）

安全审查可跳过。P274 修复只允许改时间计算、padding 裁剪和相关测试，不允许顺手改 ffmpeg 调用方式。

### 下一步输入

进入 ⑦ test-driven-development：
- 先写失败测试，证明当前代码会把 `extract_start` 错算成 `t_start`。
- 再写修复代码。
- 最后跑 P274 相关测试。

## ⑦ test-driven-development — 2026-06-15
**执行人**：Claude
**依赖类型**：📖 读指定文件
**涉及文件**：
- `v2/backend/core/qwen_asr_runner.py`
- `v2/test/test_phase1c_chunking.py`

### RED：失败测试

新增测试：`test_p274_mlx_chunk_offsets_by_extract_start_and_clips_padding`。

测试模拟：
- 原始 chunk：`1200.0-1500.0`
- padding 后实际提取：`1195.0-1505.0`
- Qwen raw segments：
  - `3.0-4.0 before`：真实时间 `1198-1199`，属于前 padding，应裁掉
  - `10.0-11.0 inside`：真实时间 `1205-1206`，应保留
  - `306.0-307.0 after`：真实时间 `1501-1502`，属于后 padding，应裁掉

第一次运行失败原因正确：
- 当前代码返回了 `1203/1210/1506` 三段
- 期望只返回 `1205-1206 inside`

随后补充更严格断言：
- `chunk["text"]` 也必须是裁剪后的 `"inside"`
- 不能保留 `"before inside after"`，否则 L2 仍可能被 padding 文本污染

### GREEN：最小实现

实现内容：
1. 新增 `_audio_extract_bounds(start_s, end_s)`，统一计算 `extract_start/extract_end`。
2. `_extract_audio_chunk()` 改用 `_audio_extract_bounds()`，保持原函数签名不变，避免影响 SenseVoice。
3. 新增 `_clip_chunk_segments()`：
   - 先用 `raw_time + extract_start` 转成真实视频时间
   - 再裁掉完全落在 padding 外的词
   - 跨边界词 clamp 到 `[t_start, t_end]`
4. 新增 `_join_segment_texts()`：
   - MLX 路径下用裁剪后的 segments 重新生成 `full_text`
   - 防止 padding 文本继续进入 L2 可靠性判断和窗口归类
5. `_transcribe_chunk()` 的 MLX 路径改为：
   - 计算 `extract_start`
   - 调 `_clip_chunk_segments()`
   - 用裁剪后的 segments 重建 `full_text`

### 测试结果

已通过：
- `pytest test/test_phase1c_chunking.py::TestRunQwenASRChunkedIntegration::test_p274_mlx_chunk_offsets_by_extract_start_and_clips_padding -q`
  - 结果：`1 passed`
- `pytest test/test_phase1c_chunking.py::TestRunQwenASRChunkedIntegration test/test_p273_qwen_ja_assembly.py test/test_p268_p269_pipeline.py -q`
  - 结果：`21 passed`
- `pytest test/test_phase1d_qwen_params.py test/test_phase1d_qwen_health.py -q`
  - 结果：`2 passed`

未通过但判定为环境/旧测试阻塞：
- `pytest test/test_phase1c_chunking.py -q`
  - 结果：运行到 SenseVoice 旧测试时导入 `funasr`，Python 进程段错误
- `pytest test/test_arch7_pipeline.py::TestIndependentQwenASR -q`
  - 结果：导入真实 `mlx_qwen3_asr` 时触发 `No Metal device available`

### ⑦ 结论（下一步只看这里）

P274 已按 TDD 完成代码层修复。相关窄测试通过，但还不能宣布真实视频问题已解决，因为尚未重跑 FJIN-141。

重要边界：
- 没有改 `_assemble_qwen_primary()`，P273 仍未修。
- 没有接入 VAD。
- 没有接入 StableTS 或 WhisperJAV 式架构。
- `v2/test/test_phase1c_chunking.py` 被 `.gitignore:20 test/` 忽略；测试文件存在并已执行，但不会自然出现在 git diff 里。后续提交前需要决定是否强制纳入或调整测试目录跟踪规则。

## ⑧ verification-before-completion — 2026-06-15
**执行人**：Claude
**依赖类型**：🏃 跑应用

### 验证命令与结果

通过：
```bash
cd /Users/andy/Andy-agent/video-subtitle/v2
.venv/bin/python -m pytest test/test_phase1c_chunking.py::TestRunQwenASRChunkedIntegration test/test_p273_qwen_ja_assembly.py test/test_p268_p269_pipeline.py -q
```

结果：`21 passed in 3.86s`

通过：
```bash
cd /Users/andy/Andy-agent/video-subtitle/v2
.venv/bin/python -m pytest test/test_phase1d_qwen_params.py test/test_phase1d_qwen_health.py -q
```

结果：`2 passed in 0.02s`

阻塞：
```bash
cd /Users/andy/Andy-agent/video-subtitle/v2
.venv/bin/python -m pytest test/test_arch7_pipeline.py::TestIndependentQwenASR -q
```

结果：`3 failed`，失败原因均为导入真实 `mlx_qwen3_asr` 时触发 `RuntimeError: No Metal device available`。

### ⑧ 结论

P274 相关代码路径的窄验证通过。全量验证不能给绿灯，原因是当前环境存在已知的 Metal/MLX 导入阻塞和 SenseVoice/funasr 段错误风险。

下一步必须做真实端到端验证：
- 重跑 FJIN-141
- 重点复查 21:39、22:00、49:37、50:00
- 统计 `<0.2s` 字幕数量、`>3s` 空洞数量、最大空洞
- 如果 5 秒整体滞后消失，再进入 P273 组装质量修复

## ⑩ 端到端实测结果分析 — 2026-06-15
**执行人**：Claude
**依赖类型**：🏃 跑应用
**说明**：真实重跑由用户在本机完成，本步骤读取重跑后的日志、SRT、transcript 和 translated 产物做结果判定。⑨ 浏览器 QA 未执行；当前只记录 ASR 分支真实视频端到端结果。

### 读了什么
- `/Users/andy/.video-subber/jobs/e5494b352d/meta.json`
- `/Users/andy/.video-subber/jobs/e5494b352d/FJIN-141.srt`
- `/Users/andy/.video-subber/jobs/e5494b352d/cache/c2e90d73d46a/transcript.json`
- `/Users/andy/.video-subber/jobs/e5494b352d/cache/c2e90d73d46a/translated.json`
- `/Users/andy/.video-subber/logs/e5494b352d/user.log`
- `/Users/andy/.video-subber/logs/e5494b352d/debug.log`
- 对照：`65b1f1b3db`、`723b866ccb` 的 SRT 和日志

### 指标对比

| 指标 | Run 3 `65b1f1b3db` | Run 4 `723b866ccb` | Run 5 `e5494b352d` |
| --- | ---: | ---: | ---: |
| SRT 字幕条数 | 1157 | 1141 | 1099 |
| `<0.2s` 瞬闪字幕 | 173 | 179 | 188 |
| `<0.5s` 短字幕 | 383 | 375 | 380 |
| `>3s` 空洞 | 433 | 435 | 417 |
| `>10s` 空洞 | 161 | 161 | 151 |
| 最大空洞 | 67.733s | 67.733s | 126.920s |
| 问号/不确定标记 | 997 | 945 | 846 |
| 审查解释污染进入 SRT | 242 行 | 0 行 | 0 行 |

### 关键时间点
- 21:39 附近：Run 4 的 `1297.268-1297.347` 到 Run 5 变为 `1292.268-1292.347`，提前 5 秒。
- 22:00 附近：Run 4 的 `1306.867-1310.468` 到 Run 5 变为 `1301.867-1305.468`，提前 5 秒。
- 21:39 周围仍有 9 秒级空洞，说明 P274 已修 5 秒系统性偏移，但 P273 的空洞问题还在。
- 最新最大空洞在 `2187.702-2325.101`，中间空 126.920 秒。

### 日志对比
- `L3 不可能时长`：Run 4 为 143，Run 5 为 13。
- `L3 语速异常`：Run 4 为 157，Run 5 为 83。
- `L4 CJK重复保留`：Run 4 为 142，Run 5 为 29。
- `L1 SV误判清除`：Run 4 为 498，Run 5 为 459。
- `L5 去重`：Run 4 为 38，Run 5 为 58。

### ⑩ 结论

P274 通过真实重跑验证：长音频 chunk padding 导致的 5 秒系统性后移已经修掉。

P273 未通过真实重跑验证：最新仍有 188 条 `<0.2s` 字幕、417 个 `>3s` 空洞、最大空洞 126.920 秒。下一步必须继续修组装层，而不是再纠缠 P274。

P272 未根治：最新成品 SRT 没有审查解释污染，但 debug 日志仍出现“字幕质量审查员/版本A/版本B/选A”。这说明解释文本还会从模型返回，只是这次没有写入最终字幕。
