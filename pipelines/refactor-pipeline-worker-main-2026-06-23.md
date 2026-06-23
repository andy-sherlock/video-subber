# 重构流水线 — worker.main 拆分 — 2026-06-23

## 元信息
- 目标函数：`v3/backend/worker.py` `main()`（408–921，约 514 行）
- 起始复杂度：radon **F(109)**，全项目最绕（比已拆 transcribe 的 41 大 2.5 倍）
- 性质：纯重构，只搬代码不改逻辑，逐字保留行为含日志
- 基线：HEAD=3dc7f0c，工作区干净，全量测试待确认（交接称 615，全局摘要称 621/624，本轮先实跑核实）
- 方法：和已拆 4 个一致——找出 main 干的几件大事 → 每件抽顶层 helper → 主函数只剩编排调度
- 安全网：每抽一步后台跑 `cd v3 && .venv/bin/python -m pytest test/ -q`，绿了再下一步

## main 干的几件大事（逐块）

| # | 块 | 行号 | 复杂度估算 | 拆法 |
|---|----|------|-----------|------|
| 1 | 入参/配置加载/日志/版本 | 408–432 | 低 | 留 main |
| 2 | 配置字段提取 + tier/outputMode 归一 | 433–451 | 中 | → `_extract_settings` |
| 3 | 配置校验（嵌套 `_validate_config`） | 453–469 | ~8 | 提为模块级 `_validate_config` |
| 4 | URL 下载 | 475–502 | ~4 | → `_download_source_if_url` |
| 5 | 缓存路径 + STAGE 表 | 504–548 | 低 | → `_compute_paths` / `_build_stage_cache` |
| 6 | checkpoint 加载 + 配置变更失效缓存 | 550–585 | ~10 | → `_invalidate_stale_caches` |
| 7 | Step1 抽音频 | 588–600 | 低 | → `_step_extract_audio` |
| 8 | Step2 转录 + 加载缓存 | 602–647 | ~6 | → `_step_transcribe` |
| 9 | P291 字幕润色 | 649–668 | ~4 | → `_run_transcript_refiner` |
| 10 | Step3 翻译（含降级/反思/质量闸） | 670–812 | ~25 🔥 | → `_step_translate`（必要时再拆子 helper） |
| 11 | Step4 字幕 | 814–848 | ~6 | → `_step_subtitle` |
| 12 | Step5 烧录/封装 | 850–867 | ~6 | → `_step_burn_or_mux` |
| 13 | 保存产物 + emit done | 869–911 | ~8 | → `_save_outputs` |
| 14 | 异常处理 | 913–920 | 低 | 留 main |

## 拆分顺序（小步，每步一跑全量测试）

先做高复杂度、低耦合的块，做完再 radon 复测决定是否继续往下拆。

- [x] S0 确认基线绿（624 passed）
- [x] S1 提升 `_validate_config` 为模块级
- [x] S2 抽 `_invalidate_stale_caches`
- [x] S3 抽 `_download_source_if_url`
- [~] S4 settings 提取 — **刻意跳过**（直筒赋值，抽出 churn 高收益低）
- [x] S6 抽 `_step_extract_audio`
- [x] S7 抽 `_step_transcribe`
- [x] S8 抽 `_run_transcript_refiner`
- [x] S9a/b/c 抽 `_llm_fallback_if_needed` / `_reflection_review` / `_step_translate`
- [x] S10 抽 `_step_subtitle`
- [x] S11 抽 `_step_burn_or_mux`
- [x] S12 抽 `_copy_outputs_to_save_path`
- [x] 收尾：radon 复测 **main F(109) → C(15)**，已记录

## 注意事项（继承先例）
- 替换分支时先替换主函数为调用（趁原文唯一），再插 helper 定义，避免重复文本破坏 Edit 匹配
- Edit 拆小步，别发 100+ 行超大 Edit
- 警惕源码文本测试（inspect.getsource + assertIn）：抽函数后会误伤，按先例改成行为测试
- `current_step` / `_real_stdout` / `_log` / `emit` / `stderr_progress` 是模块级全局/函数，helper 直接引用；改 `current_step` 的 helper 内加 `global current_step`

## 执行记录（追加，不改历史）

**基线**：HEAD=3dc7f0c，工作区干净，全量 **624 passed**（交接说 615 是旧数，已核实为 624）。

每步均「先替换 main 为调用 → 再插模块级 helper」，每批后台跑全量测试，绿了才下一步：

| 步 | 抽出的 helper | 测试 | 备注 |
|----|--------------|------|------|
| S1 | `_validate_config`（嵌套→模块级） | 624 ✅ | 纯提升，零闭包 |
| S2 | `_invalidate_stale_caches` | 624 ✅ | `if old_config` 改早返回 |
| S3 | `_download_source_if_url` | 624 ✅ | `if source_url` 改早返回 |
| S6 | `_step_extract_audio` | （并入 S6-8 批） | 返回 (w,h) |
| S7 | `_step_transcribe` | （并入 S6-8 批） | 返回 segments，`current_step` 留 main |
| S8 | `_run_transcript_refiner` | 624 ✅（S6-8 一起） | 就地改 segments |
| S9a | `_llm_fallback_if_needed` | （并入 S9ab 批） | 返回 (zh_list, failed, sub_splits) |
| S9b | `_reflection_review` | 624 ✅（S9ab 一起） | 返回 failed |
| S9c | `_step_translate` 外壳 | 624 ✅ | 返回 (failed, sub_splits, ok_count)，含质量闸 raise |
| S10 | `_step_subtitle` | （并入 S10-12 批） | burn 模式排版 ass |
| S11 | `_step_burn_or_mux` | （并入 S10-12 批） | burn/mux 分支 |
| S12 | `_copy_outputs_to_save_path` | 624 ✅（S10-12 一起） | 拷贝+归档旧文件 |

**S4（settings 提取）刻意跳过**：那块基本直筒赋值、只 2 个分支，抽出去要把 main 里十几个变量全改 `settings.xxx`，高 churn 低收益。

### 结论（下一步只看这里）
- **`main` 复杂度：F(109) → C(15)**（radon），7 倍下降。全文件不再有任何 F/D 级函数。
- 共抽出 **12 个顶层 helper**（11 新 + 1 提升），main 变成纯编排：Step1…Step5 各一行调用，`current_step` 步骤标记可见。
- 行为逐字保留（含日志、进度、emit、异常路径）。全程 **624 passed 零回归**，5 个测试批次全绿。
- 抽出后各 helper 复杂度（radon）：`_step_translate` C(19)、`_invalidate_stale_caches` C(14)、`_llm_fallback_if_needed` C(14)、`_copy_outputs_to_save_path` C(13)、`_reflection_review` C(12)、`_step_subtitle` B(8)、`_step_transcribe` B(7)，其余 ≤B。`_step_translate` 是翻译步骤本身的内聚分支，远低于 F，不再碎拆。
- 未提交（待用户确认或进发布线）。

### 待拆名单剩余（下一会话，按复杂度）
worker.main 已收官。剩下（均为独立大函数，建议各开干净会话，照 transcribe/main 先例）：
`_translate_whole_with_markers` orchestrator.py F(44) → `LightweightStemmer`/`clean_and_stem` hallucination_filter.py F(46)/F(45) → 其余 D 级（transcriber_v2 的 `_detect_speaker_changes` D28、`_assemble_qwen_primary` D25、`_whisper_is_usable` D22；main.py `create_job` D25；hallucination_filter `filter_hallucinations` D24、`_extract_qwen_sentences` D21；sentence_splitter `llm_split_segments` D23）。

> 用户已选「下一个开新会话拆」（2026-06-23）。

### 下一会话开场话术（复制粘贴用）
```
继续 video-subtitle V3.08 函数复杂度拆分，新会话。
项目根 /Users/andy/Andy-agent/video-subtitle/，活跃代码在 v3/。

本轮拆 hallucination_filter.py 的 LightweightStemmer 词干器（radon F46/F45，待拆名单里最绕的）
或 orchestrator.py 的 _translate_whole_with_markers（F44）——二选一，先做哪个你说了算。
纯重构：只搬代码不改逻辑，逐字保留行为含日志。

开工先做两件事：
1. 读 pipelines/refactor-pipeline-worker-main-2026-06-23.md 末尾「待拆名单剩余」节恢复上下文
2. cd v3 && .venv/bin/python -m radon cc backend/<目标文件> -s 看目标当前复杂度，再完整读目标函数体

拆法（和已拆 main/transcribe 一致）：
- 找出函数干的几件大事 → 每件抽顶层 helper → 主函数只剩编排
- 小步拆，每抽完一步后台跑全量测试当安全网：
  cd v3 && .venv/bin/python -m pytest test/ -q > /tmp/t.log 2>&1，绿了(624 passed)再下一步
- 「先替换调用处(趁原文唯一)、再插 helper 定义」，避免重复文本破坏 Edit 匹配
- 警惕源码文本测试(inspect.getsource + assertIn)，抽函数后按先例改成行为测试
- 全程记录到新管道文件 pipelines/refactor-pipeline-<目标>-<日期>.md

基线：当前 624 测试全绿。worker.main 已拆完(F109→C15，本文件有完整记录)，未提交。
若工作区未提交先确认 git 状态。
```
