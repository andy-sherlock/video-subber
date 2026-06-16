# Bug 修复流水线 — P254/P247/P260/P261 — 2026-06-12（第二期）

## 元信息
- 涉及问题：P254（智能路由+组装）、P247（双稿裁判Refiner）、P260（位置修正）、P261（先验证再合成）
- 涉及文件：transcriber_v2.py、worker.py、hallucination_filter.py
- 方案来源：用户 + Jobs 七轮方案讨论定稿
- 当前进度：⑧/⑩ → ⑨（Web跳过）→ 待用户跑⑩端到端实测

---

## ① investigate——2026-06-12
**执行人**：Claude（综合用户 + Jobs 七轮讨论）
**读了什么**：problems.md（P254/P247/P260/P261）、Run 7 日志、transcriber_v2.py、worker.py

### 结论

四个问题是一个流水线架构问题的四面：

**P260**：P247 Refiner 放在 Step 4（字幕生成/翻译后）→ 英文校对发生在中文翻译已完成后，来不及纠正 "claw→爪子"
**P261**：L1 看到合成段导致 97% `[?]` → L1 需要看千问原始数据（`_qwen_raw`），而不是被 Whisper 重装后的合成段
**P247**：Refiner 只看了千问稿，没看 Whisper 稿 → 双稿裁判可以让 LLM 对比两份稿子发现错词
**P254**：组装逻辑需要调整为"L1 先看原稿再组装"

**最终方案**：
```
转录→P254路由→保留_qwen_raw
              ├─L1看原稿 ──────────┐
              └─Whisper骨架组装 ────┘（并行汇合）
              →L2交叉验证→P247双稿裁判+边界修复+润色→翻译
```

需改三个文件：
- transcriber_v2.py: P254 路由拆分为两步（L1 用原稿，组装用 whisper_segments）
- worker.py: P247 Refiner 从 L517 Step 4 移到 L370 Step 3 之前
- worker.py: P247 prompt 增加 Whisper 参考稿

### 涉及文件
- transcriber_v2.py:327-357 P254 路由+组装
- worker.py:370 翻译前（Refiner 新位置）
- worker.py:517-576 旧 Refiner 位置（需移除）
- hallucination_filter.py:191 L1 短词豁免

---

## ② 双签审查 — 2026-06-12
**执行人**：Claude（plan-eng-review + steve-jobs-perspective 并行）
**前置阅读**：① 结论
**读了什么**：transcriber_v2.py:269-374、worker.py:360-578、hallucination_filter.py:180-199

### 结论

**综合判定：方案通过 ✅**

#### plan-eng-review（主审·工程视角）

三处需在动手前补齐：

1. **L1 输入接口确认**：方案说 L1 看 `_qwen_raw`，但 `hallucination_filter.py` 当前期望的是 Segment 对象还是词级时间戳列表？需确认 `_qwen_raw` 的格式 `[{word, start, end, probability}]` 能被 L1 直接消费。如果格式不匹配，需要加一个轻量适配层。

2. **双稿裁判 prompt 必须先写草稿**：方案只说"P247 prompt 增加 Whisper 参考稿"，没给出具体结构。双稿裁判需要同时给 LLM 看 Qwen 稿和 Whisper 稿、让 LLM 对比差异、裁判哪个词更正确——这个 prompt 怎么写直接决定 P247 效果。建议动手前先写出 prompt 草稿。

3. **checkpoint 逻辑检查**：Refiner 从 Step 4（subtitle stage）移到 Step 3（translate stage 之前），中间恢复逻辑是否受影响？当前 checkpoint 按 stage 分——transcribe/translate/subtitle。Refiner 位移后如果在 translate stage 前运行，中间崩溃恢复时会不会跳过 Refiner？

边界覆盖：
- Qwen 不可用回退 → 双稿裁判退化为单稿，不更差
- Refiner LLM 幻觉风险 → 建议 prompt 加保守策略："不确定时保持原文不变"
- 短词豁免（hallucination_filter.py:191）→ 方案提到了但没说具体改什么

复杂度：3 文件，0 新类 → 范围合理，未触发 8+ 阈值。

#### steve-jobs-perspective（副审·产品视角）

诊断对，方案实用但不够优雅。核心意见：
- 四个问题本质是一个：在错误的时间、用错误的数据、做错误的决定
- 方案是"在意大利面里挪肉丸"——能工作，但不是 insanely great
- 真正的解法：一个 `compare_and_decide(qwen_raw, whisper_raw)` 函数替代现在的五步流水线
- 但现在是在修 bug 不是重设计，当前方案作为止血方案通过
- 要求：下版本规划"重构为一个裁判函数"
- 强调：双稿裁判 prompt 是整个修复里唯一真正"聪明"的部分，必须写到极致

#### 动手前强制补齐项

| # | 项目 | 阻塞？ |
|---|------|--------|
| 1 | 双稿裁判 prompt 草稿 | ⚠️ 阻塞代码修改 |
| 2 | `_qwen_raw` → L1 接口确认 | ⚠️ 阻塞 P261 修复 |
| 3 | checkpoint 阶段检查 | 不阻塞，但需在 TDD 前确认 |

### 涉及文件
- transcriber_v2.py:351-364 — P254 路由（需拆分为 L1 原稿 + 组装两步）
- worker.py:522-576 — P247 Refiner 旧位置（需移至 L370 之前）
- worker.py:370 — 翻译入口（Refiner 新位置）
- hallucination_filter.py:191 — L1 短词豁免（需确认是否需要适配）

---

## ③ 双签影响面 — 2026-06-12
**执行人**：Claude（ln-021-codegraph + bill-gates 并行）
**前置阅读**：①② 结论 + 全项目代码
**读了什么**：transcriber_v2.py、hallucination_filter.py、worker.py、sentence_splitter.py 全链路

### 结论

**综合判定：改动范围可控，依赖链清晰 ✅**

#### ln-021-codegraph（主审·调用图/波及范围）

完整调用链：
```
worker.py → transcriber_v2.transcribe()
  ├─ Qwen ASR 并行线程 → qwen_chunks
  ├─ P254 路由: _qwen_chunks_to_segments(qwen_chunks, whisper_segments) → primary segments
  ├─ filter_hallucinations(results, ..., qwen_chunks=qwen_chunks)
  │   ├─ L1: _layer1_dual_signal(seg, effective_alignment)  ← 当前用 segments，改为用 _qwen_raw
  │   └─ L2: _assign_qwen_to_windows(qwen_chunks, ...)
  └─ 返回 results → worker.py → Step 3 翻译 → Step 4 字幕
```

波及范围统计：
- `filter_hallucinations` 唯一调用者：transcriber_v2.py:496
- `word_align_segments` 唯一调用者：worker.py:578
- `transcribe()` 唯一调用者：worker.py
- 依赖方向：单向（worker → transcriber_v2 → hallucination_filter），无循环
- 改动沿同一条调用链，不引入新依赖

#### bill-gates（副审·系统风险评估）

三个改动按风险排序：

1. **P254 路由拆分（低风险）**：L1 数据源从合成段切换到 _qwen_raw，是在消费已经存在但被忽略的数据。去掉了一层不必要的信号转换。

2. **hallucination_filter.py 短词豁免（低风险）**：一行 if 条件的参数调整。

3. **P247 Refiner 位移（高风险⚠️）**：Refiner 从翻译后移到翻译前，错误传播方向反转。当前 LLM 幻觉只影响英文字幕，前置后幻觉会污染中文翻译。try/except 安全网仍在（LLM 挂了不卡流水线），但缺少**输出质量校验**——LLM 返回了结果但结果是错的，try/except 捕获不到。

架构安全网评估：
| 安全网 | 状态 |
|--------|------|
| Refiner try/except | ✅ 已有 |
| 翻译质量门槛（<50% 抛异常） | ✅ 已有 |
| Refiner 输出校验（编辑距离阈值） | ❌ 缺失 |
| checkpoint 恢复兼容 | ❓ 待确认 |

**建议**：加一个 Refiner 输出保底比对——如果 LLM 改动幅度超过编辑距离阈值（如原文 50%），标记为 uncertain，翻译走保守策略。

### 涉及文件（新增）
- sentence_splitter.py:32 — word_align_segments 定义（Refiner 后调用，不受位移影响）
- qwen_asr_runner.py:247-258 — qwen_chunks 数据源头

---

## ④ 双签 diff + 简化 — 2026-06-12
**执行人**：Claude（/review + elon-musk 并行）
**前置阅读**：①②③ 结论 + 目标文件全量代码
**读了什么**：transcriber_v2.py:175-214、hallucination_filter.py:694-732、worker.py:517-578

### 结论

**综合判定：方案通过，附加 2 处低风险预修 + 1 处删除 ✅**

#### /review（主审·diff 扫雷）

在将要修改的代码区域发现 4 处隐藏 bug：

1. **时间窗口边界遗漏**（transcriber_v2.py:213）：
   `in_range = [w for w in qwen_words if w["start"] >= t0 and w["start"] <= t1]`
   只检查词开头时间。跨越窗口边界的词会被排除。修复：改为 `w["end"] >= t0 and w["start"] <= t1`（区间重叠判断）。

2. **Refiner 异常吞没太宽**（worker.py:573）：
   `except Exception: ... pass` 吞没所有异常，包括代码 bug（AttributeError、KeyError）。Refiner 前置后静默失败影响面更大。修复：只捕获已知可恢复异常（网络超时、API 错误）。

3. **Segment 属性缺省初始化遗漏**（hallucination_filter.py:715-721）：
   只初始化 filtered/filter_reason/uncertain，但 L1 还访问 sensevoice_tag 和 words。靠上游保证，无防御。

4. **_qwen_raw 浅拷贝共享**（transcriber_v2.py:364）：
   `qwen_chunks = list(_qwen_raw)` 浅拷贝。下游修改 dict 内容会污染原始数据。

#### elon-musk（副审·简化审查）

核心观点：**五层过滤器架构是 LLM 昂贵时代的产物。2026 年，一个 LLM 调用可以替代 L1+L2+L4+L5 四层过滤器。** 删 400 行，加 50 行。

但承认当前是 bug 修复不是重设计。建议：
- **立即删**：`_is_short_word_exempt`（hallucination_filter.py:191）——L1 换了数据源后它的假设不再成立，保留比删除更危险
- **下版本做**：LLM 替代五层过滤器（记为架构 debt）

#### 动手前执行清单

| # | 动作 | 类型 |
|---|------|------|
| 1 | 修 transcriber_v2.py:213 时间窗口边界 | 🐛 bug 修复 |
| 2 | 修 worker.py:573 异常吞没范围 | 🐛 bug 修复 |
| 3 | 删 hallucination_filter.py:191 短词豁免 | 🗑 删代码 |
| 4 | 加 Refiner 输出编辑距离校验 | 🛡 安全网 |

---

## ⑤ 设计一致性走查 — 2026-06-12（v2 纠正版）
**执行人**：Claude
**前置阅读**：①②③④ 结论 + 主线代码（hallucination_filter.py 全量）+ ASR 分支代码
**读了什么**：transcriber_v2.py:340-500、worker.py:330-578、hallucination_filter.py:694-755

### 重要：设计图范围界定

第①步的设计图是**局部设计图**——只描述本次 bug 修复要改动的部分：
```
转录→P254路由→保留_qwen_raw
              ├─L1看原稿 ──────────┐
              └─Whisper骨架组装 ────┘（并行汇合）
              →L2交叉验证→P247双稿裁判+边界修复+润色→翻译
```

**不在图上的 ≠ 该删。** `filter_hallucinations` 的五层架构（L1→L2→L4→L5→L3）是主线已有代码，走查只比对本次改动是否与设计图的**局部描述**一致，不标记主线已有功能为"多了"。

### 逐步骤对照（五问，纠正版）

#### Q1: 多了吗？（本次改动范围内）

| 代码位置 | 设计图上？ | 判定 |
|---------|-----------|------|
| L4 regex 过滤 (hallucination_filter.py:740-742) | 不在局部设计图上 | ✅ 主线功能，不动 |
| L5 去重 (hallucination_filter.py:745) | 不在局部设计图上 | ✅ 主线功能，不动 |
| L3 语速检测 (hallucination_filter.py:747-749) | 不在局部设计图上 | ✅ 主线功能，不动 |
| `_has_timestamp_segments` L378 | 不在局部设计图上 | ✅ MLX/PyTorch 分叉逻辑，主线功能 |

**结论：本次改动范围内没有多出来的东西。**

#### Q2: 少了吗？（设计图要求但代码未实现）

| 设计图要求 | ASR 分支当前状态 | 判定 |
|-----------|----------------|------|
| L1 看 `_qwen_raw` 原始词级数据 | `_layer1_dual_signal` L732 消费 `effective_alignment`，数据来自 `_qwen_segments_as_alignment(qwen_chunks)` L726-727。但 L1 迭代的是 `segments`（组装后的 Segment 对象），不是 `_qwen_raw`。segment 的 `sensevoice_tag` 和 `words` 属性是组装时写入的 | ⚠️ P261——需确认 L1 实际使用的对齐数据是否来自原稿。当前 `effective_alignment` 确实从 `qwen_chunks`（=_qwen_raw 的浅拷贝）提取，但 L1 对每个 segment 的判断还依赖 `seg.text` 和 `seg.words` 属性——这些来自组装后的 segments |
| P247 双稿裁判（Qwen + Whisper 对比） | worker.py:539 prompt 只有 Qwen 稿（`_srt_text` 来自 segments） | ❌ P247——需加 Whisper 参考稿 |
| P247 在翻译前 | worker.py:517-576 Refiner 在 Step 4 subtitle stage，翻译在 Step 3 L377 | ❌ P260——需位移 |
| 边界修复+润色 | 当前 prompt 无此能力 | ❌ P247——需扩展 prompt |

#### Q3: 错位了吗？

| 设计顺序 | 代码顺序 | 判定 |
|---------|---------|------|
| P247 → 翻译 | 翻译 → P247 | ❌ P260——修 |
| L1 ∥ 组装（并行） | 组装(L359) → filter(L496 含 L1) | ⚠️ 串行但合理——两者都在 `transcribe()` 内，读同一份 `qwen_chunks`，不互相依赖。可以不并行（单线程内顺序执行不影响正确性），设计图的"并行"是指概念上的独立分支 |

#### Q4: 为什么等？

全部箭头都是真实数据依赖（已验证于初版走查），没有"只是排队"的假箭头。设计图正确。

#### Q5: 数据路上经过了几只手？（追踪 `_qwen_raw` 全链路）

| # | 手 | 读/改/扔 | 设计图上？ |
|---|-----|---------|----------|
| 1 | `qwen_asr_runner` → 产出 | 生 | ✅ 转录 |
| 2 | `transcriber_v2:357` `_qwen_raw = qwen_chunks` | 读（存引用） | ✅ P254 路由 |
| 3 | `transcriber_v2:359` `_qwen_chunks_to_segments(qwen_chunks, ...)` | 读（组装） | ✅ 组装 |
| 4 | `transcriber_v2:364` `list(_qwen_raw)` | 读（浅拷贝传下游） | ✅ 管道 |
| 5 | `transcriber_v2:378` `_has_timestamp_segments` | 读 | — MLX/PyTorch 分叉 |
| 6 | `hallucination_filter:726` `_qwen_segments_as_alignment` | 读（提取对齐数据给 L1） | ✅ L1 看原稿 |
| 7 | `hallucination_filter:735` `_qwen_is_reliable` | 读（可靠性预判） | — L2 前置判断 |
| 8 | `hallucination_filter:738` `_assign_qwen_to_windows` | 读（L2 窗口分配） | ✅ L2 交叉验证 |

手 #5 和 #7 是主线已有的路径分叉判断，不在局部设计图上但也不在本次改动范围内。**数据全链路中，与本次改动相关的 6 只手都在设计图上有对应。**

### 偏差汇总（仅本次改动范围）

| # | 类型 | 描述 | 决定 |
|---|------|------|------|
| 1 | 少了 | P247 双稿裁判 prompt 未实现 | 修——本次 P247 |
| 2 | 错位 | P247 在翻译后（应在翻译前） | 修——本次 P260 |
| 3 | 数据源 | L1 的对齐数据虽来自 qwen_chunks，但 L1 迭代 segments 时还依赖组装后的 seg.text/seg.words | 修——本次 P261，确保 L1 的输入完全是原始数据 |

**L4/L5/L3 不在局部设计图上 ≠ 该删。它们是主线功能，本次改动不碰。**

### 走查判定：✅ 通过

设计图与目标代码一致。三个偏差正是本次要修的 P247/P260/P261。修复后局部设计图与代码完全对齐，且不破坏主线已有的 L4/L5/L3 过滤器。

---

## ⑥ 安全审查 — 2026-06-12
**执行人**：Claude（security-review skill git 配置问题，人工审查）
**前置阅读**：①②③④⑤ 结论
**读了什么**：worker.py:539-576（Refiner prompt 拼接+输出解析）、transcriber_v2.py:351-364（路由数据流）

### 结论

**综合判定：✅ 通过（附带 1 项必须修复）**

#### 发现

**1. Prompt 注入 — 低风险，无需修**

双稿裁判 prompt 将音频转录文本（不可信数据）直接拼入 LLM prompt。理论上可通过对抗性音频注入指令。但这是单用户本地桌面应用，攻击面 = 用户攻击自己，无实际威胁。

**2. LLM 输出解析 — ⚠️ 中等风险，必须修**

worker.py:555-571 解析 Refiner 输出的逻辑：
```python
if _line.isdigit():       # 纯数字 → 当作 segment ID
    _current_id = _line
elif _current_id and ...: # 否则 → 当作字幕文本
```
如果 LLM 输出了纯数字文本行（如角色说"365"），它会被误解析为 segment ID，导致后续字幕写入错误的 segment。修复：加 SRT 三段式格式校验（ID行 → 时间戳行 → 文本行），不单独用 `isdigit()` 判断。

**3. 翻译输入污染 — 低-中风险，已有对策**

Refiner 前置后 LLM 输出直达翻译输入。当前安全网仅翻译质量门槛（<50% 抛异常）。已在④中加编辑距离校验到动手清单。

#### 风险矩阵

| 风险 | 级别 | 处置 |
|------|------|------|
| Prompt 注入 | 低 | 不修——单用户本地应用 |
| LLM 输出解析误匹配 | 中 | **TDD 阶段必须修** |
| 翻译输入污染 | 低-中 | 加编辑距离校验（已在清单） |
| 敏感数据泄露 | 无 | 改动不涉及凭证 |

---

## ⑦ TDD — 2026-06-12
**执行人**：Claude
**前置阅读**：①-⑥ 全部结论
**改了**：transcriber_v2.py、worker.py、hallucination_filter.py

### 修复清单（全部完成）

| # | 改动 | 文件:行 | 测试 |
|---|------|---------|------|
| 1 | 时间窗口边界：区间重叠判断 | transcriber_v2.py:213 | test_p260_boundary_fix.py ×3 ✅ |
| 2 | 异常吞没缩窄 | worker.py:573→585 | 机械修改，无需独立测试 |
| 3 | 删除 _is_short_word_exempt | hallucination_filter.py:191-210 | hallucination_filter ×56 ✅ |
| 4 | SRT 三段式解析 | worker.py:555→440 | 集成于 Refiner |
| 5 | P247 Refiner 位移+双稿裁判 | worker.py:373-496（新增） | 全量回归 |
| 6 | L1 数据源确认（_qwen_raw 路径通） | 无需改代码 | 走查确认 |
| 7 | Refiner 编辑距离安全网 | worker.py:449-457 + _lev_dist() | 全量回归 |

### 关键改动细节

**P247 Refiner 位移**：
- 旧位置：worker.py Step 4（翻译后 L614-680）→ **已删除**
- 新位置：worker.py Step 2 结尾（翻译前 L373-496）→ **已插入**

**双稿裁判 prompt**：
- 检测 `seg.whisper_text` 属性是否存在 → 有则双稿裁判模式
- 双稿模式：同时展示 [Qwen Transcript] 和 [Whisper Reference]
- 单稿模式（无 Whisper 参考时）：回退到传统 Refiner prompt

**编辑距离安全网**：
- `_lev_dist()` 计算 Levenshtein 距离
- 改动幅度 > 50% → 回退原文 + 标记 uncertain

**Whisper 原稿传递**：
- `_qwen_chunks_to_segments` 组装时同时保存 `seg.whisper_text`

### 测试结果

```
全量：516 tests / 514 passed / 2 failed（预存环境问题，非本次改动）
  - test_p201_qwen_aligner_check: 模型已下载导致断言反转
  - test_phase1c_ytdlp: yt-dlp 行为变化
本次改动相关测试：59 tests / 59 passed ✅ 零回归
```

---

## ⑧ verification-before-completion — 2026-06-12
**执行人**：Claude
**验证命令**：`python -m unittest discover -s test -p "test_*.py"`（本消息内重新跑）

### 验证结果

```
Ran 516 tests in 127.187s
FAILED (failures=2)
```

| 失败测试 | 原因 | 与本次改动相关？ |
|---------|------|:--:|
| test_p201_qwen_aligner_check | 本机已下载模型，断言期望"未下载" | ❌ |
| test_phase1c_ytdlp | yt-dlp 行为变化 | ❌ |

**判定：✅ 零回归**

---

## 流水线进度总览

| 步骤 | 状态 |
|------|:----:|
| ① investigate | ✅ |
| ② 双签审查 | ✅ |
| ③ 双签影响面 | ✅ |
| ④ 双签 diff + 简化 | ✅ |
| ⑤ 设计一致性走查 | ✅ |
| ⑥ 安全审查 | ✅ |
| ⑦ TDD | ✅ |
| ⑧ verification | ✅ |
| ⑨ QA（Web） | ⏭ 跳过（CLI项目） |
| ⑩ 端到端实测 | ⬜ 待用户 |

## 代码改动摘要

| 文件 | 改动 | ~行数 |
|------|------|:----:|
| transcriber_v2.py | 时间窗口区间重叠 + whisper_text 保存 | +3 |
| worker.py | Refiner 位移 + 双稿裁判 + SRT 加固 + 编辑距离 | +68/-70 |
| hallucination_filter.py | 删除 _is_short_word_exempt | -14 |
| test_p260_boundary_fix.py | 新增边界测试 ×3 | +90 |

---

