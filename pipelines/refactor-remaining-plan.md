# 函数复杂度拆分 — 剩余规划（待拆名单 9 个）

> 这是一份**自包含**规划：新会话只读本文件就能直接开干，不必回看历史对话。
> 最后更新：2026-06-23

## 当前状态

- **基线**：全量测试 **624 passed**（`cd v3 && .venv/bin/python -m pytest test/ -q`）。
- **已拆完**：`worker.py` 的 `main()` **F(109) → C(15)**，抽出 12 个 helper，纯重构零行为变更。
  已提交 `79c9d1e`（本地 main，**未 push**）。详见 `pipelines/refactor-pipeline-worker-main-2026-06-23.md`。
- **本规划针对**：上个会话全扫 `v3/backend/` 发现的 10 个复杂度热点中，**剩下的 9 个**（main 是第 1 个，已拆）。

## ⚠️ 重要前提

**这 9 个都是「代码绕、难维护」的质量优化，不是 bug，不拆也不影响功能。**
可以全拆完，也可以只拆最绕的几个 F 级就收手——**由用户决定，不是必做项**。

## 拆分策略：按文件分组，一个对话清一个文件

不要「9 个塞一个对话」（上下文必爆，质量降）；也不要「一个对话一个」（9 次太碎、反复重载浪费）。
**同一文件的函数共享上下文，一个对话一次读完该文件、顺手全清。** 9 个函数集中在 4 个文件 → **4 个对话**。

| 对话 | 文件 | 函数（radon 复杂度） | 个数 |
|------|------|---------------------|------|
| **A**（推荐先做） | `backend/hallucination_filter.py` | `LightweightStemmer` F(46) / `clean_and_stem` F(45) / `filter_hallucinations` D(24) / `_extract_qwen_sentences` D(21) | 4 |
| **B** | `backend/transcriber_v2.py` | `_detect_speaker_changes` D(28) / `_assemble_qwen_primary` D(25) / `_whisper_is_usable` D(22) | 3 |
| **C** | `backend/translation/orchestrator.py` | `_translate_whole_with_markers` F(44) | 1 |
| **D** | `backend/main.py` `create_job` D(25) + `backend/core/sentence_splitter.py` `llm_split_segments` D(23) | 两个小的凑一组 | 2 |

### 推荐顺序：A → C → B → D

- **A 先做**：含两个最绕的 F 级（词干器），收益最大；词干器是字幕"幻觉过滤"核心（判断模型有没有把没说的话也转出来），拆清楚最值。
- **C 次之**：orchestrator F44 是滑窗翻译主逻辑，单文件单函数，干净。
- **B、D**：D 级，体量较小，靠后。D 组两个函数在两个文件，是收尾的零头。

## 统一拆法（和已拆 main/transcribe 一致）

1. 找出目标函数干的几件大事 → 每件抽一个顶层 helper → 主函数只剩编排调度。
2. 小步拆，**每抽完一步后台跑全量测试当安全网**：
   `cd v3 && .venv/bin/python -m pytest test/ -q > /tmp/t.log 2>&1`，绿了（**624 passed**）再下一步。
3. 替换时**先替换调用处（趁原文唯一）、再插 helper 定义**，避免重复文本破坏 Edit 匹配。
4. Edit 拆小步，别发 100+ 行的超大 Edit。
5. **警惕源码文本测试**（`inspect.getsource` + `assertIn`）：抽函数后会误伤，按先例改成行为测试。
6. 全程记录到新管道文件 `pipelines/refactor-<目标文件>-<日期>.md`。
7. 拆完更新本文件（划掉做完的组）+ `progress.md` + `task_plan.md` + 全局 `task_plan.md`。

## 各组开场话术（复制粘贴用）

**对话 A：**
```
继续 video-subtitle V3.08 函数复杂度拆分，新会话。项目根 /Users/andy/Andy-agent/video-subtitle/，活跃代码 v3/。
先读 pipelines/refactor-remaining-plan.md 恢复上下文。
本轮拆对话 A：backend/hallucination_filter.py 的 LightweightStemmer(F46)/clean_and_stem(F45)/filter_hallucinations(D24)/_extract_qwen_sentences(D21)。
纯重构，只搬代码不改逻辑。开工先 cd v3 && .venv/bin/python -m radon cc backend/hallucination_filter.py -s 看复杂度，再读目标函数体。
拆法见 refactor-remaining-plan.md「统一拆法」。每抽一步跑全量测试，绿了(624 passed)再下一步。基线 624 全绿，main 已拆完并提交 79c9d1e。
```

**对话 B：** 同上模板，目标换成 `backend/transcriber_v2.py` 的 `_detect_speaker_changes`/`_assemble_qwen_primary`/`_whisper_is_usable`。

**对话 C：** 同上模板，目标换成 `backend/translation/orchestrator.py` 的 `_translate_whole_with_markers`。

**对话 D：** 同上模板，目标换成 `backend/main.py` 的 `create_job` + `backend/core/sentence_splitter.py` 的 `llm_split_segments`。

## 进度勾选

- [ ] 对话 A — hallucination_filter.py（4 个）
- [ ] 对话 B — transcriber_v2.py（3 个）
- [ ] 对话 C — orchestrator.py（1 个）
- [ ] 对话 D — main.py + sentence_splitter.py（2 个）
