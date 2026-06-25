# main + sentence_splitter 复杂度拆分 — 2026-06-26

## 元信息

- 涉及问题：V3.08 P219 复杂度拆分，对话 D
- 涉及文件：`v3/backend/main.py`、`v3/backend/core/sentence_splitter.py`
- 当前进度：①/进行中
- 任务性质：纯重构，只搬代码，不改任务创建逻辑、不改断句规则、不改对外函数名

## ① 恢复上下文与基线 — 2026-06-26

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**读了什么**：
- `pipelines/refactor-remaining-plan.md:1-111`
- `pipelines/refactor-pipeline-orchestrator-whole-markers-2026-06-25.md:1-160`
- `pipelines/refactor-pipeline-transcriber-v2-2026-06-25.md:1-196`
- `task_plan.md:1-260`
- `progress.md:1-90`
- `problems.md:1-120`
- `v3/backend/main.py:623-778`
- `v3/backend/core/sentence_splitter.py:94-163`
- `v3/test/test_p188_url_validation.py:1-68`
- `v3/test/test_phase1c_llm_merge.py:153-214`
- `v3/test/test_phase1d_integration.py:50-65`

### 结论（下一步只看这里）

对话 C、对话 B 已完成但未提交，当前工作区已有这些前序改动，本轮必须保留，不回滚：

```text
M pipelines/refactor-remaining-plan.md
M progress.md
M task_plan.md
M v3/backend/transcriber_v2.py
M v3/backend/translation/orchestrator.py
M v3/test/test_arch9_dynamic_chunk.py
?? pipelines/refactor-pipeline-orchestrator-whole-markers-2026-06-25.md
?? pipelines/refactor-pipeline-transcriber-v2-2026-06-25.md
```

本轮 D 组目标复杂度：

```text
backend/main.py
    F 623:0 create_job - D (25)
backend/core/sentence_splitter.py
    F 94:0 llm_split_segments - D (23)
```

拆前基线：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 115.10s
```

两个目标函数当前职责：

1. `create_job()`：解析 JSON/URL/本地文件/上传文件三种输入，计算 hash，查重并恢复旧任务，写 `meta.json`，更新内存索引，启动 worker。
2. `llm_split_segments()`：优先应用翻译阶段传来的预拆分结果，再对剩余长字幕做规则断句；没有预拆分时，找出长字幕，按 LLM 或规则生成替换结果，再合并回完整列表。

直接覆盖测试：

- `test_p188_url_validation.py:33-68` — URL 协议校验和空 URL 本地路径错误。
- `test_phase1c_llm_merge.py:156-214` — `pre_splits` 应用、空字典/None、未预拆长字幕 fallback。
- `test_phase1d_integration.py:50-65` — `uncertain` 标记不能在 `llm_split_segments()` 中丢失。

### 涉及文件列表

- `v3/backend/main.py:623-778` — `create_job()` 目标函数
- `v3/backend/core/sentence_splitter.py:94-163` — `llm_split_segments()` 目标函数
- `v3/test/test_p188_url_validation.py:33-68` — URL 模式行为测试
- `v3/test/test_phase1c_llm_merge.py:156-214` — 断句行为测试
- `v3/test/test_phase1d_integration.py:50-65` — 不确定标记传递测试

## ② 拆 `llm_split_segments()` — 2026-06-26

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：① 结论 + `v3/backend/core/sentence_splitter.py:94-163`

### 结论（下一步只看这里）

新增 6 个顶层 helper，把 `llm_split_segments()` 拆成“预拆分路径”和“普通长字幕路径”：

- `_is_long_zh_segment()`：保留原判断条件：有中文、不是 `[翻译失败]`、长度超过 `_MAX_ZH_CHARS`。
- `_is_from_precomputed_split()`：保留原来的 `seg.id == orig * 100 + j` 判断，避免预拆过的子段再被规则断句。
- `_apply_precomputed_splits()`：保留 `pre_splits` 优先应用逻辑，并对未预拆的长字幕做规则 fallback。
- `_find_long_segment_indexes()`：收集普通路径里的长字幕下标。
- `_split_long_segments()`：保留有 `translate_raw` 就批量 LLM 断句，否则规则断句。
- `_replace_segments_with_splits()`：保留按原下标把替换段合并回结果列表。

主函数现在只保留调度：

```text
if pre_splits: return _apply_precomputed_splits(...)
long_segs = _find_long_segment_indexes(...)
if not long_segs: return segments
replacements = _split_long_segments(...)
return _replace_segments_with_splits(...)
```

拆后复杂度：

```text
llm_split_segments D(23) → A(3)
_apply_precomputed_splits B(7)
_split_long_segments A(5)
其余新 helper A(3)
```

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 111.77s
```

### 涉及文件列表

- `v3/backend/core/sentence_splitter.py:94-178` — 新 helper + `llm_split_segments()` 编排

## ③ 拆 `create_job()` — 2026-06-26

**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**前置阅读**：② 结论 + `v3/backend/main.py:623-778`

### 结论（下一步只看这里）

新增 9 个顶层 helper，把 `create_job()` 拆成“输入准备、已有任务处理、新任务启动”：

- `_normalize_job_config()`：保留原配置解析，并继续移除 `tier` / `local_path`。
- `_new_job_dir()`：保留 `uuid.uuid4().hex[:10]` 和 `JOBS_DIR / jid` 创建逻辑。
- `_prepare_url_job()`：保留 P188 http/https 校验、`source_url` 写入配置、URL hash、空 `video_path`。
- `_hash_local_file()`：保留本地文件 8MB 分块 SHA256。
- `_prepare_local_path_job()`：保留本地路径存在性检查、`source_dir`、读取失败清理 job 目录和 500 错误。
- `_prepare_json_job()`：保留 JSON body 路径，URL 优先，否则本地路径。
- `_prepare_upload_job()`：保留 multipart 路径、上传文件落盘、无文件 400 错误和上传失败清理。
- `_handle_existing_job()`：保留 stale index 清理、interrupted/error 恢复、done 复用、queued/running 复用。
- `_start_new_job()`：保留写 `meta.json`、更新两个内存索引、写 `JOBS`、启动 worker。

主函数现在只保留调度：

```text
prepared = await _prepare_json_job(...) or await _prepare_upload_job(...)
if prepared is JSONResponse: return prepared
tk = _task_key(...)
if existing: response = _handle_existing_job(...)
return _start_new_job(...)
```

拆后复杂度：

```text
create_job D(25) → B(6)
_handle_existing_job B(7)
_prepare_upload_job B(6)
_prepare_local_path_job A(5)
其余新 helper A(1-3)
```

验证：

```text
cd v3 && .venv/bin/python -m pytest test/ -q
624 passed, 8 warnings in 109.84s
```

### 涉及文件列表

- `v3/backend/main.py:622-844` — 新 helper + `create_job()` 编排

## ④ 复杂度确认与后续断点 — 2026-06-26

**执行人**：Codex  
**依赖类型**：📋 看记录

### 结论（下一步只看这里）

D 组已完成。两个目标函数拆后复杂度：

```text
create_job D(25) → B(6)
llm_split_segments D(23) → A(3)
```

A/B/C/D 四组现已全部完成。下一步按 `pipelines/refactor-remaining-plan.md` 跑调用图验证：

```text
cd v3 && ln-021-codegraph analyze_architecture
cd v3 && ln-021-codegraph audit_workspace
```

本轮未提交。
