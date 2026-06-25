# V3.07 已发布 + 后续版本计划

**版本**：V3.07 ✅ 已发布
**日期**：2026-06-21
**Git**：tag `v3.07`（commit `5302d9d`），只读快照 `v2/v3.07/`
**测试**：614 passed, 1 skipped, 0 failed
**状态**：V3.07 已发布。收尾线 7 步 + 发布线 5 步全部通过。收尾审查修复 P294/P295/代理 URL 安全脱敏。8 项 P1/P2 遗留归入 V3.08。P273 残留接受，不阻塞发布

**补充记录（2026-06-18）**：已新增 `competitor-comparison.md`，完成外部项目对比与 JAV 场景借鉴整理。当前结论：先做人声地图、短词并段、低分段返修，不新增本地 ASR，不立刻走模型微调。

**补充记录（2026-06-18 晚）**：P283 后重新跑 FJIN-141，job `23e2621360`。`[?]` 从上一轮 `129/922` 降到 `52/931`；P275 日志显示 `无字幕人声区=0`、`最长疑似漏字幕=None`；49:20-50:20 老问题窗口不再有 5 秒系统性后移和大面积 `[?]`。剩余问题主要是极短字幕、短促叫声、重复声和正常无对白间隔，作为成人片素材残留接受，不阻塞 V3.07 收尾。

**补充记录（2026-06-18 夜）**：P266 Mac 端 Whisper 换 `mlx-whisper` 已完成代码适配和模型下载。新增 `backend/core/whisper_runner.py` 做平台分流：Mac `Darwin arm64` 使用 `mlx-whisper`，Windows/其他平台继续使用 `faster-whisper`。`mlx-community/whisper-large-v3-mlx` 已下载到 Hugging Face 本地缓存，权重大小 `3083520416 bytes`。相关测试 `82 passed`。真实 MLX 转录仍需脱离 Codex/headless 沙盒验证。

**补充记录（2026-06-19）**：Whisper 换库分支真实跑 London VLOG 暴露 P284：Qwen 主引擎组装结果没有挂回 Whisper 参考稿，导致 P247/P263+P264 的双稿裁判没有启动；`clock/claw` 旧问题复发，且第一句因 Qwen 停顿切句把 `Oh gosh I don't` 合入前一句。已按 bug 修复流水线完成代码修复：英语 Qwen 主路径改用 Whisper 边界并回填 `whisper_text`，短字幕合并时同步保留参考稿；相关回归 `80 passed`，全量测试受 Codex/headless Metal 限制仍需普通 Mac 前台真实转录验收。
**补充记录（2026-06-19 晚）**：London VLOG 重跑前缓存清理规则已固定：以后用户说“清缓存”时，默认保留 `user.log`、`debug.log` 和 `.srt` 对比材料，删除 job 目录其余文件，并通过清掉 `meta.json` + 完成记录让前端刷新后不再恢复旧已完成卡片。本轮同时修复了后端 stale done 自愈：`get_job()` 发现 `meta.json` 已不存在且进程不活时，会自动清掉内存里的旧任务状态并返回 404。
**补充记录（2026-06-19 深夜）**：后台任务监控显示当前唯一 `running` 任务是 `48b739a158`（`MIDA-659.ts`）。日志停在 `21:41:38 加载 Whisper large-v3 模型…`，连续轮询 15 秒无任何推进；`checkpoint.json` 仍停在 `extract_audio`，说明这条任务目前更像“状态没收回来或启动后卡死”，不是稳定运行中。另有 3 个 URL 任务（`2049795afe`、`05cbec0727`、`01bdd52151`）已确认只是测试链接 `https://www.youtube.com/watch?v=abc` 被 `yt-dlp` 判定为截断，不属于主转录链路回归。
**补充记录（2026-06-19 深夜 P285）**：重新测试后新任务 `a58b786e34` 复现同一问题。旧任务 `48b739a158` 未正常完成，目录只剩 `config.json`；新任务为 `standard` 标准模式，但 worker `59930` 在 `加载 Whisper large-v3 模型…` 后持续高 CPU/高内存，未进入 Qwen。根因已登记为 P285：标准模式本应只让 Whisper 跑第一段做语言判断，当前代码却把 8191 秒完整音频传给 `mlx_whisper.transcribe(..., word_timestamps=True)`，导致前端长时间停在“正在启动语音识别…”。已按 bug 修复流水线完成代码修复：Mac/MLX 标准模式现在先抽短音频做语言探测，适合 Qwen 时不再完整调用 Whisper；目标测试 `25 passed`，待普通 Mac 前台环境用 `MIDA-659.ts` 做真实标准模式验收。

---

## 已完成 ✅

- [x] Phase 1A-1D：翻译管线、多模型转录、长音频切分、Qwen3-FA 对齐
- [x] P156：千问参数校准（1.1+20t/s）
- [x] P160 Phase 1：新 L2 架构（窗口+词干+双重确认），端到端一致率 89%
- [x] P157：翻译内容闸（DeepSeek 返回俄语→自动降级免费引擎）
- [x] P158：字幕默认字号 20→42px
- [x] P147/P148/P154/P155：千问防坍缩、L2 裁判健康检查、砍掉 API Key 加密、Git 版本管理修复
- [x] P150：condition_on_previous_text=True 确认保持（用户主动选，换准确率，L2 安全网在线）
- [x] env_check：SenseVoice + Qwen3-ASR 检测项已做
- [x] V3.06 迭代：263→273 测试全过，git commit `415b89f` + 只读快照 `v2/v3.06/`

---

## V3.07 — 后端精度收尾 + 换库 + 功能收尾（最后一个后端版本）

> **在 v2/ 路径下修完所有 bug，修完后再搬家。**

### 已完成（HARDEN Sprint 2 · 5 个 Commit · 459 测试 0 失败）

**第一批 [BUG] 止血**：⑤P161 → ⑪P166 → ①P160 → ⑫P167 → ⑥P162
**第二批 [FEATURE] 调优**：②P149（含 P171 分母评估） → ③英语词干评估 → ⑩P165 → ⑬P174 链接直传
**第三批 [ARCH] 架构**：⑦P152+P144（含 P168 时间戳偏移 + P169 缺参数） → ⑧LLM合并✅ → ⑨动态输入✅
**第四批 HARDEN 收尾**（原第四+第五合并）：P170 + P172 + P178 + P175/P176/P177 文档同步 + ④端到端验证

| 状态 | 事项 | 编号 | 说明 |
|------|------|------|------|
| ✅ | P160 第二步 | P160 | `_match_qwen_to_whisper` SequenceMatcher → LightweightStemmer 词干匹配。TDD 通过 |
| ❌ | 门槛校准 | P149 | **❌ 已关闭（换库后精确时间归窗，门槛默认值够用）**。原诊断日志已加（逐窗口 w/q/shared/score），MIN_STEMS=5 门槛已加（P171）。换 MLX ASR 库后千问自带词级时间戳，L2 归窗从相似度猜窗口变为精确时间归窗，数据喂对了，词干重叠率自然稳定，不再需要逐语言人工校准 |
| 🔧 | 英语词干评估 | — | 已补 9 个后缀 (-able/-ible/-ful/-ous/-ive/-ize/-ise/-ity/-al)，待真实视频验证覆盖率 |
| ⬜ | 端到端验证 | — | 中/日/英/俄各跑一条完整视频，确认窗口统计合理 |
| ✅ | L2 双维度交叉验证 | P161 | 4 子任务全部完成：①删 trim 调用 ②删 L1 fallback ③并行搭档 SV+FA ④FA 解锁标准档。TDD 8 测试全过 |
| ✅ | P162 prompt 目标语言 | P162 | `_TARGET_LANGUAGE = "简体中文"` 嵌入 prompt |
| ✅ | 转录流水线重构 | P152+P144+P168+P169+P173 | 生产者-消费者流水线（Whisper 边转录边喂千ASR）+ P144 FA对齐先于SV贴标 + P168 extract_start偏移修复 + P169 return_time_stamps统一 + P173 提取_load_qwen_model()。347 测试全过 |
| ✅ | LLM 合并（术语+摘要） | ARCH ⑧ | _extract_terminology_and_summary() 一次调 LLM，Markdown 标题分隔，缺失回退。16 新增测试 |
| ✅ | 翻译上下文动态输入 | ARCH ⑨ | `_compute_chunk_size(llm)` 从 LiteLLM 缓存读模型 context window 动态算分块大小。14 新增测试 |
| ✅ | 内容闸前移 + LLM 烟雾测试 | P165 | P1 摘要 CJK<10% → 跳过该 LLM，降级链继续 |
| ✅ | L4 条件硬过滤 + 顺序调整 | P166 | 顺序 L1→L2→L4→L5→L3。L2 标注 l2_status，L4 关键词规则对 GREEN 跳过。TDD 6 测试全过 |
| ✅ | SenseVoice 标签改重叠判定 | P167 | 包含→overlap 匹配（≥50% 重叠率）。TDD 5 测试全过 |
| ✅ | P168+P169+P173 归入 P152+P144 | — | 已在 ARCH ⑦ 中修复 |
| ✅ | run_qwen_asr 空列表防护 | P170 | `if not segments: return segments` 加在 segments[-1].end 之前。_chunk_segments() 已有同名防护，现覆盖全部入口 |
| ✅ | L2 分母策略修复 | P171 | MIN_STEMS=5 + 分母从 `min` 改为 `max`（Steve Jobs 审查）。Qwen 6字/Whisper 20字 → 旧:6/6=100%假绿灯 → 新:6/20=30%正确暴露缺失。385 测试全过 |
| ✅ | L4 重复词门槛降为 2 次 | P172 | 正则从 `^((\S+)\s+)\2{2,}\2*$` 改为 `^(\S+)(\s+\1)+$`——不仅降门槛，同时修复了原结构对有空格分隔的 3+ 词从未匹配的 bug |
| ✅ | YouTube/B站 链接直传（后端） | P174 | main.py URL 模式 + worker.py 透明下载 + 进度推送。前端 URL 输入框留给 V3.09 |
| ✅ | L2 Pass 1 删除 sv_tag+qwen_text 跨层交叉验证 | P193 | **2026-06-08 诊断审计确认已修**：当前代码中 L2 Pass 1 不存在，`_layer2_cross_validate` 仅做窗口词干比对 |
| ⏳ | L2 接入 Whisper 逐字时间戳 | P179 | **延后 V3.08（新功能）**。Whisper `.words` + SenseVoice 人声区间 → L2 时间有效性维度。加维度要调参数，影响其他层平衡 |
| ✅ | P182 尾部 chunk 丢失 | P182 | Producer-consumer finally 块只发 sentinel 不 flush 剩余 segments → Qwen 交叉验证跳过。TDD: test_p182_tail_flush.py |
| ✅ | P183 L1 单信号 fallback | P183 | FA=None 时 non-Speech 不过滤。修复: FA 不可用 Trust SV alone。TDD: test_p183_l1_fallback.py |
| ✅ | P185 死 import 清理 | P185 | transcriber_v2.py 删除未使用 `_chunk_segments` |
| ✅ | P188 URL 协议校验 | P188 | P174 链接直传接受 file:///ftp:///javascript:。修复: 仅允许 http/https。TDD: test_p188_url_validation.py |
| 🔧 | Whisper 模型释放时机确认 | P175 | ARCH ⑦ plan-eng-review 发现。审查确认当前 del model 位置安全，流水线模式不需改动 |
| ✅ | Producer-Consumer 同步机制 | P176 | ARCH ⑦ plan-eng-review 发现。queue.Queue + threading.Event 已在 ARCH ⑦ 流水线重构中实现 |
| ✅ | 消费者动态分批逻辑 | P177 | ARCH ⑦ plan-eng-review 发现。300s 累计时长触发 + done Event 收尾，已在 ARCH ⑦ 中实现 |
| ✅ | _CHUNK_SIZE / _OVERLAP_SIZE 死代码清理 | P178 | 删除两行死代码，保留 _OVERLAP_RATIO=0.2 仍被 _translate_whole_with_markers 使用 |

### HARDEN 版本收尾 (2026-06-08) 新增发现

| 状态 | 事项 | 编号 | 说明 |
|------|------|------|------|
| ✅ | localStorage 剥离 api_key + 异步竞态导致 API Key 反复丢失 | P197 | 三步减法：loadConfig 删 apiKeys 后再 Object.assign + configReady 防竞态。2026-06-09 TDD 修复 |
| ✅ | worker 非零退出误导性 OOM 错误消息 | P194 | **2026-06-08 诊断审计确认已修**：`worker_manager.py:94-96` 已改为退出码 + 日志引导 |
| ✅ | orchestrator silent exception swallowing | P195 | **2026-06-08 诊断审计确认已修**：`orchestrator.py:90-93` 两处 except 已有 `_log.warning` |
| ✅ | 模块 docstring stale order | — | **2026-06-08 诊断审计确认已修**：hallucination_filter.py:3 已写 `L1 → L2 → L4 → L5 → L3`，与代码一致 |
| ✅ | yt-dlp 分轨下载重构 | P198 | video-only + audio-only 分别下载，音频直送转录，最终 video+audio+字幕 合并。2026-06-09 TDD 修复 |
| ✅ | standard 模式 SV+FA 并行 | P199 | should_parallel_svfa() 独立判定，SV+FA 线程启动与 Qwen pipeline 解耦。2026-06-09 TDD 修复 |
| ✅ | precise 模式 Qwen join 不再阻塞 SV | P200 | SV+FA 线程启动移到 qwen_thread.join() 之前。2026-06-09 TDD 修复 |
| ✅ | FA 模型检测 + env_check 补漏 | P201 | is_qwen_aligner_available() 用 AutoConfig local_files_only 检查，env_check 新增 check_qwen_aligner。2026-06-09 TDD 修复（注：仅检测，模型下载归 P208） |
| 🔴 | FA 手写对齐器造假数据 — 换官方 API | P207 | **🔴 blocking**。当前 `align()` 三条造假路径：① `argmax(logits)` 硬选最大值丢置信度 ② 索引不匹配→静默均匀排列 0.5s/词 ③ 推理崩溃→按音频时长均匀分配。三条都返回正常格式，调用方无法区分真伪。L1 双信号交叉验证 + L2 窗口词干比对全吃假数据。修复：换官方 `Qwen3ForcedAligner.from_pretrained()` + `model.align()`，删手写 100 行。同时 ① pyproject.toml 加 qwen-asr==0.0.6 ③ 版本号全锁定 ④ tokenizers→0.23.0 |
| ✅ | FA 手写对齐器造假数据 — 换官方 API | P207 | 2026-06-09 TDD 修复完成。279→200 行，8 新增测试，419 全量零回归。Jobs 审查发现 3 个遗留问题 → P212/P213/P214 |

### Jobs 审查遗留（2026-06-09）

| 状态 | 事项 | 编号 | 说明 |
|------|------|------|------|
| 🧑 | FA 对齐精度无真实语音验证 | P212 | **🧑 依赖人工，延后 V3.08 和心机音频合并**。反造假测试用正弦波证明不再造假但未证明准确。需录真人声+标注词边界 |
| ✅ | apply_aligner_timestamps 词数漂移 | P213 | 改为时间范围匹配 + 未匹配段 WARNING 日志。2026-06-10 TDD 修复 |
| ✅ | FA 对齐失败时静默降级 | P214 | `_fa_quality` 标记 + 降级 WARNING 日志。2026-06-10 TDD 修复 |
| ✅ | FA 对齐器长音频 O(n²) 内存爆炸 | P220 | 22 分钟音频→73,000 token→注意力矩阵 53 亿元素→需 18.82 GiB。新增 `align_segments()` 按 Whisper 句子分段对齐，402 段逐句跑。421 测试零回归 |
| ✅ | Qwen ASR MLX 版不认识的参数 | P221 | 4 个调用点改为条件展开 `**({"return_time_stamps": True} if not _USE_MLX else {})`，MLX 时不传参数。421 测试零回归 |
| ✅ | Qwen ASR v0.0.6 语言名必须传完整名 | P222 | 新增 `_resolve_language()` + `_LANG_NAME` 映射表，4 调用点改为 `language=_resolve_language(language)`。421 测试零回归 |
| ✅ | FA 分段对齐时间戳偏移丢失 | P224 | `align_segments()` 中对齐结果每个词的 start/end 加上 `seg.start` 偏移。421 测试零回归 |
| ✅ | 错误提示 memory 匹配精确化 | P202 | _friendly_error 改为精确关键词匹配。2026-06-09 TDD 修复 |
| ✅ | 文件归档加时间戳 | P203 | _archive_path() 加 _%H%M%S 时间戳后缀。2026-06-09 TDD 修复 |

**规则**：只改后端。不改前端。不改框架。全部在 v2/ 目录下完成。修完一组 commit 一次。

---

### V3.07 新增 — 换库 + 功能收尾（2026-06-11 从 V3.08 移入）

| 编号 | 事项 | 严重度 | 说明 |
|------|------|--------|------|
| 🔧 | 换 MLX ASR 库 | — | gabrimatic→moona3k。方案已定（`MLX-ASR-换库方案.md`），待开分支。换完后 P179/P212/P149/P171/P242 自然关闭，P251 归窗精确化 |
| P254 | 转录智能路由 | 🟡 HIGH | 基于源语言自动切换主副引擎。英语/日/韩→Qwen 主+Whisper 副，冷门→Whisper 主。换库后 Qwen 可独立检测语言+分块+出时间戳 |
| P247 | ASR 分歧裁决 | ✅ 已修 | Plan C 单段隔离安全阀 + P263/P264 双稿裁判重构。FJIN-141 实测 843→0 [?] |
| P237 | 黑匣子日志 | 🔴 P0 | 系统日志只有流水账无状态快照。加 `checkpoint()` + `psutil` 定期拍内存/进程/线程/队列快照 |
| P284 | Whisper 参考稿丢失导致 `clock/claw` 回归 | ✅ 已修并验收 | 英语 Qwen 主路径改用 Whisper 边界并回填 `whisper_text`；短字幕合并保留参考稿。2026-06-21 清缓存重跑 London VLOG 验收：clock/claw 正常，Hey Taylor 断句正确 |

**换库后的两档模式（2026-06-12 最终定稿）**：

| 档位 | 加载模型 | 并行？ | 峰值内存 | 最低要求 | 适合场景 |
|------|---------|--------|---------|---------|---------|
| ⚖️ 标准 | Qwen ASR → SenseVoice | 串行（Qwen 先跑→释放→SV） | ~7.5GB | **8GB** | 日常使用，中/英文最优 |
| 🎯 精确 | Qwen + Whisper 并行 → SenseVoice | Qwen 和 Whisper 同时跑 | ~10.4GB | **16GB** | 最高质量，双引擎交叉验证 |

> **内存指的是系统 RAM（Mac 是统一内存，Windows 是系统内存）。** 两个模型吃内存最多：Qwen ~7GB、Whisper ~3GB。精确档同时加载两者→~10.4GB 峰值。SenseVoice（~1.8GB）在转录完成后单独跑，不叠加。翻译调云端 API，本地不占模型内存。
>
> **用户提示**：选档时检测可用内存。不够时弹提醒：「⚠️ 精确模式需要 16GB 内存，当前可用 8GB。建议切换到标准模式。」
>
> 旧三档（快速/标准/精确）废弃。旧标准=Whisper only、旧精确=四模型全上，均被新架构替代。
>
> **语言检测方案（2026-06-16 修正）**：标准模式先跑 Whisper 第一段拿语言判断。若识别出 `ja/zh/en/ko` 且置信度足够，再停止 Whisper、切到 Qwen 跑全片；若是 Qwen 不擅长的语言，或置信度不够，就继续用 Whisper 跑标准模式。这样不会把 `lang=auto` 直接交给 Qwen 长片自猜。

**待做（2026-06-17 核实更新）**：

### 已完成 ✅（代码已修，含已提交和未提交）

| 状态 | 事项 | 说明 |
|------|------|------|
| ✅ | P275 L1 全片人声/非人声时间轴（Phase 1） | 2026-06-17 真实重跑验证完成。`_build_voice_timeline` 生成 voice_regions/non_voice_regions，并输出 `P275 人声地图` 摘要 |
| ✅ | 标准/精确模式改造 | 2026-06-16 完成。砍掉 fast，标准=单主引擎+SenseVoice，精确=双引擎+SenseVoice。前端改为两按钮。545 测试全绿 |
| ✅ | P276 标准模式模型路由 | 2026-06-17 真实重跑验证通过：Whisper 先判 `ja`，标准模式切 Qwen 并锁定 `ja` |
| ✅ | P277 启动环境日志 | 2026-06-17 真实重跑验证通过：`Worker Python` 指向 `v2/.venv/bin/python`。V3.07 继续补强固定启动方式，不做目录大整理 |
| ✅ | P278 SenseVoice 日志 | 2026-06-17 真实重跑验证通过：SenseVoice 加载、分段、标签分布和 P275 人声地图均有日志 |
| ✅ | P279 标准模式 L2 跳过 | 2026-06-17 真实重跑验证通过：日志明确 `L2 跳过: 单主引擎模式无第二份参考稿` |
| ✅ | P280 已并入 P277/P278 | 不单独修 |
| ✅ | P281 单字字幕止血（Phase 1） | `merge_short_subtitle_fragments()` 已实现：只合并前后 `<=1s` 的短词。6/17 真实重跑显示超短段下降，但单字段仍有 92 个，需 Phase 2 接入人声地图 |
| ✅ | P256 L2 智能窗口边界 | 2026-06-12 Mac 端完成 |
| ✅ | P254 智能路由 | 2026-06-16 完成 |
| ✅ | P247 ASR 分歧裁决 | Plan C 单段隔离安全阀 + P263/P264 双稿裁判重构完成 |
| ✅ | P265 Qwen 重组碎片化 | 2026-06-13 完成 |
| ✅ | P268 L1 承载段错误 | 2026-06-14 完成 |
| ✅ | P269 Refiner 单稿疏通 | 2026-06-14 完成 |
| ✅ | P270 L4 CJK 重复短句 | 2026-06-14 完成 |
| ✅ | P274 Qwen 切片 padding 回填 | 2026-06-15 完成，真实重跑验证 5 秒偏移已修 |

### 已修并通过 FJIN-141 真实视频验收（5 个 + 1 个验收补强）

| 状态 | 事项 | 说明 |
|------|------|------|
| ✅ | P272 翻译接缝审查污染字幕 | 已加 `_review_seam()` 输出防线，拒绝”版本A/版本B/建议/原因”等解释文字写回字幕；新增回归测试 |
| ✅ | P237 黑匣子日志闭环 | 已在异常路径写入安全 `crash_snapshot`，不记录 API Key、环境变量、字幕全文；新增回归测试 |
| ✅ | P281 Phase 2 人声地图接入字幕重组 | `merge_short_subtitle_fragments()` 已接入 `voice_timeline`；同一人声区内更宽松合并，非人声区孤立短词只保留内部原因，不再直接显示 `[?]` |
| ✅ | P275 Phase 2 人声地图接入质量判断 | 已新增 `P275 人声地图质量` 日志：人声区、无字幕人声区、非人声短词、最长疑似漏字幕区间 |
| ✅ | P277 固定运行环境验收补强 | 已新增 `v2/run-test.sh`：固定 `v2/.venv` 且不带 `--reload`；V3.08 再做环境大整理 |
| ✅ | P283 问号噪音修复 | 已把内部质量信号和用户可见问号分开：P281 非人声短词、L3 CJK 短碎片不再默认显示 `[?]` |

### 已修并通过 2026-06-21 真实视频验收

| 状态 | 事项 | 说明 |
|------|------|------|
| ✅ | P284 Whisper 参考稿丢失（clock/claw） | 清缓存重跑 London VLOG：clock/claw 正确，Hey Taylor 断句正常 |
| ✅ | P285 标准模式整条音频传给 Whisper | job `16a692d74e`：Whisper 4秒探语言后释放，Qwen 接手，全程不再卡住 |
| ✅ | P287 Qwen 主引擎 whisper_text 未回填 | job `98ac3271c2`：精准模式 786/1022 段带 whisper_text |
| ✅ | P289 L2 交叉验证左右输入未互换 | job `98ac3271c2`：[?]率从 95.2% 降到 38.4%，剩余均为真实重复叫声 |
| ✅ | P291 Refiner 整段一次性发 LLM | job `98ac3271c2`：9窗口共 238 处修正，不再 corrections=0 |
| ✅ | P266 Mac 端 Whisper 换 mlx-whisper | 标准模式短探测 MLX 正常（4秒，日语概率0.92）|

### 接受残留（1 个）

| 状态 | 事项 | 说明 |
|------|------|------|
| ⏸ | P273 短字幕 / JAV 干扰项残留 | P283 后真实重跑 `23e2621360`：`[?]` 降到 52/931，P275 显示无字幕人声区为 0。仍有极短字幕、短促叫声、重复声和长 SRT 间隔，但不再作为 V3.07 阻塞项继续修 |

### 提交 / 合并

| 状态 | 事项 | 说明 |
|------|------|------|
| ✅ | Whisper 换库分支提交 | commit `a1c1758`，提交时排除了 `.hex-skills/` 和 `v2/v2/` |
| ✅ | Whisper 换库分支合并主线 | merge commit `c9824bb`，使用 `--no-ff` 在 main 上留下明确合并提交 |
| ✅ | P266 Mac 端 Whisper 换 `mlx-whisper` | **2026-06-21 真实验收通过**。标准模式短探测 MLX 正常工作（4秒，日语概率0.92）。Mac Apple Silicon 走 `mlx-whisper`；Windows/其他平台继续走 `faster-whisper` |

### 后续版本（不在 V3.07 范围内）

| 状态 | 事项 | 归属 |
|------|------|------|
| ⬜ | P271+P282 目录清理+环境整理 | V3.08 搬家时统一处理。V3.07 不删除/归档旧 `.venv` 和历史目录，只固定当前运行入口 |
| ⬜ | P255 Windows 端千问时间戳启用 | 需 Windows 真机测试 |
| ⏸ | FJIN 重跑后观察项 | L3 策略、翻译 prompt 场景化、对白价值判断层继续暂缓；其中 `P259` 不再单列独立待办，只在真实使用中被动观察 |
| ✅ | 真实长视频验收 | FJIN-141 job `23e2621360` 已验证：P272/P275/P281/P283 方向生效，残留接受 |

### 已关闭

| 编号 | 原问题 | 关闭原因 |
|------|--------|---------|
| P223 | FA 分段对齐并行化 | ❌ 关闭。核心问题（O(n²) 内存爆炸）已被 P220 `align_segments()` 逐句对齐解决，并行加速属锦上添花 |

---

## V3.08 🆕 — 目录整理 + 复杂度拆分 + 启动健康检查

> **换库 + P254/P247/P237 已移到 V3.07。** V3.08 专注三件事：搬家、拆函数、健康检查。不写新功能。

### HARDEN 延后项（已全部处理）

| 编号 | 严重度 | 类型 | 问题 | 处置 |
|------|--------|------|------|------|
| P242 | ❌ 已关闭 | bug | MLX 后端崩溃 | 换库后死代码随旧库删除 |
| P245 | ❌ 已关闭 | bug | GLOBAL_HASH_INDEX 无锁 | bug 流水线判假 bug，不修 |
| P247 | ⬜ → V3.07 | 新功能 | ASR 分歧裁决 | 已移到 V3.07 |
| P254 | ⬜ → V3.07 | 新功能 | 转录智能路由 | 已移到 V3.07 |



> **三件事：① 搬家 ② 拆分高复杂度函数 ③ 启动健康检查（决策二·动态防线）。不写新功能。**
>
> 搬家先做，复杂度拆在后。搬家坏了好回滚。重构在干净目录上做。

### 第一部分：搬家 + 清理（P132 + P271 + P282 合并）

| 状态 | 事项 | 说明 |
|------|------|------|
| ✅ | v2/ → v3/ 重命名 | 2026-06-21 完成 |
| ✅ | 快照移出 | v2.10~v3.07 全部移到 `snapshots/` 目录 |
| ✅ | P271+P282 清理旧目录和环境 | 已清理：根目录 .venv(1.7G)、v3.05/.venv(1.2G)、.git.*、.hex-skills×2、egg-info×2、v2/v2 嵌套、快照 test 目录。垃圾暂存 `_trash/`(2.9G) |
| ✅ | 路径引用更新 | CLAUDE.md、.gitignore、run-test.sh 已更新。代码内部无硬编码 v2/ 路径 |
| ✅ | 测试确认零退化 | 614 passed, 1 skipped, 0 failed（与 V3.07 发布时一致） |
| ✅ | Git commit | `9a016be` "V3.08: 目录整理 v2/→v3/ + 清理 2.9G 冗余文件" |

**为什么拆出来**：搬家有风险。不和 V3.07 的 bug 修混在一起。V3.07 全部修完 commit 之后再搬家。搬家坏了——回滚 V3.08 即可，V3.07 不受影响。

### 第二部分：复杂度拆分（P219）

> 纯重构——不改行为、不增删功能、不换算法。每个函数拆之前跑测试确认基线，拆完跑测试确认零退化。

#### 🔒 本轮开发线 DECIDE 锁定（2026-06-21 · Feature Freeze 生效）

> 乔布斯减法：15 件事一轮做完是许愿池。本轮**只杀一条龙**——拆 transcribe()，顺手擦同文件脏代码。其余全部 No。
>
> 测试基线（拆前）：**614 passed, 1 skipped, 0 failed**（97s，真机复现成功）

| 范围 | 内容 |
|------|------|
| ✅ **本轮主任务（已完成）** | 拆 `transcribe()` **41→10**，抽出 16 个职责 helper（全部 ≤10）。纯搬运、零行为变更、**615 测试全绿**（基线 614 passed + 1 个原静默跳过的测试改为真断言通过） |
| ✅ 顺手擦（实际只 1 项安全） | 删空壳 `_assemble_qwen_on_whisper_boundaries`（内联其直通目标）。另两项剥离：`__import__('time')` 在别的文件、`_is_repetition_pattern` 两套是不同算法（合并=改行为，非纯重构） |
| ✅ 配套测试更新（用户批准 Option A） | 6 个「源码文本检查测试」改为检查模块/对应 helper（意图不变，行为可证明未变）：`_fa_quality`×2、`release_whisper_model`、probe 顺序、SV/Qwen 顺序×2 |
| 📝 本轮发现登记 | P296（Phase C 闭包 late-binding 竞态，刻意保留）、P297（.gitignore `test/` 把整个测试套件排除出版本控制） |
| ⏸ 本轮推迟 | 另外 5 个函数（sensevoice/layer2/qwen_asr/orchestrator/build_ass） |
| ⏸ 本轮推迟 | 间隔合并提取、RefinerWindow→namedtuple、确认对话框、更新设计图、`__import__('time')`、统一 `_is_repetition_pattern`（需先判是否会改行为） |
| 🚫 剥离出本轮 | **第三部分启动健康检查（FEATURE 不与纯重构同版本）** |

**拆出的 16 个 helper**（按职责）：`_vad_speech_gate`、`_whisper_words_to_dicts`、`_whisper_output_to_segments`、`_collect_qwen_chunks`、`_resolve_routing`(+`_Routing`)、`_should_parallel_qwen`、`_run_standard_language_router`、`_run_full_whisper`、`_start_sv_thread`、`_start_fa_thread`、`_dispatch_sv_fa`、`_await_sv_fa_threads`、`_serial_fa_align_and_apply`、`_apply_fa_timestamps`、`_annotate_fa_quality`、`_run_sensevoice_and_fa`、`_infer_standard_language`、`_make_voice_timeline`、`_run_l1_dual_signal`、`_assemble_and_merge`、`_apply_five_layer_filter`、`_finalize_segments`

**铁律执行情况**：拆 = 搬，逐字搬零逻辑变更。唯一"改测试"是 6 个源码文本检查测试（检查代码结构非行为），经用户批准（Option A）后更新以跟随重构。

| # | 文件 | 函数 | 复杂度 | 状态 |
|---|------|------|--------|------|
| 1 | [transcriber_v2.py](v3/backend/transcriber_v2.py) | `transcribe` | 41→**10** ✅ | ✅ 2026-06-21 拆分完成，615 测试零退化 |
| 2 | [sensevoice_runner.py](v3/backend/core/sensevoice_runner.py) | `run_sensevoice` | 22 | ⬜ 推迟下轮 |
| 3 | [hallucination_filter.py](v3/backend/core/hallucination_filter.py) | `_layer2_cross_validate` | 19 | ⬜ |
| 4 | [qwen_asr_runner.py](v3/backend/core/qwen_asr_runner.py) | `run_qwen_asr` | 19 | ⬜ |
| 5 | [orchestrator.py](v3/backend/translation/orchestrator.py) | `translate_with_orchestrator` | 17 | ⬜ |
| 6 | [subtitle_style.py](v3/backend/subtitle_style.py) | `build_ass_styled` | 16 | ⬜ |

**补充记录（2026-06-25）**：剩余复杂度拆分对话 A 已完成。`v3/backend/core/hallucination_filter.py` 四个目标均降级：`LightweightStemmer` F(46)→B(8)，`clean_and_stem` F(45)→B(7)，`_extract_qwen_sentences` D(21)→A(4)，`filter_hallucinations` D(24)→B(8)。纯重构零行为变更；每步全量测试，最终 **624 passed, 8 warnings**。记录见 `pipelines/refactor-pipeline-hallucination-filter-2026-06-25.md`，剩余 B/C/D 三组见 `pipelines/refactor-remaining-plan.md`。

**V3.07 双签 diff 遗留项（2026-06-21 版本收尾审查发现，归入 V3.08 一起做）**：

| # | 类型 | 内容 | 来源 |
|---|------|------|------|
| 1 | P1 代码质量 | 删空壳函数 `_assemble_qwen_on_whisper_boundaries`（直接调 `_assemble_whisper_primary`） | 副审 elon-musk |
| 2 | P1 代码质量 | `translate_raw_json` 中 `__import__('time')` 改正常 import | 副审 elon-musk |
| 3 | P1 代码质量 | `_is_repetition_pattern` 在 transcriber_v2 和 hallucination_filter 各有一套，统一 | 副审 elon-musk |
| 4 | P1 代码质量 | 间隔合并（interval merging）逻辑重复 3 处，提取公共函数 | 副审 elon-musk |
| 5 | P2 架构建议 | `_build_smart_boundaries` + `_extract_qwen_sentences` 共 90 行，可考虑简化为固定窗口 | 副审 elon-musk |
| 6 | P2 架构建议 | Refiner `RefinerWindow` 类可简化为 namedtuple | 副审 elon-musk |
| 7 | P2 架构建议 | "重新处理"按钮从禁用改为始终可点，建议加确认对话框 | 副审 elon-musk |
| 8 | 设计图更新 | 走查发现 4 处代码比设计图多（VAD检测/SV语言推断/人声地图/短字幕合并），更新设计图 | 走查 2.0 |

**执行规则**：
- `transcribe()`（复杂度 41）优先——一个函数干了 6 件事（模型加载/Whisper 转录/Qwen 交叉验证/SenseVoice 声音分类/FA 对齐/结果合并）
- 逐个拆分，一个改完验完再改下一个
- 目标：每个函数复杂度降到 ≤10
- 用 TDD 护体——拆前全量测试基线，拆后全量测试零退化

**为什么放 V3.08**：搬家后目录干净了再重构，不乱。目录整理和复杂度拆分一起做，V3.08 之后代码既整齐又易维护。

### 第三部分：启动健康检查（决策二·动态防线）

> 前期准备：录制 3 秒"心机音频"（1秒静音+2秒人声），手动标注每个字的准确时间点作为黄金标准答案。音频打包进资源目录。

| 状态 | 事项 | 说明 |
|------|------|------|
| ⬜ | 录制心机音频 | 前1秒静音/杂音 + 后2秒人声，手动标注逐字时间戳作为黄金标准答案 |
| ⬜ | FA 启动体检 | 用心机音频跑一次对齐，检查：等间距→造假→拦住；偏差>0.3秒→退化→拦住；<0.1秒→正常 |
| ⬜ | ASR 启动体检 | 用心机音频跑一次转录，检查输出文字是否大致正确 |
| ⬜ | SenseVoice 启动体检 | 用心机音频跑一次分类，检查前1秒识别为静音、后2秒识别为人声 |
| ⬜ | 体检失败报错 | 告知用户哪个模型没过 + 偏差多大 + 建议操作（不做自动修复） |

**执行时机**：软件启动时跑一次，藏在"模型加载中..."进度条里。不在每次任务提交时跑。

---

## V3.09 — PyWebView 桌面壳 + 精度报告（2026-06-12 路线修正）

> **路线修正（Steve Jobs 审查）**：取消 PySide6 重写。改用 PyWebView 把现有 HTML/CSS/JS 前端嵌入桌面窗口，复用率 ~90%。

| 状态 | 事项 | 说明 |
|------|------|------|
| ⬜ | PyWebView 桌面壳 | 原生窗口内嵌 WebView，替换浏览器访问 localhost |
| ⬜ | 前端嵌入适配 | FastAPI REST → PyWebView JS Bridge（`pywebview.api.xxx()`），WebSocket 日志 → 轮询 |
| ⬜ | 欢迎页 + 下载系统 | 首次启动展示下载进度 UI，统一下载依赖 + 模型，不弹 CMD |
| ⬜ | 7 区域 UI 适配 | 现有 HTML/CSS/JS 原样嵌入，适配 PyWebView 窗口尺寸 |
| ⬜ | 精度报告面板 | 转录完成后展示过滤统计、置信度分布、时间轴精度等级 |
| ⬜ | `--dev` 模式保留 | 开发者仍可浏览器访问 localhost 调试 |

**规则**：后端不动。前端复用。进入时已经是干净的 v3/ 目录。

### V3.09 设计到代码 · 完整流程（2026-06-06 锁定，2026-06-12 修正框架选型）

分两个阶段：**A 设计决策（不动代码）+ B 视觉规范与实现（动代码）**。

#### 阶段 A：设计决策（不动代码，纯审查，5 棒全 CLEARED 才进入阶段 B）

```
① office-hours（头脑风暴）
   输入：V3.09 需求——PyWebView 桌面应用 + 应用内下载系统
   讨论：7 区域划分 / 前后端通信（JS Bridge vs HTTP）/ 下载进度 UI / 多线程策略 / 文件结构
   产出：设计文档 v1（技术架构 + 数据流 + 模块划分）
          ↓
② plan-ceo-review（产品视角）
   审查：7 区域是否有必要 / 核心任务路径 / 产品定位（专业 vs 傻瓜）/ 范围边界
   产出：设计文档 v2（产品方向 + 范围锁定）
          ↓
③ plan-eng-review（工程视角）
   审查：QWidget vs QML / HTTP 本地回环 vs 直接调用 / QThread vs asyncio / 边界情况
   产出：设计文档 v3（架构选型 + 技术方案锁定）
          ↓
④ plan-design-review（设计师视角）
   审查：7 区域布局 / 信息层级 / 用户操作流程 / 状态反馈（等待/转录/翻译/完成/失败）
   产出：设计文档 v4（交互流程 + 信息架构 + 状态机锁定）
          ↓
⑤ plan-devex-review（开发者体验视角）
   审查：组件文件划分 / QSS 样式复用 / 调试体验 / UI 测试策略
   产出：设计文档 v5（开发规范 + 文件结构锁定）
```

```
🏁 设计阶段收官，五棒全 CLEARED → 进入阶段 B
```

#### 阶段 B：视觉规范 + 代码实现（动代码）

```
⑥ ui-ux-pro-max → 出文字版设计规范
   输入：设计文档 v5（7 区域布局 + 交互流程已定）
   产出：visual-spec.md
   内容：色彩系统 / 字体 / 间距 / 圆角 / 阴影 / 按钮样式 / 组件状态 / 布局尺寸
   举例："主按钮 #2563EB, 36px, 6px圆角" "卡片 #FFFFFF, 8px圆角, 0 1px 3px阴影"
          ↓
⑦ Claude → 对着 visual-spec.md 实现 PyWebView 桌面壳
   规范→代码映射：
     "主按钮 blue #2563EB" → CSS: .btn-primary { background: #2563EB }
     "卡片 8px圆角"       → CSS: .card { border-radius: 8px }
     "间距 16px"          → CSS: gap: 16px
   产出：desktop.py（PyWebView 窗口） + welcome.html（下载页） + 现有前端 HTML/CSS/JS 适配
          ↓
⑧ 开发者 → 运行 python desktop.py → 肉眼检查 → 调整数值 → 循环迭代
   一次迭代 ~3 分钟，目标 20 轮内收敛
```

**核心原则**：ui-ux-pro-max 只出文字规范，不写代码。①-⑥ 都不动代码。⑦ 才开始写第一行 PyWebView 适配。

### ⚠️ 跨平台移植注意事项（Mac 开发 → Windows 用户）

V3.09 在 MacBook 上开发，最终用户是 Windows。开发过程中必须在两台机器上都跑一遍，确认一致。

| 问题 | 表现 | 避免方式 |
|------|------|---------|
| 字体回退 | Mac 有 SF Pro/PingFang，Windows 没有；Windows 有微软雅黑，Mac 可能没有 | QSS 里写回退链：`font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif` |
| 窗口外观 | macOS 自动加圆角/阴影，Windows 可能是直角 | 显式声明窗口样式，不依赖系统默认 |
| DPI 缩放 | Mac Retina @2x，Windows 125%/150% | 用 `setMinimumSize` 而非 `setFixedSize` |
| 滚动条 | Mac 半透明浮动，Windows 固定宽度 | QSS 显式设置滚动条样式 |
| QSS 渲染 | 部分属性跨平台表现不一致 | 所有样式显式写死，不写 `default`/`native` |

**核心原则**：不依赖系统默认。颜色、字体、边框、间距全部在 QSS 里显式声明，操作系统换什么都不影响。

**Windows 预览方式**（不需要打包 EXE）：拷贝 `v3/` 目录到 Windows → 装 PyWebView → `python desktop.py`。

---

## V3.10 — 打包发布：PyInstaller 壳 + 应用内下载（2026-06-12 路线修正）

> **打包方案（Steve Jobs 审查定稿）**：壳 ~150 MB PyInstaller EXE。打开后 UI 内下载依赖+模型，不弹 CMD。用户从双击图标开始就在产品里了。

### 打包内容

| 组件 | 打进 EXE？ | 大小 | 说明 |
|------|----------|------|------|
| Python 解释器 | ✅ 是 | ~30 MB | PyInstaller 内置 |
| PyWebView | ✅ 是 | ~5 MB | 系统 WebView 套壳 |
| requests | ✅ 是 | ~1 MB | 下载能力 |
| FFmpeg | ✅ 是 | ~100 MB | 音频提取必需 |
| 欢迎页 HTML | ✅ 是 | ~1 KB | 下载进度 UI |
| **EXE 合计** | | **~150 MB** | **零依赖，双击打开** |
| 运行环境（torch 等） | ❌ 应用内下载 | ~1.4 GB | UI 进度条，GPU 自动检测 |
| 基础模型（Whisper） | ❌ 应用内下载 | 2.9 GB | HuggingFace 镜像回退 |
| 高精度引擎（Qwen3） | ❌ 应用内下载 | 4.4 GB | 可选 |

**界面布局**：APP 顶部模型检测区域 → 5 个组件状态（✅/❌）→ **一个「下载全部模型」按钮** → 点击后进度条显示每个模型的下载进度。Whisper 另在下方模型选择区单独选型号下载。

**5 个组件及用户可见名称**：

| # | 用户看到的名字 | 技术名称 | 大小 | 下载方式 |
|---|-------------|---------|------|---------|
| 1 | 基础转录 | Whisper（5 型号） | 75MB–2.9GB | 下方模型选择区，点选型号圆点触发下载 |
| 2 | 高精度转录 | Qwen3-ASR | ~4.7GB | **一键下载全部**（含内嵌 FA，Mac 不需单独下载） |
| 3 | 噪音过滤 | SenseVoice | ~944MB | **一键下载全部** |
| 4 | 说话人区分 | Resemblyzer | ~99MB | **一键下载全部** |
| 5 | FFmpeg | FFmpeg（full build） | ~100MB（zip） | **安装包内置**（检测缺失时提示安装） |

**关键规则**：
- **一个按钮下载全部模型**（Qwen + SenseVoice + Resemblyzer）。不再提供每个模型的单独下载按钮。用户不需要理解"FA 是什么""为什么有两个千问模型"——点一下，三个模型一起下
- Whisper 独立选择：5 个型号（tiny 75MB / base 145MB / small 488MB / medium 1.5GB / large-v3 2.9GB），用户按需选一个。默认推荐 medium
- FA 不再出现在下载列表里——Mac 上内嵌在千问 ASR 库中，Windows 上随千问 ASR 自动加载
- 下载按钮下方显示内存提示：「⚖️ 标准模式需 8GB 内存 | 🎯 精确模式需 16GB 内存」
- 所有 HuggingFace 下载走 `HF_ENDPOINT=https://hf-mirror.com` 国内镜像（P133）
- FFmpeg 必须是**完整版（full build）**，包含字幕烧录滤镜（libass 等）

### 待办事项

| 状态 | 事项 | 编号 | 说明 |
|------|------|------|------|
| ⬜ | 「下载全部模型」按钮 | — | 一键下载 Qwen ASR + SenseVoice + Resemblyzer（~5.7GB 合计）。进度条显示每个模型的下载进度。Mac FA 内嵌不需单独下载，Windows 随千问自动加载 |
| ⬜ | Whisper 型号选择 + 下载 | — | 5 个型号圆点选择（tiny/base/small/medium/large-v3），默认推荐 medium（~1.5GB）。点选后触发下载 |
| ⬜ | HuggingFace 镜像 | P133 | 所有 HuggingFace 下载走 `HF_ENDPOINT=https://hf-mirror.com` |
| ⬜ | FFmpeg 内置打包（full build） | — | ~100MB，打进入安装包，必须包含字幕烧录功能 |
| ⬜ | 打包方案重新评估 | — | PyInstaller→Nuitka。需重测 FROZEN / `sys._MEIPASS` 兼容性 |
| ⬜ | FA ModelScope 预下载 | P208 | ⏸ Windows 打包需要 FA 模型单独预下载（Mac 内嵌不需要）。待 Windows 真机确认 |
| ⬜ | Windows 真机打包 | P209 | 在真 Windows 机器上用 Nuitka 打包成 .exe |
| ⬜ | 启动自检一键修复 | P210 | 启动时检查所有零件。缺模型→一键下载。缺 FFmpeg→一键安装。内存不足→提示档位要求 |
| ⬜ | Docker 方案评估 | P211 | 一个箱子装所有依赖+模型，不挑系统 |
| ❌ | ~~FA 对齐状态推送 UI~~ | P216 | 已合并到 V3.09 精度报告 |
| ✅ | ~~`pyproject.toml` 加 `qwen-asr==0.0.6`~~ | ~~P207①~~ | **已移至 V3.07 Commit 4（决策二·静态防线）** |
| ✅ | ~~全部依赖版本锁定 `==`~~ | ~~P207③~~ | **已移至 V3.07 Commit 4（决策二·静态防线）** |
| ✅ | ~~tokenizers 升到 0.23.0~~ | ~~P207④~~ | **已移至 V3.07 Commit 4（决策二·静态防线）** |

**规则**：不改算法。不改 UI 逻辑。只加下载和打包。

---

## V4 — 体验打磨 + 数据飞轮（V3 全部完成后启动）

| 编号 | 事项 | 前置条件 |
|------|------|---------|
| ① | GPU 加速包内置（仅 Windows+NVIDIA，Mac 原生加速） | — |
| ③ | 字幕手动微调界面（PySide6 面板） | Phase 1D 对齐做到极致后评估 |
| ④ | RAG 翻译修正反馈循环（四层记录结构） | 需先有用户基础和数据 |
| ⑨ | 智能硬件调度（两档自动推荐） | 启动时检测可用内存 → 自动推荐标准（≥8GB）或精确（≥16GB）。用户可手动覆盖。FA 不再外部存在，无需单独调度 |

---

## 未来 — 无固定版本号

| 事项 | 前置条件 |
|------|---------|
| 多语言翻译（任意语言→任意语言） | V3.09 语言选择 UI 上线后，逐步扩展语言列表。`_TARGET_LANGUAGE` 变量已就绪，加语言=加选项 |
| 双语字幕（原文+译文同屏显示） | 渲染器从 `transcript.json` 缓存读原文，与翻译结果合并烧录。数据已有，不须重新转录 |
| 积分体系 + 云端语料库 + 账号系统 | 需先有用户基础 |
| P151：千问 context 提示 | 延期到 Windows 实测 |
| 在线 CC 字幕提取（YouTube/B站） | — |
| NAS 代理服务器（知识库+语料库+账号密码中心存储） | 需先有 RAG 系统和用户语料积累 |

---

**四条线，不交叉**：V3.07 修后端 bug + 版本锁定（4 个 Commit）。V3.08 搬家 + 复杂度拆分 + 启动健康检查 + 黑匣子日志（P237）+ HARDEN 延后项。V3.09 PyWebView 桌面壳 + 精度报告。V3.10 PyInstaller 打包发布（壳 + 应用内下载）。

### 2026-06-10 HARDEN 新增问题（P240-P248）

| 编号 | 严重度 | 问题 | 位置 |
|------|--------|------|------|
| P240 | 🔴 CRITICAL | llm_fallback_decision 路径穿越 | main.py — ✅ 已修 |
| P241 | 🟡 HIGH | retry 硬编码"英文→中文" | orchestrator.py — ✅ 已修 |
| P242 | 🟡 HIGH | transcribe_raw() MLX 后端崩溃 | qwen_asr_runner.py — ⏳ 延后 V3.08 |
| P243 | 🟡 MEDIUM | /api/llm-models SSRF | main.py — ✅ 已修 |
| P244 | 🟡 MEDIUM | get_job 缺路径穿越校验 | main.py — ✅ 已修 |
| P245 | 🔵 LOW | GLOBAL_HASH_INDEX 无锁 | main.py — ⏳ 延后 V3.08 |
| P246 | 🟡 MEDIUM | L4"重复模式""重复词"缺 L2 GREEN 豁免 | hallucination_filter.py — ✅ 已修 |
| P247 | 🔵 LOW | P1 ASR 分歧裁决未实现 | orchestrator.py — ⏳ 延后 V3.08 |
| P237 | 🔴 P0 | 系统日志是"日记"不是"黑匣子"（根本原因无法定位） | worker.py/sensevoice_runner.py — ⏳ 延后 V3.08 |
| P248 | 🟡 MEDIUM | P3 反思仅在失败时触发 | worker.py — ✅ 已修 |

**修复策略（2026-06-10 用户拍板，含尾巴整合）**：

**本版本修（V3.07 HARDEN Sprint · 5 个 Commit · 8 项代码修复 + 1 项配置）**：
- Commit 1 安全防线：P240 + P243 + P244（抽象 `_validate_job_path` + `verify_safe_url`）
- Commit 2 翻译质量：P241 + P248（动态源语言 + 删 `and failed`）
- Commit 3 过滤器豁免：P246（L2 GREEN 豁免加到 L222/L228）
- Commit 4 版本锁定（决策二·静态防线）：pyproject.toml 全部依赖 `==` 锁死
  - qwen-asr==0.0.6（P207 子任务①）
  - transformers==4.57.6 + tokenizers==0.23.0（P207 子任务③④）
- Commit 5 尾巴修复：P213 + P214（词数漂移修复 + FA 降级可见化）

**延后到 V3.08 Backlog**：
- P247（ASR 分歧裁决 🟡 MEDIUM）— 换库后 L1 信号更可靠，裁决价值上升。描述修正：Whisper 版本 → 主引擎版本
- P254（转录智能路由 🟡 HIGH）— 换库后 Qwen 可独立运行，从未来想法提升到 V3.08 第一批。英语/日/韩→Qwen 主+Whisper 副，冷门→Whisper 主
- **P237（黑匣子日志）** — 系统日志只有"日记"没有"状态快照"，根因靠猜。V3.08 加 `checkpoint()` 工具函数 + `psutil` 内存/进程/线程快照

**已关闭（换 MLX ASR 库或审查判定后自然解决）**：
- ❌ P179（L2 接入 Whisper 逐字时间戳）— 千问自带词级时间戳，L2 归窗已精确化，加 Whisper 维度是画蛇添足
- ❌ P212（FA 真实语音验证）— 外部 FA 已随换库删除，FA 验证并入启动健康检查（心机音频）
- ❌ P149/P171（L2 门槛校准）— 精确时间归窗替代相似度猜窗口，门槛从需人工校准变默认值够用
- ❌ P242（MLX 后端崩溃）— bug 流水线已确认 `transcribe_raw()` 为死代码。换库后旧库整体删除，崩溃路径消失
- ❌ P245（并发锁缺失）— bug 流水线已验证 asyncio 单线程原子，加锁防御不存在的竞态

**全部 14 项问题总览**：

| # | 编号 | 问题 | 处置 |
|---|------|------|------|
| 1 | P240 | 路径穿越（llm_fallback_decision） | ✅ 本次修 · Commit 1 |
| 2 | P243 | SSRF（/api/llm-models） | ✅ 本次修 · Commit 1 |
| 3 | P244 | 路径穿越（get_job） | ✅ 本次修 · Commit 1 |
| 4 | P241 | retry 硬编码源语言 | ✅ 本次修 · Commit 2 |
| 5 | P248 | P3 反思仅失败触发 | ✅ 本次修 · Commit 2 |
| 6 | P246 | L4 缺 L2 GREEN 豁免 | ✅ 本次修 · Commit 3 |
| 7 | — | pyproject.toml 版本锁定 | ✅ 本次修 · Commit 4 |
| 8 | P213 | 词数漂移（Python分词≠FA分词） | ✅ 本次修 · Commit 5 |
| 9 | P214 | FA 降级静默（用户不知道） | ✅ 本次修 · Commit 5 |
| 10 | P242 | MLX 后端崩溃 | ❌ 换库关闭（死代码随旧库删除） |
| 11 | P245 | 并发锁缺失 | ❌ 假 bug（asyncio 单线程原子，不修） |
| 12 | P247 | ASR 分歧裁决 | ⏳ 延后 V3.08（换库后信号更可靠，严重度 LOW→MEDIUM） |
| 13 | P179 | L2 接入 Whisper 逐字时间戳 | ❌ 换库关闭（千问自带词级时间戳） |
| 14 | P212 | FA 真实语音验证 | ❌ 换库关闭（外部 FA 已删，验证并入心机音频） |
| 15 | P237 | 黑匣子日志 | ⏳ 延后 V3.08 |
| 16 | P149/P171 | L2 门槛校准 | ❌ 换库关闭（精确时间归窗，默认值够用） |
| 17 | P254 | 转录智能路由 | 🆕 提升到 V3.08 第一批（换库后 Qwen 可独立运行） |

---

### 三大战略决策（2026-06-10 乔布斯审查 + 工程师联审，全部定稿）

#### 决策一：管线复杂度
- **结论**：功能不动（五层过滤器全保留），V3.08 拆 worker.py 按职责分模块
- **原因**：五模型参数互相咬合，砍一层需重调其余全部参数，成本远超维持现状
- **执行**：V3.08 第二部分（P219 复杂度拆分）已覆盖

#### 决策二：模型防退化（P207 教训）
- **问题**：版本锁死防不了"模型看起来正常但吐假数据"（P207 FA 均匀假时间戳污染五层过滤器数周）
- **四道防线**：

| 防线 | 做什么 | 防什么 | 版本 |
|------|--------|--------|------|
| 静态防线 | pyproject.toml 全部 `==` 锁死 | 依赖库偷偷升级 | V3.07 Commit 4 |
| 动态防线 | 启动时用心机音频（1秒静音+2秒人声）体检 FA/ASR/SenseVoice | 静默造假 + 精度退化 + 文件损坏 | V3.08 |
| 体验防线 | 输出报告标注时间轴精度等级（高/中/低） | 让用户对质量心里有数 | V3.09 |
| 打包复用 | 同一套检查嵌入打包流水线 | 开发机正常但打包产物异常 | 桌面端打包阶段 |

- **心机音频设计**：前1秒静音/杂音 + 后2秒人声。一段音频同时验三个模型——FA造假第一个字从0秒开始（露馅）、SenseVoice卡死在"有人声"（前1秒抓出来）、ASR转出静音段文字（暴露）
- **执行时机**：只在软件启动时跑一次，藏在"模型加载中..."进度条里
- **判定标准**：等间距→造假→拦住 | 偏差>0.3秒→严重退化→拦住 | 0.1-0.3秒→轻度退化→放行标注 | <0.1秒→正常
- **延后项**：自救按钮（等收集真实失败数据）、打包复用（等打包流水线存在）

#### 决策三：战略重心转移（2026-06-12 修正）
- **结论**：V3.07 是最后一个后端专注版本，封版后全力转 **PyWebView 桌面壳**（非 PySide6）
- **理由**：复用现有 HTML/CSS/JS 前端，PyWebView 套壳即可，无需重写。PySide6 重写 = 全部推倒重来，成本巨大且无收益
- **打包方案**：壳 + 应用内下载（~150 MB EXE，打开后 UI 内下载依赖和模型，不弹 CMD）。详见 video-subtitle.md
- **后端**：进入维护模式，只修安全漏洞和严重 bug
- **Web 前端**：V3.09 嵌入 PyWebView，网页端开发模式保留（`--dev` 开浏览器调试）
