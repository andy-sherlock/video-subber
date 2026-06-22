# 复杂度拆分流水线 — V3.08 余下 5 函数 — 2026-06-22

## 元信息
- 类型：纯重构（提取函数），只搬代码不改逻辑，逐字保留行为含日志
- 策略（用户拍板，乔布斯副审一致）：**每拆完一个函数跑一次 615 单元测试当安全网，绿了再拆下一个**；端到端真实视频测试留到 5 个全拆完统一做
- 目标函数（按复杂度高→低）：
  1. ✅ `run_sensevoice`（sensevoice_runner.py，C13→B6，615 passed）
  2. ✅ `_layer2_cross_validate`（hallucination_filter.py，D30→C18，615 passed）
  3. ✅ `run_qwen_asr`（qwen_asr_runner.py，C18→B8，615 passed；附带把 P170 源码文本测试改成行为测试）
  4. ⬜ `translate_with_orchestrator`（translation/orchestrator.py，约 17）
  5. ⬜ `build_ass_styled`（subtitle_style.py 或 subtitler，约 16）
- 安全网基线：615 passed（commit 1c4c4fa 后）
- 已提交起点：`1c4c4fa`（transcribe 拆分 + P297）

## 排查指南（出问题时怎么用这个文件）
每个函数一节，记录：改了哪个文件、抽出哪些 helper、改动行号、拆分前后复杂度、单元测试 passed 数。
若后续端到端测试发现行为变了，按「最后一个让测试仍全绿、但行为可能变的拆分」倒查——
每节的「行为保真要点」列出了该次拆分里最容易出错的等价性细节。

---

## ① run_sensevoice — 2026-06-22

**文件**：`v3/backend/core/sensevoice_runner.py`
**拆分前**：`run_sensevoice` radon C(13)，函数体 143-238（96 行）

### 拆分方案
原函数干 6 件事：chunked 判断 / 加载模型 / generate 推理 / 释放模型 / 解析 events / 统计标签。
抽 3 个 helper：
- `_load_sensevoice_model(device)` — import funasr + AutoModel 加载 + 两处异常 → model 或 None
- `_sensevoice_generate(model, audio_path)` — model.generate + 异常 + proc_time/n_items 日志 → res 或 None
- `_parse_sensevoice_results(res)` — 遍历 res 组装 events + 标签分布统计 → events
主函数只剩：chunked 判断 → 加载 → generate → del+gc → 空值分流 → 解析。

### 行为保真要点（最易错的等价性）
1. **del model 时机**：原代码成功/失败两条路径都 `del model; gc.collect()`。重构统一在主函数无条件 del，等价。
2. **"模型已释放"日志**：原代码 generate 失败（except 内 return）**不打**此日志，成功才打。主函数用 `del model; gc.collect()` 后 `if res is None: return None`（return 在日志前），严格保留——失败不打、成功打。
3. **返回值三态**：None（import/加载/generate 失败）、`[]`（res 为空）、events（成功）。逐一保留。

### 结果（下一步只看这里）
- 拆分后复杂度：`run_sensevoice` **C(13)→B(6)**；`_load_sensevoice_model` A(3)、`_sensevoice_generate` A(3)、`_parse_sensevoice_results` B(6)。全部 ≤6。
- 语法：py_compile 通过。
- **615 passed, 0 failed（94.03s）**。零退化。
- 行为保真三点（del 时机 / 失败不打"已释放"日志 / 返回值三态）已在代码中逐字保留。
- 改动位置：`sensevoice_runner.py:143-310`（3 helper 插在 run_sensevoice 之前，主函数精简为 chunked 判断→加载→generate→del→分流→解析）。
- ✅ **完成**

---

## ② _layer2_cross_validate — 2026-06-22

**文件**：`v3/backend/core/hallucination_filter.py`
**拆分前**：`_layer2_cross_validate` radon **D(30)**（比 office-hours 记的 19 高，搬家后代码变了），函数体 563-699

### 拆分方案
五层幻觉过滤的 L2（文本内容比对：滑窗 + 词干重叠）。抽 3 个 helper：
- `_l2_find_best_window(start, end, boundaries, num_windows)` — 原内部**闭包**提成模块级，加 boundaries/num_windows 参数。3 处调用全改。
- `_score_l2_window(w_text, q_text, language, win_has_bgm, current_threshold, min_stems)` — 单窗口词干重叠打分 + 5 种 status（EMPTY/NO_QWEN/LOW_DATA/GREEN/YELLOW）。
- `_annotate_l2_segments(segments, window_stats, boundaries, num_windows)` — 标注存活段 l2_status/uncertain。

### 行为保真要点（最易错）
1. **MIN_STEMS 引用陷阱**：原 MIN_STEMS 定义在打分循环内(629)，完成日志(694)也引用它。若把 MIN_STEMS 随打分逻辑带进 helper，主函数日志会 NameError。解法：MIN_STEMS=5 留主函数单一定义，作参数传进 `_score_l2_window`。
2. **win_has_bgm 计算时机**：原代码 EMPTY/NO_QWEN 窗口提前 continue、不算 win_has_bgm；重构移到主循环无条件算再传入。因它是纯读 segments 的 any()、无副作用、EMPTY/NO_QWEN 也不用它，结果等价（仅多算几次无害）。
3. **闭包→模块级**：`_find_best_window` 捕获 boundaries/num_windows，提取后显式传参，3 处调用逐字改。

### 结果（下一步只看这里）
- 复杂度：`_layer2_cross_validate` **D(30)→C(18)**；_score_l2_window B(9)、_annotate_l2_segments A(5)、_l2_find_best_window A(3)。
- 说明：主函数 C(18) 未到 ≤10——L2 内在是「窗口化循环比对」（填窗/逐窗/标注三循环），非线性编排，18 是合理值；已抽走最复杂的打分逻辑。
- 验证：L2 相关 38 测试 0.04s 全过；**全量 615 passed, 0 failed（104.51s）**。零退化。
- 改动位置：`hallucination_filter.py:563-740`（3 helper 插在 _layer2 之前）。
- ✅ **完成**

---

## ③ run_qwen_asr — 2026-06-22

**文件**：`v3/backend/core/qwen_asr_runner.py`
**拆分前**：`run_qwen_asr` radon C(18)，函数体 365-450

### 拆分方案
抽 2 个 helper：
- `_resolve_qwen_duration(audio_path, segments)` — 拿总时长（segments 模式取末段 end / 独立模式 ffprobe）；空 segments 或 ffprobe 失败返回 None。
- `_qwen_transcribe(model, audio_path, language)` — MLX/PyTorch 两后端转录 + 异常；失败返回 None，成功返回 (full_text, ts_segments)。

### 行为保真要点
1. **del model 时机**：原代码转录成功/失败两路径都 del model；重构统一在主函数无条件 del。等价。
2. **空 segments / ffprobe 失败**：原 return []（segments 空无日志、ffprobe 失败打 warning）；重构 helper 返回 None → 主函数 return []。日志行为保留。

### ⚠️ 测试抓到 1 个失败（这正是每步跑测试的价值）
- 失败：`test_harden_v307.py::TestP170EmptyListGuard::test_function_has_empty_list_check`
- 根因：**源码文本检查测试**——`inspect.getsource(run_qwen_asr)` 断言含 `if not segments`。拆分把空守卫挪进 `_resolve_qwen_duration`，run_qwen_asr 函数体不再有那行文本 → 测试失败。**功能没坏**（空 segments 仍返回 []）。
- 处理（用户选「改成行为测试」）：改成 `test_empty_segments_returns_empty`——mock is_qwen_asr_available=True，调 `run_qwen_asr("dummy.wav", segments=[])` 断言返回 []。重构挪守卫位置不会再误伤。
- 教训：源码文本检查测试（`inspect.getsource` + assertIn）天生与重构冲突，每次抽函数都会炸。后续再遇到同类一律优先改行为测试。

### 结果（下一步只看这里）
- 复杂度：`run_qwen_asr` **C(18)→B(8)**；_qwen_transcribe B(10)、_resolve_qwen_duration A(4)。
- 验证：test_harden_v307 8 测试 4.16s 全过；**全量 615 passed, 0 failed（102.32s）**。零退化。
- 改动文件：`qwen_asr_runner.py:365-462`（2 helper）+ `test/test_harden_v307.py:31-44`（源码文本测试 → 行为测试）。
- ✅ **完成**

---

## ④ translate_with_orchestrator — 2026-06-22 ✅
**文件**：orchestrator.py，**F(61)**，710-920（211 行），本轮最大。
**方案**：抽 3 个顶层 helper——_translate_llm_context_aware / _translate_llm_per_batch / _translate_free_only，各返回 4 元组。主函数只剩准备 backends + 三分支调用。
**保真要点**：三分支返回值都是 4 元组 (translated, failed_indices, token_usage, sub_splits) 逐字保留；context_aware 用 ordered_backends，free_only 用 ordered_backends+mode，per_batch 都不用。

### 执行（分两波，每波跑全量测试）
- **WAVE 1**：抽 _translate_llm_per_batch（else 分支）+ _translate_free_only（免费引擎分支）。先替换主函数分支为调用（趁原文唯一），再插 helper 定义。主函数 F(61)→F(43)。**615 passed**。
- **WAVE 2**：context_aware 分支内部细拆，再抽 4 个 helper：
  - `_run_llm_fallback_chain` C(17)：降级链 for 循环（init+loop+post-loop status），返回 6 元组 (translated, failed_indices, token_usage, sub_splits, last_llm, llm_produced)
  - `_retry_failed_with_llm` B(9)：P230 单段重试，guard 内置，原地改 translated/failed_indices
  - `_fill_gaps_with_free` B(7)：免费引擎补全，guard 内置，原地改 translated
  - `_translate_llm_context_aware` B(7)：编排（chain → retry → fill → final_failed + 收尾日志）

### 行为保真要点（最易错）
1. **translated 三态**：循环后 translated 可能是 None（全 P165/异常）、全失败列表（最后一个 backend 全失败未 break）、或部分成功列表。llm_produced = (translated is not None)，所以"最后一个全失败"也算 llm_produced=True。`_run_llm_fallback_chain` 原样返回，逐字保留。
2. **failed_indices 同一对象贯穿**：chain 返回的 failed_indices 列表对象，先传给 retry（原地 .remove 删项），再传给 fill（读同一对象）。三个 helper 共享同一 list 引用，mutation 贯穿——和原内联代码等价。
3. **retry/fill 的 guard 内置**：原 `if failed_indices and last_llm and len<=10` / `if failed_indices and ordered_backends` 守卫整体搬进 helper，调用方无条件调，等价。

### ⚠️ 测试抓到 1 个失败（又是源码文本测试，同 ③ P170）
- 失败：`test_harden_v307_sprint2.py::TestP241RetryLanguage::test_uses_target_language_variable`
- 根因：`inspect.getsource(translate_with_orchestrator)` 断言含 `_TARGET_LANGUAGE`。P230 重试 prompt 搬进 `_retry_failed_with_llm` 后，主函数源码文本不再有这词 → 测试失败。**功能没坏**（重试 prompt 照样用 _TARGET_LANGUAGE）。
- 处理（按 ③ 已确立的"源码文本测试一律改行为测试"）：`TestP241RetryLanguage` 两个方法改成行为测试——mock 假 LLM 调 `_retry_failed_with_llm`，捕获实际 prompt，断言含 `_TARGET_LANGUAGE`、不含硬编码"将以下英文翻译成中文"。survive 未来重构。

### 结果（下一步只看这里）
- 复杂度：`translate_with_orchestrator` **F(61)→B(7)**；新 helper 全部 ≤17（_run_llm_fallback_chain C17 是降级链循环固有复杂度，同 ② L2 留 C18 的道理，已把 retry/fill 抽走）。
- 主函数现在只剩：默认 priority → 组 ordered_backends → 组 llm_backends → if llm_backends:(context_aware? 调 context_aware : 调 per_batch) else 调 free_only。
- 验证：TestP241 行为测试 2 passed 0.03s；**全量 615 passed, 0 failed（99.38s）**。零退化。
- 改动文件：`orchestrator.py:708-930`（新增 6 个 helper，主函数精简为三分支调度）+ `test/test_harden_v307_sprint2.py:161-192`（源码文本测试 → 行为测试）。
- ✅ **完成**

---

## ⑤ build_ass_styled — 2026-06-22 ✅（核实后判定：不拆）
**文件**：`v3/backend/subtitle_style.py:13`
**预估**：约 16（office-hours 旧数据，搬家前）
**实测**：radon **B(6)**，函数体仅 62 行（13-74），从头到尾一条直路（解析 style → _collect_entries/_annotate_speaker_changes → 写 ASS 文件三段）。
**判定**：**不拆**。radon 健康区间 A=1-5、B=6-10，B(6) 已属低复杂度。给已简单的函数硬抽 helper 只增加间接层、不降复杂度，违背「纯重构只为降复杂度」初衷（Karpathy Simplicity First / Surgical Changes）。
**附带核实**：subtitler.py 里 build_ass_styled 的三个辅助函数（_collect_entries/_annotate_speaker_changes/_to_ass_time）均 A/B，无需动；该文件唯一 C 级是 `_split_zh_into_sentences` C(14)，本轮不在范围。
- ✅ **本轮「余下 5 函数拆分」收官**：①②③（已提交 3258d40）+ ④（本会话，待提交）真拆；⑤ 核实判定不拆。

---

## 📋 待拆名单（下一轮，单独开会话）— 2026-06-22 扫描
本会话末扫全 `v3/backend/` 发现的真·复杂度热点（D 级及以上，均**不在**原「5 函数」清单内，是新发现）。
**为何留新会话**：worker.main F(109) 体量超大（比 transcribe 的 41 还大 2.5 倍），需占满一整块上下文，硬塞进长会话易切错。按 transcribe 先例单独开干净会话拆。

| 函数 | 文件 | 复杂度 | 备注 |
|------|------|--------|------|
| `main` | worker.py:408 | **F(109)** 🔥 | 头号目标，单独一会话 |
| `LightweightStemmer` / `.clean_and_stem` | hallucination_filter.py:15/28 | F(46)/F(45) | 类+方法，词干器 |
| `_translate_whole_with_markers` | orchestrator.py:556 | F(44) | 滑窗翻译 |
| `_detect_speaker_changes` | transcriber_v2.py:222 | D(28) | |
| `_assemble_qwen_primary` | transcriber_v2.py:657 | D(25) | |
| `create_job` | main.py:613 | D(25) | |
| `filter_hallucinations` | hallucination_filter.py:740 | D(24) | |
| `llm_split_segments` | sentence_splitter.py:94 | D(23) | |
| `_whisper_is_usable` | transcriber_v2.py:487 | D(22) | |
| `_extract_qwen_sentences` | hallucination_filter.py:384 | D(21) | |

### 下一会话开场话术（复制粘贴用，省 token）
```
继续 video-subtitle V3.08 函数复杂度拆分，新会话。
项目根 /Users/andy/Andy-agent/video-subtitle/，活跃代码在 v3/。

本轮拆 worker.py 的 main 函数（radon F109，全项目最绕，比已拆的 transcribe 41 还大）。
纯重构：只搬代码不改逻辑，逐字保留行为含日志。

开工先做两件事，别漫无目的探索：
1. 读 pipelines/refactor-pipeline-2026-06-22-remaining5.md 末尾「待拆名单」节恢复上下文
2. cd v3 && .venv/bin/python -m radon cc backend/worker.py -s 看 main 当前复杂度，再完整读 main 函数体

拆分方法（和已拆 4 个一致）：
- 找出 main 干的几件大事 → 每件抽成一个顶层 helper → 主函数只剩编排调度
- 小步拆，每抽完一步跑一次全量测试当安全网：
  后台跑 cd v3 && .venv/bin/python -m pytest test/ -q > /tmp/t.log 2>&1，绿了(615 passed)再下一步
- 替换分支时「先替换主函数为调用(趁原文唯一)、再插 helper 定义」，避免重复文本破坏 Edit 匹配
- Edit 拆小步，别发 100+ 行的超大 Edit(会触发工具调用解析退化)
- bash 命令保持极简，别用管道正则
- 警惕源码文本测试(inspect.getsource + assertIn)：抽函数后会误伤，按先例改成行为测试
- 全程记录到新管道文件 pipelines/refactor-pipeline-worker-main-<日期>.md

基线：当前 615 测试全绿。④ translate_with_orchestrator 已拆(F61→B7)，
若工作区未提交先确认 git 状态。拆完 main 再按复杂度依次处理待拆名单其余 D/F 函数。
```
> 话术也存在此处，下个会话读本文件即可，不必回看本对话。
