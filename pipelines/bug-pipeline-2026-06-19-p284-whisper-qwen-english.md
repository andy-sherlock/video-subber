# P284 Whisper/Qwen English Subtitle Regression — 2026-06-19

## 元信息
- 涉及问题：P284
- 涉及文件：
  - v2/backend/transcriber_v2.py:636-798 — Qwen/Whisper 字幕组装入口和各组装函数
  - v2/backend/worker.py:179-230 — Refiner single/dual 模式选择和 prompt 构造
  - v2/backend/core/hallucination_filter.py:563-780 — L2 窗口级交叉检查
  - v2/test/test_p260_boundary_fix.py:1-90 — 现有 Whisper 边界组装测试
  - v2/test/test_p268_p269_pipeline.py:141-190 — 现有 Refiner 模式测试
  - v2/test/test_p273_qwen_ja_assembly.py:1-90 — 现有日语组装冻结测试
- 当前进度：①/⑩

## ① investigate — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件
**读了什么**：
- /Users/andy/.video-subber/jobs/a2789ff719/cache/f82cadc4bac1/transcript.json
- /Users/andy/.video-subber/jobs/a2789ff719/London VLOG： Exploring the City, Markets, & Hidden Gems_video.srt
- /Users/andy/.video-subber/logs/a2789ff719/user.log
- v2/backend/transcriber_v2.py:636-798, 1148-1184
- v2/backend/worker.py:179-230, 520-590
- v2/backend/core/hallucination_filter.py:563-780

### 结论（下一步只看这里）
London VLOG 英文成品出现两个主问题：第一句边界错误，第二句 `clock` 被听成 `claw` 后没有被修正。真实产物显示最终 416 条字幕中 `whisper_text` 覆盖为 0 条，所以 Refiner 只能走 single 模式，不能走 dual 模式。

根因不是 LLM 自己选错，也不是日语句尾规则直接套到英语。根因是 Qwen 主引擎路径 `_smart_assemble()` 调用了 `_assemble_qwen_primary()`。该函数只按 Qwen 词、停顿和通用句尾切字幕，没有把 Whisper 段落文字挂回 `seg.whisper_text`。因此 `worker.py` 的 `_choose_refiner_mode()` 找不到任何 Whisper 参考稿，只能返回 `single`。

L2 确实是两份转录结果的检查，但它是窗口级检查：把一段时间内的文字聚合后比较词重合度。它适合发现大段跑偏、幻觉字幕、缺数据，不适合逐句决定 `clock/claw` 这种单词级错听。LLM 不应整体挪进 L2。更清晰的职责是：L2 标风险，Refiner 看最终字幕上的 Qwen 正文和 `whisper_text` 做逐句修正。

修复方案：
1. 新增英语专用组装函数，例如 `_assemble_qwen_on_whisper_boundaries()`。
2. 仅当 `primary_engine == "qwen"`、`lang == "en"`、Whisper 可用时启用。
3. 新函数用 Whisper 段落边界装 Qwen 词，同时设置 `seg.whisper_text = ws.text`。
4. 日语继续走 `_assemble_qwen_primary()`，不改 P273 日语句尾规则。
5. 测试必须覆盖三件事：英文第一句不能把 `Oh gosh` 粘进上一句；英文最终字幕必须带 `whisper_text` 并让 Refiner 进入 dual；P273 日语组装输出不回归。

### 关键代码
```python
if primary_engine == "qwen":
    return _assemble_qwen_primary(qwen_words, whisper_segments, lang)
```

```python
def _choose_refiner_mode(segments) -> str:
    refs = []
    for seg in segments:
        whisper_text = getattr(seg, "whisper_text", "")
        if whisper_text:
            refs.append(...)
    return "dual" if _whisper_is_usable(refs, total_end=total_end) else "single"
```

### 涉及文件列表
- v2/backend/transcriber_v2.py:636-798 — 需要新增英语专用组装路径
- v2/backend/worker.py:179-230 — 不直接改，但测试要确认 dual 模式恢复
- v2/backend/core/hallucination_filter.py:563-780 — 不把 LLM 挪进 L2，仅保留检查职责
- v2/test/test_p260_boundary_fix.py:1-90 — 添加英文 Whisper 边界测试
- v2/test/test_p268_p269_pipeline.py:141-190 — 添加 dual 模式/冲突 prompt 测试
- v2/test/test_p273_qwen_ja_assembly.py:1-90 — 作为日语不回归测试

## ② 双签审查 — 2026-06-19
**执行人**：Codex
**依赖类型**：📋 看记录
**前置阅读**：① 结论

### plan-eng-review 结论
方案通过，但范围必须收窄。正确修法不是把 LLM 挪进 L2，也不是扩大或重写 `_assemble_qwen_primary()`。最小改动是在 `_smart_assemble()` 的 Qwen 主路径里加一个英语专用分支：当语言是 `en` 且 Whisper 可用时，用 Whisper 边界承载 Qwen 文字，并把 `ws.text` 写回 `seg.whisper_text`。

边界要求：
- 只对 `lang == "en"` 启用，不能写成非 CJK 全启用。
- `worker.py` 不改。它已经按 `whisper_text` 自动选择 dual/single。
- L2 不改。L2 保持窗口级低成本检查，不承担逐句纠错。
- 日语 P273 组装路径不动，并跑现有日语测试确认不回归。

### steve-jobs-perspective 结论
产品上要砍掉复杂性：不要把 LLM 塞进 L2，让一个步骤同时做检查和修文案。用户真正需要的是字幕看起来对，不是流程名更漂亮。最清楚的体验链路是：Whisper 提供边界和参考稿，Qwen 提供主文字，LLM 只在最后看两份稿子修明显错词。

## ③ 双签影响面 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖🌐 读全项目
**前置阅读**：①-② 结论；手动 fallback 扫描，未使用 hex-graph MCP。

### ln-021-codegraph 手动 fallback 结论
调用链：
- `transcribe()` 在 v2/backend/transcriber_v2.py:1158 调用 `_smart_assemble()`。
- `_smart_assemble()` 在 v2/backend/transcriber_v2.py:776-798 根据主引擎选择组装函数。
- `_assemble_qwen_primary()` 在 v2/backend/transcriber_v2.py:636-679 产生最终 Segment，但不写 `whisper_text`。
- `_assemble_whisper_primary()` 在 v2/backend/transcriber_v2.py:682-735 会写 `seg.whisper_text = ws.text`。
- `worker.py` 在 v2/backend/worker.py:179-190 用 `whisper_text` 决定 Refiner 是否 dual。

测试影响：
- v2/test/test_p260_boundary_fix.py 已覆盖 `_smart_assemble()` 的 Whisper 边界模式，适合新增 P284 英文 Qwen 主路径测试。
- v2/test/test_p268_p269_pipeline.py 已覆盖 P269 Refiner，适合新增 `whisper_text` 触发 dual 的测试。
- v2/test/test_p273_qwen_ja_assembly.py 已覆盖日语组装，作为不回归检查。

### bill-gates 结论
系统层面风险不在单个阈值，而在数据契约断了。`worker.py` 需要的输入契约是：如果有 Whisper 参考稿，最终字幕必须带 `whisper_text`。这次 Qwen 主路径破坏了这个契约。修复应恢复契约，而不是让下游猜测或重跑模型。

### 下一步
进入 TDD：先写失败测试，再改生产代码。

## ④ 双签 diff + 简化 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖🌐 读全项目
**前置阅读**：当前 diff；涉及文件行号见元信息

### /review 结论
本次相关 diff 只应关注三处：
- v2/backend/transcriber_v2.py:376-424 — 短字幕合并时同步合并 `whisper_text`。
- v2/backend/transcriber_v2.py:751-814 — 新增英语 Qwen 主路径的 Whisper 边界分支。
- v2/test/test_p260_boundary_fix.py 和 v2/test/test_p268_p269_pipeline.py — 新增 P284 失败测试。

未发现需要阻断的问题。`worker.py` 不需要改，因为它已经按 `whisper_text` 自动选择 dual/single。L2 不需要改，因为它的职责是窗口级风险检查，不是逐句修文案。

注意：同一文件里还有 Whisper 换库分支早已有的 `whisper_runner` 改动，不属于 P284 本次修复，不在本次审查范围内评价。

### elon-musk 结论
更简单的路是恢复数据契约，而不是搬家：不要把 LLM 移到 L2，不要新增一套新的 Refiner，不要泛化到所有语言。当前实现只在英语 Qwen 主路径加一个小分支，符合最小修复。

## ⑤ 设计一致性走查 — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件
**设计图**：上传视频 → Whisper/Qwen 转录 → `_smart_assemble()` 组装最终字幕 → L2 标风险 → `worker.py` Refiner 看最终字幕和 `whisper_text` 修错词 → 翻译输出
**代码入口**：v2/backend/transcriber_v2.py:_smart_assemble()

### 逐步骤对照
| 设计步骤 | 代码对应 | 一致？ |
| --- | --- | --- |
| Qwen 主文字进入组装 | `_flatten_qwen_words()` + `_smart_assemble()` | ✅ |
| 英文使用 Whisper 边界 | `_smart_assemble()` 中 `lang == "en" and whisper_ok` | ✅ |
| Whisper 参考稿挂回最终字幕 | `_assemble_whisper_primary()` 写 `seg.whisper_text = ws.text` | ✅ |
| 日语保留原组装路径 | 非英语继续 `_assemble_qwen_primary()` | ✅ |
| L2 只做窗口级检查 | `filter_hallucinations()` 调 `_layer2_cross_validate()` | ✅ |
| Refiner 根据 `whisper_text` 选择 dual | `worker.py:_choose_refiner_mode()` | ✅ |

### 数据流追踪
| 步骤 | 数据包 | 手（函数） | 读/改/扔 | 设计图上？ |
| --- | --- | --- | --- | --- |
| Whisper 输出 | `whisper_segments` | `_smart_assemble()` | 读 | ✅ |
| Qwen 输出 | `qwen_chunks` | `_flatten_qwen_words()` | 改成词列表 | ✅ |
| 英文最终字幕 | Qwen 词 + Whisper 边界 | `_assemble_qwen_on_whisper_boundaries()` | 改成 Segment | ✅ |
| Whisper 参考稿 | `ws.text` | `_assemble_whisper_primary()` | 写入 `seg.whisper_text` | ✅ |
| 短字幕合并 | `seg.whisper_text` | `merge_short_subtitle_fragments()` | 合并保留 | ✅ |
| Refiner 输入 | `segments` | `_choose_refiner_mode()` | 读 | ✅ |

### 偏差汇总
无未处理偏差。LLM 不移入 L2，避免检查层和修正文案层混在一起。

## ⑦ test-driven-development — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件

### RED
新增测试：
- `TestP284EnglishQwenUsesWhisperBoundaries.test_english_qwen_primary_does_not_merge_next_whisper_sentence`
- `TestP284EnglishQwenUsesWhisperBoundaries.test_japanese_qwen_primary_keeps_existing_assembly_path`
- `TestP284MergeKeepsWhisperText.test_merging_short_fragment_keeps_reference_text`
- `TestP269RefinerSingleMode.test_whisper_text_switches_refiner_to_dual_conflict_prompt`

失败结果：
- 英文第一句当前会输出 `Hey Taylor what time is it Oh gosh I don't`。
- 短字幕合并后 `whisper_text` 少了前置短词 `Oh`。

### GREEN
生产代码最小改动：
- 新增 `_assemble_qwen_on_whisper_boundaries()`，复用 Whisper 边界组装逻辑。
- `_smart_assemble()` 中仅对 `lang == "en" and whisper_ok` 的 Qwen 主路径启用该函数。
- 新增 `_merge_whisper_text()`，短字幕合并正文时同步合并参考稿。

验证结果：
- `./.venv/bin/python -m pytest test/test_p260_boundary_fix.py test/test_p268_p269_pipeline.py test/test_p273_qwen_ja_assembly.py -q`
- 结果：25 passed in 0.07s

## ⑥ security-review — 2026-06-19
**执行人**：Codex
**依赖类型**：📖 读指定文件

### 结论
跳过。P284 修改不涉及认证、权限、网络入口、用户输入解析、文件上传、API Key、敏感数据存储或命令执行。改动范围仅在本地字幕段组装和测试。

## ⑧ verification-before-completion — 2026-06-19
**执行人**：Codex
**依赖类型**：🏃 跑应用

### 验证命令
- `./.venv/bin/python -m pytest test/test_p260_boundary_fix.py test/test_p268_p269_pipeline.py test/test_p273_qwen_ja_assembly.py -q`
  - 结果：`25 passed in 0.07s`
- `./.venv/bin/python -m pytest test/test_harden_v307_sprint2.py test/test_phase1d_qwen_health.py test/test_p160_layer2.py test/test_p160_stemmer.py -q`
  - 结果：`59 passed, 2 warnings in 4.02s`
- `./.venv/bin/python -m pytest test/test_p260_boundary_fix.py test/test_p268_p269_pipeline.py test/test_p273_qwen_ja_assembly.py test/test_p276_standard_language_env.py test/test_phase1d_qwen_health.py test/test_phase1d_integration.py test/test_p251_qwen_window_assign.py test/test_p228_timestamp_fix.py -q`
  - 结果：`80 passed in 4.86s`

### 全量测试边界
- `./.venv/bin/python -m pytest test -q`
  - 结果：收集阶段失败，原因是当前 Codex/headless 环境没有 Metal/GPU，`mlx` 报 `No Metal device available`。
  - 判断：这是环境限制，不是 P284 修改导致；同类限制已在 P266 换库记录中出现。

## ⑨/⑩ QA / 端到端 — 2026-06-19
**执行人**：Codex
**依赖类型**：🏃 跑应用

### 结论
未在当前 Codex/headless 环境重跑 London VLOG。原因是真实 MLX 转录需要普通 Mac 前台 Metal/GPU 环境。下一步应在普通 Mac 前台环境重跑 job，人工检查：
- 第 1 条英文源字幕应停在 `Hey Taylor what time is it`，不能包含 `Oh gosh`。
- 第 2 条应通过 dual Refiner 看到 Whisper 的 `clock` 参考稿。
- 成品中文不应再出现「真希望我有爪子」。
