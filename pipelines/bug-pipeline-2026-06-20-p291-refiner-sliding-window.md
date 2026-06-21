# Bug 修复流水线 — P291 Refiner 滑窗润色 — 2026-06-20

## 元信息
- 涉及问题：P291（取代 P269）
- 涉及文件：
  - `v2/backend/worker.py`（Refiner 主逻辑）
  - `v2/backend/translation/orchestrator.py`（翻译滑窗，参考不改）
- 当前进度：①/⑩
- 触发背景：MIDA-659.ts 日语视频，标准 + 精准两种模式 Refiner 都 `corrections=0`，1 秒返回，字幕碎片句未润色

## 一句话问题
Refiner（LLM 润色）在所有模式下都失效：把约 1009 段一口气塞给 LLM（~22000 token），注意力被稀释，直接放弃。需改为滑窗分批 + 砍掉失效的候选段筛选 + 统一所有模式的润色流程。

---

## ① investigate — 2026-06-20
**执行人**：Claude
**依赖类型**：📖 读指定文件
**读了什么**：
- `v2/backend/worker.py:154-282`（Refiner 全部辅助函数：normalize / 模式选择 / 候选筛选 / prompt 构造 / corrections 回填）
- `v2/backend/worker.py:552-599`（Refiner 执行入口）
- `v2/backend/translation/orchestrator.py:328-476`（翻译滑窗：chunk 计算 / 缝隙审查，作参考）
- `git log` worker.py 最近 8 次提交

### 结论（下一步只看这里）

**根因确认：三层问题叠加，全部坐实。**

**RC1 — 无滑窗，单次全量调用（核心根因）**
- [worker.py:566-575](v2/backend/worker.py:566)：`_build_refiner_prompt(_refiner_segments, ...)` 把全部候选段拼成**一个** prompt，然后 [worker.py:575](v2/backend/worker.py:575) `_llm.translate_raw_json(...)` 发**一次** LLM 调用。没有循环、没有分批。
- 日语场景约 1009 段 ≈ 22000 token，远超 LLM 注意力疲劳极限（约上下文窗口 30%，即翻译模块 [orchestrator.py:334-337](v2/backend/translation/orchestrator.py:334) 注释的 "Lost in the Middle"）。LLM 注意力被稀释，1 秒返回 `corrections=[]`。

**RC2 — 候选段筛选对日语失效（放大 RC1）**
- [worker.py:172-176](v2/backend/worker.py:172) `_looks_broken_sentence`：判断条件 `len(t) < 20 and t[-1] not in "。？！.?!"`。千问输出的日语**无任何标点**，几乎每段都满足「结尾无标点」→ 1008/1022 段被选中。
- [worker.py:193-201](v2/backend/worker.py:193) `_select_refiner_segments` single 模式用 `uncertain OR _looks_too_short_fragment OR _looks_broken_sentence` 筛选，结果等于全选 → 1009 段全塞进 RC1 的单次调用。筛选名存实亡。

**RC3 — 双稿模式只裁决不润色（设计缺陷）**
- dual 模式 prompt（[worker.py:218-230](v2/backend/worker.py:218)）系统提示词：「对比 Qwen 和 Whisper，选出最准确的版本」「绝对不要抄写没有冲突的段落」。它**只做冲突裁决**，裁出最终稿后没有任何通顺性润色（补标点、理顺碎片）。
- dual 模式同样吃 RC1：[worker.py:194-195](v2/backend/worker.py:194) 返回全部段 → 单次调用 → 长视频同样注意力稀释。

**数据流确认（无意外修改风险）**：Refiner 在 [worker.py:553](v2/backend/worker.py:553) 操作的 `segments` 是 L1-L5 过滤 + 组装后的最终转录稿。corrections 回填走 [worker.py:278](v2/backend/worker.py:278) `seg_index[tid].text = fix`，有 `_clean_dist > 0.80` 安全阀拦截大改（[worker.py:275](v2/backend/worker.py:275)）。这部分逻辑健康，保留。

**注意 dual 模式副作用**：[worker.py:259-262](v2/backend/worker.py:259) dual 模式把所有未被纠正的段 `uncertain=False`（"裁判通过"假设）。统一润色流程时要保留这个语义，不能让润色 pass 误清标记。

### 修复方向（细节留给 ②③④ 审）

1. **加滑窗**：Refiner 内部加分批循环。窗口大小借用 [orchestrator.py:331-347](v2/backend/translation/orchestrator.py:331) `_compute_chunk_size` 的注意力疲劳公式（参考，不 import），硬上限约 150 段/窗口；重叠 20%，重叠区只取第一个窗口结果、后续窗口当只读上下文（不需缝隙审查，因为润色不是全量重写）。
2. **砍候选筛选**：删 `_looks_broken_sentence`、`_looks_too_short_fragment`、`_select_refiner_segments` 的 single 筛选；所有段进滑窗，LLM 自己判断改不改。
3. **统一所有模式润色**：dual = 冲突裁决 + 润色；single = 润色。两种模式最终都过同一个滑窗润色 pass。pass 结构（裁决和润色合一个 prompt，还是两个 pass）留给 ② plan-eng-review + steve-jobs 定。

### 涉及文件列表
- `v2/backend/worker.py:168-176` — `_looks_too_short_fragment` + `_looks_broken_sentence`（删）
- `v2/backend/worker.py:193-201` — `_select_refiner_segments`（改：全段进滑窗）
- `v2/backend/worker.py:204-251` — `_build_refiner_prompt`（改：按窗口构建 + 加上下文）
- `v2/backend/worker.py:254-282` — `_apply_refiner_corrections`（基本保留，注意 uncertain 语义）
- `v2/backend/worker.py:552-599` — Refiner 执行入口（改：加滑窗循环）
- `v2/backend/translation/orchestrator.py:331-347` — `_compute_chunk_size`（参考公式，不改不 import）

### 关键代码（现状，单次调用铁证）
```python
# worker.py:566-575
_user_prompt, _system_prompt = _build_refiner_prompt(_refiner_segments, _refiner_mode)
_log.info("P269 Refiner 模式: %s, candidates=%d", _refiner_mode, len(_refiner_segments))
_raw_json = None
try:
    _raw_json = _llm.translate_raw_json(_user_prompt, system_prompt=_system_prompt)  # ← 一次性全量
except Exception as _api_err:
    _log.warning("P247 Refiner translate_raw_json 失败: %s，保留原文", _api_err)
```

### investigate 状态
DONE — 根因确认（RC1 单次调用 / RC2 日语筛选失效 / RC3 双稿不润色），不动代码，移交 ② 双签审查。

---

## ② 双签审查 — 2026-06-20

### ②-主审 plan-eng-review
**执行人**：Claude
**依赖类型**：📋 看记录（看 ① 结论 + 已读代码）

#### 结论（下一步只看这里）
方向对、根因对，但有 5 条意见，其中 3 条必改、2 条待用户拍板：

1. 🔴【必改】**重叠 → 只读上下文边距**。翻译的 20% 重叠是「重译两遍 + 缝隙审查挑优」，Refiner 不重写不需要。照搬会导致边界段只能看半边上下文（看不到下一句，无法判断断句/补标点）。正确结构：**活跃区不重叠**（step = window_size），每窗两侧加只读上下文 pad；LLM 只对活跃区 seg_id 返 corrections，落地只接受活跃区范围 seg_id（防误改上下文段）。→ 无去重、无缝隙审查、边界质量=中间质量。
2. 🟡【拍板】**窗口大小用专属常量，别假装借公式**。`_compute_chunk_size` 下限 max(200,...)，硬上限 150 等于公式永远失效。建议 `_REFINER_WINDOW_SIZE=120`、`_REFINER_CONTEXT_PAD=20`，明确写 Refiner 逐段判断比批量翻译费注意力，取小窗。
3. 🔴【必改】**uncertain 按窗口粒度清，失败窗口不清**。现 dual 模式 worker.py:259-262 全局清 uncertain=False。滑窗后若某窗 LLM 失败，那窗段没真被判过，不能跟着清成"已验证"（否则=假数据，违反 P207 教训）。只清成功返回的窗口。
4. 🟡【拍板】**润色清不清 [?]**？[?] 来自 uncertain（两模型对不上/L2 信心低）。润色 pass 把文字改顺，但没解决「模型为何对不上」。倾向：润色只改文字、不碰 uncertain，[?] 仍由 dual 裁决决定。但影响成品观感，留用户定。
5. 🟢【必改/建议】**拆两个 pass**（正好对应用户原话「对比完得最终稿，然后走润色」）：Pass 1 冲突裁决（仅 dual 模式），Pass 2 通顺润色（所有模式）。不合一个 prompt——裁决说「只返回有冲突的段」，润色说「把任何粗糙的改顺」，指令打架。代价：dual 约 7 窗×2=14 次调用，single 约 7 次；对比现在「1 次废调用」可接受。

#### 边界清单（给 ⑦ TDD）
末窗不足 120→range 自然处理；总段<120→单窗；某窗异常→该窗保留 uncertain 其余照跑；LLM 返回空→该窗保原文记日志；LLM 返回上下文区 seg_id→拒绝只接受活跃区；单段超长→字幕有界非问题。

#### DRY
现在别抽公共滑窗模块（活跃区 vs 重叠区、JSON corrections vs 全量重译，机制不同会强行耦合）。Refiner 本地写 ~10 行切块。出现第三个滑窗 LLM 消费者再抽。

#### 主审状态
DONE_WITH_CONCERNS — 3 必改（①③⑤）+ 2 待拍板（②④）。

### ②-副审 steve-jobs-perspective
**执行人**：Claude（乔布斯视角）
**依赖类型**：📋 看记录（审 ① 诊断 + 主审意见）

#### 结论（下一步只看这里）
诊断逻辑全对，不推翻。在主审基础上再砍 3 刀 + 1 个新担心：

1. **砍：Pass 1 别重新设计**。dual 冲突裁决本质和 L2 交叉验证重叠（同一件事做两遍），但这次不动 L2、不重设计 Pass 1——把现有 dual 裁决逻辑原样塞进滑窗循环即可。**全部精力给 Pass 2 润色**（用户真正能感觉到的是「半截日语句子变通顺」）。
2. **砍：[?] 清除不该开放式问**。答案显然：润色**改了**一句（corrections 落地、过 0.80 安全阀）→ 清该句 [?]（LLM 替它背书了）；**没改**→ 保留。给默认答案让用户否决，别给白纸。
3. **砍：窗口 120 定了别再算**。用户早说了按注意力不按文本长度，120 是好数字。two-way door，真机 MIDA-659 测了不行再调，不值得现在辩。
4. **新担心：14 次调用 = 45-70 秒延迟**。现在 1 秒是因为啥也没干（骗人）。改完进度条必须显示「润色中 3/7 窗口」，不能让用户对着不动的条干等（P285 教训：用户最恨「不知道在干嘛」）。

#### 一句话定义
这次只做一件事：让 LLM 像校对员一样一次看 120 句，把读不通的日语理顺（Pass 2）。其他全是噪音。

#### 副审状态
DONE — 诊断确认无误，方案砍到「只做 Pass 2 润色」。

### ② 双签收口（两审合并）
- **共识必改**：滑窗用「不重叠活跃区 + 两侧只读上下文 pad」（非 20% 重叠）；uncertain 按窗口粒度清、失败窗口不清；进度条显示窗口进度。
- **共识范围**：Pass 1（dual 冲突裁决）原样塞滑窗不重设计；Pass 2（润色）所有模式都跑、是本次重点。
- **窗口参数**：`_REFINER_WINDOW_SIZE=120`、`_REFINER_CONTEXT_PAD=20`（先定，真机可调）。
- **待用户拍板（1 个，带默认）**：润色改了的段清 [?]、没改的保留 —— 默认采纳，待用户确认/否决。

### ② 用户决策（已拍板 2026-06-20）
**[?] 清除策略 = 改了就清、没改保留**。LLM 真改了某句（corrections 落地且过 0.80 安全阀）→ 清该句 [?]；没改 → 保留。（正是现有 single 模式 apply 的语义，已实现。）

---

## ③ 双签影响面 — 2026-06-20

### ③-主审 ln-021-codegraph
**执行人**：Claude（grep 全项目符号扫描，Python 私有函数引用确定性高）
**依赖类型**：📖🌐 读全项目

#### 结论（下一步只看这里）

**删除安全**：`_looks_broken_sentence`、`_looks_too_short_fragment` 仅被 `_select_refiner_segments` 内部引用，零测试/零外部引用 → 删除零风险。

**现有零件大部分可复用，blast radius 比预想小。** 现有已实现：
- dual prompt（Pass1 冲突裁决）worker.py:205-231 ✅
- single prompt（Pass2 润色：补标点/疏通碎裂/修听错）worker.py:233-251 ✅
- apply dual（裁判清 uncertain）worker.py:259-262 ✅
- apply single（改了就清、没改保留）worker.py:264-282 ✅ 正是用户拍板语义

**缺的只有 4 样**：① 滑窗循环（RC1）；② dual 裁决后接 Pass2、所有模式跑 Pass2（RC3）；③ 删坏筛选（RC2）；④ prompt 加只读上下文 + 每窗进度日志。

**签名兼容策略（关键，避免破 3 个测试）**：
- `_choose_refiner_mode(segments)` / `_apply_refiner_corrections(segments, corrections, mode)` 签名**不动**。滑窗时按窗口传 segment 子集 → dual 全局清 uncertain（worker.py:260 `for seg in segments`）自然限定在单窗内；失败窗口不调 apply → uncertain 自然保留。**主审②问题3 靠「按窗口传子集」自动满足，不需改 apply。**
- `_build_refiner_prompt` 加可选参 `context_before=[], context_after=[]`（默认空）→ 现有 `([seg], mode)` 测试调用不破。

**测试影响**：3 测试文件（test_p268_p269_pipeline / test_harden_v307_sprint2 / test_p287_whisper_text_backfill）旧断言基本不破（dual `[CONFLICT]` 格式保留、mode 判断保留、改了就清没改保留语义保留）。⑦ TDD 需新增滑窗用例。

#### 主审状态
DONE — 删除零风险；签名兼容可达；blast radius 小。

### ③-副审 bill-gates
**执行人**：Claude（盖茨视角）
**依赖类型**：📋 看记录（审主审影响面 + 系统层面）

#### 结论（下一步只看这里）

架构隔离正确：Refiner 坐转录↔翻译之间，独立处理段，不碰转录/过滤/翻译层；0.80 安全阀是好的隔离边界。blast radius 可控。

**唯一系统级风险——二阶效应**：今天 Refiner 是死的（corrections=0），从未真正改过字幕。所有历史真机验收（FJIN-141/MIDA-659）都是在「Refiner 不工作」前提下通过的。本次修复 = 第一次接通从未通电的电路。下游翻译模块（滑窗/术语表/缝隙审查）在**润色稿**上的表现从未验证过。
→ **⑩ 端到端实测必须覆盖完整链路（转录→润色→翻译→SRT），不能只测 Refiner 单元。** 加重标记。

**规模风险**：1000 段=14 次调用，3 小时视频（5000 段）≈66 次串行调用，DeepSeek 限流/超时可能拖长或卡死。
- 本次：串行 + 每窗 try/except 隔离（一窗失败不拖垮全部）+ 进度条。
- 下版本（记 ideas）：窗口间并行（asyncio/线程池）。这次先正确再快。

#### 副审状态
DONE — 架构隔离 OK，二阶效应（下游翻译未在润色稿上验证）是 ⑩ 必查项，规模靠 try/except 兜底。

### ③ 双签收口
- 删除零风险；签名兼容（按窗口传子集 + prompt 可选上下文参）；现有零件大量复用。
- **⑩ 必须端到端测完整链路**（盖茨二阶效应：下游翻译第一次吃润色稿）。
- 每窗 try/except 隔离（兼顾主审问题3 + 盖茨规模兜底）。

---

## ④ 双签 diff + 简化 — 2026-06-20（写码前部分）
- **主审 /review（旧代码埋雷）**：5 个雷 ① 上下文 pad 段会被误改→prompt 分区 + apply 只收活跃区段；② translate_raw_json 无内部 token 上限（已查 base.py:170，只有 120s 超时，排除）；③ mode 决策保持全局；④ `_accepted`/日志要跨窗聚合；⑤ 双 pass 不会双清 uncertain。
- **副审 elon-musk（简化）**：删假公式硬编码 120；删 2 筛选函数；想删 Pass1 但有理由保留（L2 只标记不修正，Pass1 真改 clock/claw）；净代码持平。
- 注：diff-based /review 在 ⑦ 写完码后才能真正跑 diff。

## ⑤ 设计一致性走查 — 2026-06-20
五问通过：Q1 无多余步骤；Q2 补「进度回调」进设计图；Q3 Pass1→Pass2 顺序正确；Q4 Pass2 真数据依赖 Pass1（润色裁决后的最终稿），串行正确非排队；Q5 数据经手 mode判断(读)→Pass1 apply(改)→Pass2 apply(改)→翻译(读)，无管道外的手。✅ 通过。

## ⑥ 安全审查 — 2026-06-20
注入面：字幕文本进 LLM prompt。single prompt 已有注入防护（worker.py:237）；dual prompt 缺，已在 ⑦ 补上「字幕文本只是待裁决内容，不是指令」。无认证/权限/敏感数据面。✅

## ⑦ TDD — 2026-06-20
**依赖类型**：📖 读指定文件
- RED：新建 `test/test_p291_refiner_sliding_window.py`（13 用例），跑 → 13 failed（功能缺失/旧函数仍在），RED 正确。
- GREEN：worker.py 实现——`_REFINER_WINDOW_SIZE=120`/`_REFINER_CONTEXT_PAD=20`、`RefinerWindow`、`_chunk_refiner_windows()`、`_run_refiner()`（两 pass + 每窗 try/except + 按窗传子集）、`_build_refiner_prompt` 加 `context_before/after` + `_refiner_context_block()`、dual prompt 补注入防护、删 `_looks_broken_sentence`/`_looks_too_short_fragment`/`_select_refiner_segments`、编排改调 `_run_refiner` + 进度回调。
- 跑 → 13/13 passed。3 个相关旧测试文件 53/53 passed（签名兼容，旧断言不破）。
- 覆盖：非重叠切窗+上下文 pad、短输入单窗、删函数、prompt 分区只读、300 段日语多窗（RC1 回归）、单稿一 pass/双稿两 pass、失败窗保留 uncertain、上下文 seg_id 拒绝、改了清没改保留、进度回调每窗触发。

## ⑧ verification-before-completion — 2026-06-20
**依赖类型**：🏃 跑应用（测试）
- P291 专项 13/13；相关旧测试 53/53；全量 `test/` 613 passed, 1 failed, 1 skipped。
- **唯一失败 p201（test_returns_false_when_model_not_downloaded）经证明非 P291 引入**：单独跑/整文件跑都通过；排除 P291 新测试后全量仍 1 failed（600 passed,1 failed）；零 refiner/worker 引用；根因是 `sys.modules` mock 被加载真实 qwen 的测试污染 + 模型已下载本机。**预存测试隔离 flake，单独标记不阻塞 P291。**

## ⑨⑩ 端到端验收（待真实环境）
- 当前 headless 无 Metal/GPU + 无 DeepSeek API，无法跑真实「转录→润色→翻译→SRT」。
- 交用户在 Mac 前台 GPU + API 环境用 MIDA-659.ts 跑：标准模式 + 精准模式各一遍，看日志 `P291 Refiner 完成: mode=... pass1=... pass2=...`（pass2 应 >0）、`[?]` 数量、字幕碎片句是否变通顺、进度条是否显示窗口进度。
- **盖茨二阶效应必查**：下游翻译首次吃润色稿，确认翻译质量不因输入变化产生新问题。

## 当前进度：⑧/⑩（代码完成，⑨⑩ 待真实环境）
