# hallucination_filter 函数复杂度拆分 — 2026-06-25

## 元信息

- 涉及问题：V3.08 P219 复杂度拆分，对话 A
- 涉及文件：`v3/backend/core/hallucination_filter.py`
- 当前进度：对话 A 已完成，剩余 B/C/D 待拆
- 任务性质：纯重构，只搬代码，不改过滤规则、不改算法、不改对外函数名

## ① 恢复上下文与基线 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**读了什么**：
- `pipelines/refactor-remaining-plan.md:1-99`
- `task_plan.md:211-295`
- `progress.md` 末尾 V3.08 记录
- `v3/backend/core/hallucination_filter.py:1-807`

### 结论（下一步只看这里）

本轮原计划写的是 `backend/hallucination_filter.py`，真实活跃路径是 `v3/backend/core/hallucination_filter.py`。按真实路径执行。

拆前 radon：

```text
LightweightStemmer F(46)
LightweightStemmer.clean_and_stem F(45)
filter_hallucinations D(24)
_extract_qwen_sentences D(21)
```

沙盒内全量测试跑到 `259 passed, 1 skipped` 后卡在 Hugging Face/Metal 访问，退出时报 `No Metal device available`。随后用非沙盒权限重跑同一命令，基线确认：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 110.92s
```

### 涉及文件列表

- `v3/backend/core/hallucination_filter.py:15-144` — 词干器目标函数
- `v3/backend/core/hallucination_filter.py:423-495` — Qwen 句子提取目标函数
- `v3/backend/core/hallucination_filter.py:805-893` — 五层过滤主编排目标函数

## ② 拆 LightweightStemmer / clean_and_stem — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：① 结论 + `v3/backend/core/hallucination_filter.py:15-144`

### 结论（下一步只看这里）

把 `clean_and_stem()` 内部各语言分支搬成顶层 helper，保留原调用入口 `LightweightStemmer.clean_and_stem(text, lang)`：

- `_stem_chinese_text`
- `_stem_english_text`
- `_stem_english_word`
- `_strip_english_suffix`
- `_normalize_english_three_suffix_stem`
- `_stem_russian_text`
- `_strip_russian_suffix`
- `_stem_japanese_korean_text`

俄语后缀列表从函数内部搬到模块常量 `_RU_SUFFIXES` / `_RU_SHORT_SUFFIXES`。英文后缀判断顺序保持原顺序：4 字母后缀 → 3 字母后缀 → 2 字母后缀 → `es` → 单 `s`。

第一版拆完后 `_stem_english_word` 仍是 D(25)，继续把英文后缀规则拆小。最终 radon：

```text
LightweightStemmer B(8)
LightweightStemmer.clean_and_stem B(7)
_stem_english_word A(2)
_strip_english_suffix B(7)
```

每次拆完全量测试均通过：

```text
624 passed, 8 warnings
```

### 涉及文件列表

- `v3/backend/core/hallucination_filter.py:15-106` — 新增词干 helper
- `v3/backend/core/hallucination_filter.py:109-144` — `clean_and_stem()` 改为语言分发

## ③ 拆 _extract_qwen_sentences — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：① 结论 + `v3/backend/core/hallucination_filter.py:423-495`

### 结论（下一步只看这里）

把 `_extract_qwen_sentences()` 拆成两段：先从 Qwen chunk 中收集带时间戳的词，再按 `_L2_SENTENCE_SPLIT` 合成句子。

新增 helper：

- `_qwen_word_from_segment`
- `_extract_qwen_word_chunks`
- `_append_split_qwen_sentence`
- `_append_trailing_qwen_sentence`
- `_qwen_words_to_sentences`

保留旧行为：遇到句末标点时，仍然只把 `clean[0]` 作为句子文本写入；尾部无标点文本按原样追加。

radon：

```text
_extract_qwen_sentences D(21) → A(4)
```

验证：

```text
624 passed, 8 warnings in 213.76s
```

### 涉及文件列表

- `v3/backend/core/hallucination_filter.py:423-477` — 新增 Qwen 词块/句子 helper
- `v3/backend/core/hallucination_filter.py:480-495` — `_extract_qwen_sentences()` 变成编排函数

## ④ 拆 filter_hallucinations — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：① 结论 + `v3/backend/core/hallucination_filter.py:805-893`

### 结论（下一步只看这里）

把五层过滤主函数拆成阶段 helper，主函数只保留顺序编排：

- `_initialize_filter_state`
- `_effective_l1_alignment`
- `_apply_layer1`
- `_apply_layer2`
- `_apply_layer4`
- `_apply_layer3`
- `_renumber_segments`

过滤顺序保持不变：

```text
L1 → L2 → L4 → L5 → L3
```

细节保留：

- `skip_l1=True` 时不做 L1。
- Qwen chunk 带时间戳时仍走 `_qwen_segments_as_alignment()`，日志仍打印 `L1 使用 Qwen 内部 FA 时间戳`。
- `_apply_layer2()` 内仍先调用 `_qwen_is_reliable(qwen_chunks)`，再判断 `skip_l2`，保持旧执行顺序。
- 最终仍只返回 `filtered=False` 的段，并重新写 `id`。

radon：

```text
filter_hallucinations D(24) → B(8)
```

最终全文件 radon 不再有 D/F 级函数：

```text
_layer4_regex C(19)
_layer2_cross_validate C(18)
_build_smart_boundaries C(16)
_assign_by_similarity C(15)
_layer1_dual_signal C(14)
filter_hallucinations B(8)
LightweightStemmer B(8)
_extract_qwen_sentences A(4)
```

最终验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 108.20s
```

### 涉及文件列表

- `v3/backend/core/hallucination_filter.py:805-859` — 新增过滤阶段 helper
- `v3/backend/core/hallucination_filter.py:862-893` — `filter_hallucinations()` 变成编排函数

## ⑤ 后续断点

**执行人**：Codex  
**依赖类型**：📋 看记录  

### 结论（下一步只看这里）

对话 A 已完成。下一步按 `pipelines/refactor-remaining-plan.md` 推荐顺序做对话 C：

```text
v3/backend/translation/orchestrator.py::_translate_whole_with_markers F(44)
```

也可以按用户要求改做对话 B 或 D。A 组不需要继续拆。
