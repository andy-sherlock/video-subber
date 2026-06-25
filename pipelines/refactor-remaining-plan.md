# 函数复杂度拆分 — 剩余规划（待拆名单 9 个）

> 这是一份**自包含**规划：新会话只读本文件就能直接开干，不必回看历史对话。
> 最后更新：2026-06-25

## 当前状态

- **基线**：全量测试 **624 passed**（`cd v3 && .venv/bin/python -m pytest test/ -q`）。
- **已拆完**：`worker.py` 的 `main()` **F(109) → C(15)**，抽出 12 个 helper，纯重构零行为变更。
  已提交 `79c9d1e`（本地 main，**未 push**）。详见 `pipelines/refactor-pipeline-worker-main-2026-06-23.md`。
- **已拆完**：对话 A `v3/backend/core/hallucination_filter.py` 四个目标已拆完，纯重构零行为变更，最终 **624 passed**。详见 `pipelines/refactor-pipeline-hallucination-filter-2026-06-25.md`。
- **本规划针对**：上个会话全扫 `v3/backend/` 发现的 10 个复杂度热点中，剩余未拆的是 B/C/D 三组。

## ⚠️ 重要前提

**这 9 个都是「代码绕、难维护」的质量优化，不是 bug，不拆也不影响功能。**
可以全拆完，也可以只拆最绕的几个 F 级就收手——**由用户决定，不是必做项**。

## 拆分策略：按文件分组，一个对话清一个文件

不要「9 个塞一个对话」（上下文必爆，质量降）；也不要「一个对话一个」（9 次太碎、反复重载浪费）。
**同一文件的函数共享上下文，一个对话一次读完该文件、顺手全清。** 9 个函数集中在 4 个文件 → **4 个对话**。

| 对话 | 文件 | 函数（radon 复杂度） | 个数 |
|------|------|---------------------|------|
| ✅ **A**（已完成） | `backend/core/hallucination_filter.py` | `LightweightStemmer` F(46)→B(8) / `clean_and_stem` F(45)→B(7) / `filter_hallucinations` D(24)→B(8) / `_extract_qwen_sentences` D(21)→A(4) | 4 |
| **B** | `backend/transcriber_v2.py` | `_detect_speaker_changes` D(28) / `_assemble_qwen_primary` D(25) / `_whisper_is_usable` D(22) | 3 |
| **C** | `backend/translation/orchestrator.py` | `_translate_whole_with_markers` F(44) | 1 |
| **D** | `backend/main.py` `create_job` D(25) + `backend/core/sentence_splitter.py` `llm_split_segments` D(23) | 两个小的凑一组 | 2 |

### 推荐顺序：A → C → B → D

- **A 已完成**：词干器、Qwen 句子提取、五层过滤主编排已拆清楚。
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

## 🔴 全部拆完后 — 调用图验证（和珅建议 · 2026-06-24）

**为什么**：拆出去十几个 helper，跨了 4 个文件。如果不跑 codegraph 查调用图，你不知道新抽出去的 helper 有没有被不合理地调用了太多次、或者某个 helper 设计歪了但后续拆分带着这个伤继续走。

**做什么**：
```
cd v3 && ln-021-codegraph analyze_architecture
```
重点看：
1. 新抽出去的 helper 调用频率 — 有没有被调上千次的（说明拆得对，是真正的公共函数）、有没有只被调一两次的（可能拆错了，应该塞回去）
2. 有没有新引入的循环依赖 — 拆 A 组函数时不小心让 B 组文件依赖了 A 组，形成不该有的耦合
3. `audit_workspace` — 拆完之后有没有遗留的 dead code（旧函数残留、import 残留）

**时机**：A/B/C/D 四组全部拆完、测试全绿之后，**停半天**，跑这个。确认调用图没问题了，才算真正收工。

---

## 进度勾选

- [x] 对话 A — hallucination_filter.py（4 个）
- [ ] 对话 B — transcriber_v2.py（3 个）
- [ ] 对话 C — orchestrator.py（1 个）
- [ ] 对话 D — main.py + sentence_splitter.py（2 个）
- [ ] 🔴 调用图验证 — codegraph analyze_architecture + audit_workspace
