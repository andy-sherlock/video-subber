# transcriber_v2 复杂度拆分 — 2026-06-25

## 元信息

- 涉及问题：V3.08 P219 复杂度拆分，对话 B
- 涉及文件：`v3/backend/transcriber_v2.py`
- 当前进度：①/进行中
- 任务性质：纯重构，只搬代码，不改转录逻辑、不改断句规则、不改模型路由、不改对外函数名

## ① 恢复上下文与基线 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**读了什么**：
- `pipelines/refactor-remaining-plan.md:1-99`
- `pipelines/refactor-pipeline-orchestrator-whole-markers-2026-06-25.md:1-161`
- `task_plan.md:1-260`
- `progress.md` 末尾 V3.08 记录
- `findings.md:1-220`
- `v3/backend/transcriber_v2.py:222-330`
- `v3/backend/transcriber_v2.py:487-536`
- `v3/backend/transcriber_v2.py:657-711`
- `v3/test/test_p268_p269_pipeline.py:65-90`
- `v3/test/test_p287_whisper_text_backfill.py:1-169`
- `v3/test/test_p273_qwen_ja_assembly.py:1-90`
- `v3/test/test_p275_voice_timeline.py:92-114`
- `v3/test/test_p291_refiner_sliding_window.py:64-74`

### 结论（下一步只看这里）

对话 C 已完成但未提交。当前工作区已有这些 C 组改动，本轮必须保留，不回滚：

```text
M pipelines/refactor-remaining-plan.md
M progress.md
M task_plan.md
M v3/backend/translation/orchestrator.py
M v3/test/test_arch9_dynamic_chunk.py
?? pipelines/refactor-pipeline-orchestrator-whole-markers-2026-06-25.md
```

本轮 B 组目标复杂度：

```text
backend/transcriber_v2.py
    F 222:0 _detect_speaker_changes - D (28)
    F 657:0 _assemble_qwen_primary - D (25)
    F 487:0 _whisper_is_usable - D (22)
```

拆前基线：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 104.98s
```

三个目标函数当前职责：

1. `_whisper_is_usable()`：判断 Whisper 参考稿是否够用。包含长视频覆盖率检查、空文本检查、数字幻觉检查、重复文本检查、连续数字跑马灯检查。
2. `_assemble_qwen_primary()`：把 Qwen 词级结果组装成字幕段。包含断句参数、SenseVoice 标签放宽停顿、日语微停顿断句、跨度/字数兜底、回填 `whisper_text`。
3. `_detect_speaker_changes()`：用 resemblyzer 声纹比较相邻字幕。包含依赖加载、声纹提取、相似度收集、单人判断、阈值搜索、应用 speaker_change、间隙检测和安全钳取消。

直接覆盖测试：

- `_whisper_is_usable()`：`test_p268_p269_pipeline.py:65-90`，另有 `test_p291_refiner_sliding_window.py:64-74` 间接依赖。
- `_assemble_qwen_primary()`：`test_p273_qwen_ja_assembly.py:28-86`、`test_p287_whisper_text_backfill.py:22-165`。
- `_detect_speaker_changes()`：未发现直接单测，主要靠全量测试兜底。

### 涉及文件列表

- `v3/backend/transcriber_v2.py:222-330` — `_detect_speaker_changes()` 目标函数
- `v3/backend/transcriber_v2.py:487-536` — `_whisper_is_usable()` 目标函数
- `v3/backend/transcriber_v2.py:657-711` — `_assemble_qwen_primary()` 目标函数
- `v3/test/test_p268_p269_pipeline.py:65-90` — Whisper 可用性行为测试
- `v3/test/test_p287_whisper_text_backfill.py:22-165` — Qwen 主路径回填参考稿行为测试
- `v3/test/test_p273_qwen_ja_assembly.py:28-86` — Qwen 日语断句行为测试

## ② 拆 `_whisper_is_usable()` — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：① 结论 + `v3/backend/transcriber_v2.py:487-536`

### 结论（下一步只看这里）

新增 6 个顶层 helper，把 Whisper 可用性判断里的子检查搬出主函数：

- `_resolve_whisper_total_end()`：保留 `total_end is None` 时按 segment 最大 `end` 推断总时长。
- `_whisper_has_enough_long_media_coverage()`：保留长视频两条规则：`total_end >= 300 and len < 20` 直接不可用；覆盖率 `< 0.30` 直接不可用。
- `_collect_digit_whisper_segments()`：保留 NFKC 归一化后纯数字段收集逻辑。
- `_has_too_many_digit_segments()`：保留数字段占比 `>= 0.2` 判不可用。
- `_has_repeated_whisper_texts()`：保留重复文本占比 `>= 0.30` 判不可用。
- `_has_long_sequential_digit_run()`：保留连续数字跑马灯 `max_run >= 5` 判不可用。

主函数判断顺序不变：

1. 空 `whisper_segments` → `False`
2. 长视频覆盖率不足 → `False`
3. 无非空文本 → `False`
4. 少于 5 段 → `True`
5. 数字段太多 → `False`
6. 重复文本太多 → `False`
7. 返回是否没有长连续数字跑马灯

拆后复杂度：

```text
_whisper_is_usable D(22) → B(9)
```

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 105.24s
```

### 涉及文件列表

- `v3/backend/transcriber_v2.py:487-562` — 新 helper + `_whisper_is_usable()` 编排

## ③ 拆 `_assemble_qwen_primary()` — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：② 结论 + `v3/backend/transcriber_v2.py:657-711`

### 结论（下一步只看这里）

新增 6 个顶层 helper，把 Qwen 主路径组装拆成“参数、断句、组段、回填参考稿”：

- `_qwen_primary_assembly_params()`：保留 joiner、`base_pause=0.5`、日语 `max_span=5.0/max_chars=25`、其他语言 `8.0/40`。
- `_qwen_primary_pause_threshold()`：保留 SenseVoice 标签 `<|Breath|>`、`<|Crying|>`、`<|Laughter|>` 时把停顿阈值放宽到 `base_pause * 1.5`。
- `_qwen_primary_buffer_exceeds_limits()`：保留当前 buffer 文本长度和跨度兜底断句。
- `_should_break_qwen_primary()`：保留原断句顺序：停顿、标点、日语微停顿规则、跨度/字数兜底。
- `_split_qwen_primary_words()`：保留用 `_words_to_segment()` 从 Qwen words 组装字幕段。
- `_backfill_qwen_primary_whisper_text()`：保留按时间重叠把 Whisper 文本写入 `seg.whisper_text`，没有 Whisper 时写空字符串。

主函数只保留：

```text
if not qwen_words: return []
segments = _split_qwen_primary_words(...)
_backfill_qwen_primary_whisper_text(...)
return segments
```

拆后复杂度：

```text
_assemble_qwen_primary D(25) → A(2)
```

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 107.56s
```

### 涉及文件列表

- `v3/backend/transcriber_v2.py:683-761` — 新 helper + `_assemble_qwen_primary()` 编排

## ④ 拆 `_detect_speaker_changes()` — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：③ 结论 + `v3/backend/transcriber_v2.py:222-330`

### 结论（下一步只看这里）

新增 9 个顶层 helper，把声纹检测拆成独立步骤：

- `_load_speaker_change_dependencies()`：加载 `resemblyzer` 和 `librosa`，缺依赖时仍按原逻辑 warning 后跳过。
- `_create_voice_encoder()`：创建 `VoiceEncoder()`，异常时仍静默跳过。
- `_get_speaker_embedding()`：保留单段小于 1 秒跳过、`librosa.load(... sr=16000 ...)`、`preprocess_wav()`、`encoder.embed_utterance()`，异常仍返回 `None`。
- `_collect_speaker_similarities()`：保留相邻有效声纹的 `np.dot()` 相似度收集。
- `_find_speaker_change_threshold()`：保留“最小组内方差”自动找阈值。
- `_apply_speaker_change_threshold()`：保留 `sim < best_thr` 时写 `seg.speaker_change=True`。
- `_clear_speaker_changes()`：保留取消检测时把已标记的变化清回 `False`。
- `_speaker_gap_check_allows_split()`：保留间隙比 `< 3.0` 时取消全部变化。
- `_speaker_group_gap_allows_split()`：保留组间距 `< 0.15` 时取消全部变化。

主函数仍保持原判断顺序：

1. 依赖/编码器不可用 → 返回原 segments
2. 有效相似度不足 → 返回原 segments
3. 段数不足 5 → 返回原 segments
4. 分数像单人 → 返回原 segments
5. 找阈值并先写 `speaker_change`
6. 间隙检查不过 → 清标记并返回原 segments
7. 安全钳不过 → 清标记并返回原 segments
8. 记录完成日志并返回 segments

拆后复杂度：

```text
_detect_speaker_changes D(28) → B(10)
```

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 103.31s
```

### 涉及文件列表

- `v3/backend/transcriber_v2.py:222-368` — 新 helper + `_detect_speaker_changes()` 编排

## ⑤ 复杂度确认与后续断点 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📋 看记录  

### 结论（下一步只看这里）

B 组已完成。三个目标函数拆后复杂度：

```text
_detect_speaker_changes D(28) → B(10)
_assemble_qwen_primary D(25) → A(2)
_whisper_is_usable D(22) → B(9)
```

本轮未提交。下一步按 `pipelines/refactor-remaining-plan.md` 继续 D 组：

```text
D: v3/backend/main.py::create_job + v3/backend/core/sentence_splitter.py::llm_split_segments
```

四组 A/B/C/D 全部完成后，再跑调用图验证：

```text
cd v3 && ln-021-codegraph analyze_architecture
cd v3 && ln-021-codegraph audit_workspace
```
