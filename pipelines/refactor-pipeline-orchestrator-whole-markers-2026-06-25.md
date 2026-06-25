# orchestrator _translate_whole_with_markers 复杂度拆分 — 2026-06-25

## 元信息

- 涉及问题：V3.08 P219 复杂度拆分，对话 C
- 涉及文件：`v3/backend/translation/orchestrator.py`
- 当前进度：①/进行中
- 任务性质：纯重构，只搬代码，不改翻译逻辑、不改分块规则、不改接缝审查规则、不改对外函数名

## ① 恢复上下文与基线 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**读了什么**：
- `pipelines/refactor-remaining-plan.md:1-99`
- `pipelines/refactor-pipeline-hallucination-filter-2026-06-25.md:1-199`
- `task_plan.md:1-260`
- `progress.md` 末尾 V3.08 记录
- `problems.md:1-120`
- `v3/backend/translation/orchestrator.py:520-705`
- `v3/test/test_harden_v307.py:86-101`
- `v3/test/test_arch9_dynamic_chunk.py:131-142`
- `v3/test/test_p272_seam_review_guard.py:1-52`

### 结论（下一步只看这里）

A 组已提交，当前 `HEAD` 是：

```text
29e3cee refactor(V3.08): split hallucination filter complexity
```

本轮目标拆前复杂度：

```text
backend/translation/orchestrator.py
    F 556:0 _translate_whole_with_markers - F (44)
```

拆前基线：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 116.62s
```

`_translate_whole_with_markers()` 当前干 6 件事：

1. 初始化进度、日志、`translated` 和 `non_empty`
2. 按 `_compute_chunk_size(llm)` 和 `_OVERLAP_RATIO` 建重叠 chunks
3. 把术语表和内容摘要拼进 prompt 前缀
4. 并发调用 `_translate_one_chunk()`，合并每个 chunk 的译文和 token
5. 对重叠区域调用 `_review_seam()` 做接缝审查
6. 收尾 `sub_splits`、统计成功条数、打进度和返回结果

直接相关测试里有源码文本检查：

- `test_arch9_dynamic_chunk.py:137-142` 检查 `_translate_whole_with_markers` 源码里包含 `_compute_chunk_size`
- `test_harden_v307.py:88-101` 检查 `_translate_whole_with_markers` 源码里不包含 `_CHUNK_SIZE` / `_OVERLAP_SIZE`

抽 helper 后第一条会误伤，应改成行为测试：通过 patch `_compute_chunk_size` 观察实际分块数量，而不是检查源码字符串。

### 涉及文件列表

- `v3/backend/translation/orchestrator.py:556-705` — 本轮目标函数
- `v3/test/test_arch9_dynamic_chunk.py:131-142` — 源码文本测试需改行为测试
- `v3/test/test_harden_v307.py:86-101` — 只检查旧死常量，预计不必改

## ② 抽初始化 / 分块 / prompt 前缀 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：① 结论 + `v3/backend/translation/orchestrator.py:556-620`

### 结论（下一步只看这里）

新增 3 个顶层 helper：

- `_initialize_whole_translation(texts)`：生成 `translated` 初始列表，并收集非空文本 `non_empty`
- `_build_overlapping_chunks(non_empty, llm)`：保留旧公式 `_compute_chunk_size(llm)` + `_OVERLAP_RATIO`
- `_build_whole_translation_prompt_prefix(terminology, context_summary)`：保留术语表最多 30 条、摘要日志和 prompt 拼接顺序

同时把 `test_arch9_dynamic_chunk.py` 里「源码里必须包含 `_compute_chunk_size`」改成行为测试：patch `_compute_chunk_size` 返回 3，再调用 `_translate_whole_with_markers()`，确认 7 条文本被分成 3 次 `translate_raw()` 调用。

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 101.10s
```

### 涉及文件列表

- `v3/backend/translation/orchestrator.py:556-615` — 新 helper 和主函数调用点
- `v3/test/test_arch9_dynamic_chunk.py:135-169` — 源码文本测试改为行为测试

## ③ 抽并发翻译块 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：② 结论 + `v3/backend/translation/orchestrator.py:620-642`

### 结论（下一步只看这里）

新增 `_translate_chunks_with_prompt()`，把线程池翻译 chunk 的逻辑整体搬出主函数。保留旧行为：

- `max_workers=min(total_chunks, 5)`
- 每个 future 仍调用 `_translate_one_chunk(chunks[ci], prompt_prefix, llm, ci, total_chunks)`
- `translated` 仍原地写入，仍保留 `threading.Lock()`
- token 仍通过 `_merge_tokens()` 累加
- 多 chunk 时仍按完成数回调 `LLM 翻译第 x/y 段…`

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 101.76s
```

### 涉及文件列表

- `v3/backend/translation/orchestrator.py:556-586` — 新增 `_translate_chunks_with_prompt()`
- `v3/backend/translation/orchestrator.py:636-638` — 主函数改为调用 helper

## ④ 抽接缝审查 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：③ 结论 + `v3/backend/translation/orchestrator.py:646-680`

### 结论（下一步只看这里）

新增 `_review_translation_seams()`，把多个 chunk 之间的重叠区审查整体搬出主函数。保留旧行为：

- 只有 `total_chunks > 1 and terminology is not None` 时才启用
- overlap 少于 3 条时跳过
- 仍用 `_review_seam(translated, overlap_start, overlap_end, version_a, version_b, terminology, llm)`
- 有审查结果时原地覆盖 `translated[idx]`
- token 仍用 `{"seam_reviews": 1}` 累加
- 进度仍是 82%「接缝审查中…」和 90%「接缝审查完成」

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 100.26s
```

### 涉及文件列表

- `v3/backend/translation/orchestrator.py:589-628` — 新增 `_review_translation_seams()`
- `v3/backend/translation/orchestrator.py:675-677` — 主函数改为调用 helper

## ⑤ 抽收尾统计并确认复杂度 — 2026-06-25

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：④ 结论 + `v3/backend/translation/orchestrator.py:682-705`

### 结论（下一步只看这里）

新增 `_finalize_whole_translation()`，把 `sub_splits` 收尾和成功条数统计搬出主函数。保留旧行为：

- `len(parts) >= 2` 时仍把 `translated[idx]` 改成 `" ".join(parts)`，并写入 `final_sub_splits`
- `len(parts) == 1` 且原翻译失败时，仍用唯一子句覆盖
- 仍用同一条日志输出预拆分数量
- `ok_count` 仍把空原文视为成功
- `ok_count == 0` 时仍在主函数里抛 `RuntimeError("LLM 整篇翻译失败：完全无法解析返回格式")`

拆后复杂度：

```text
_translate_whole_with_markers F(44) → B(10)
_finalize_whole_translation B(10)
_translate_chunks_with_prompt B(9)
_review_translation_seams B(9)
_build_overlapping_chunks A(4)
_build_whole_translation_prompt_prefix A(4)
_initialize_whole_translation A(3)
```

最终验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 98.63s
```

### 涉及文件列表

- `v3/backend/translation/orchestrator.py:556-758` — 新 helper + 主函数编排
- `v3/test/test_arch9_dynamic_chunk.py:135-169` — 行为测试覆盖动态分块

## ⑥ 后续断点

**执行人**：Codex  
**依赖类型**：📋 看记录  

### 结论（下一步只看这里）

对话 C 已完成。下一步按 `pipelines/refactor-remaining-plan.md` 继续 B 或 D：

```text
B: v3/backend/transcriber_v2.py::_detect_speaker_changes / _assemble_qwen_primary / _whisper_is_usable
D: v3/backend/main.py::create_job + v3/backend/core/sentence_splitter.py::llm_split_segments
```

本轮未提交。
