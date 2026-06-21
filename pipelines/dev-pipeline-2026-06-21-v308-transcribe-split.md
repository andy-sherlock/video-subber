# 开发线流水线 — V3.08 复杂度拆分 transcribe() — 2026-06-21

## 元信息
- 类型：开发线（DECIDE → BUILD → HARDEN）· 纯重构（CLEANUP）
- 涉及问题：P219（复杂度拆分）
- 涉及文件：v3/backend/transcriber_v2.py（transcribe 在 839-1250）
- 测试基线（拆前）：**614 passed, 1 skipped, 0 failed**（真机 97s 复现成功）
- 当前进度：BUILD 进行中

## ① DECIDE — 范围锁定 — 2026-06-21
**执行人**：Claude（planning-with-files-zh + steve-jobs-perspective）
**依赖类型**：📋 看记录

### 结论（Feature Freeze）
乔布斯减法：15 件事砍到「只杀一条龙」。
- ✅ 本轮主任务：拆 transcribe() 复杂度 41 → 多个 ≤10 职责函数，纯搬运零行为变更
- ✅ 顺手擦（仅 1 项安全）：删空壳 `_assemble_qwen_on_whisper_boundaries`（769-770，纯直通 `_assemble_whisper_primary`，仅 829 行调用）
- 🚫 剥离：`__import__('time')`（在 translation/base.py，不同文件，拆 transcribe 碰不到）
- 🚫 剥离：统一 `_is_repetition_pattern`（transcriber_v2 词频算法 vs hallucination_filter 正则 `_RE_REPEATED_*`，**两套不同算法，合并=改行为**，非纯重构，需单独分析）
- ⏸ 推迟：另外 5 个函数、namedtuple/确认框/设计图、第三部分启动健康检查（FEATURE 不与纯重构同版本）

## ② BUILD — 拆分设计 — 2026-06-21
**执行人**：Claude
**依赖类型**：📖 读指定文件（transcriber_v2.py + 测试）

### 🔴 决定性约束（必须遵守，否则测试假红）
`test/test_p266_mlx_whisper.py:179 test_standard_router_detects_language_before_full_whisper`
用 `inspect.getsource(transcribe)` 检查**源码文本**，断言以下字符串必须留在 transcribe() 体内：
- `detect_language_with_whisper`（探测调用，905）
- `transcribe_whisper`（全量转录调用，937）
- `use_standard_router`（870）
- `_standard_primary == "whisper"`（933、987 的比较）

→ 设计上让这 4 个调用点/比较保持**内联**，只搬它们周围的重分支块。这样测试自然绿，**不碰这个测试**。

### transcribe() 职责块地图（839-1250）
| 块 | 行号 | 职责 | 含保护串？ | 处置 |
|----|------|------|-----------|------|
| A | 851-861 | VAD 语音占比 gate | 否 | 抽 `_vad_speech_gate` |
| B | 863-878 | 硬件检测+模式标志 | 否 | 留内联（线性） |
| C | 880-896 | Qwen 并行线程早启动 | 否 | 抽 `_maybe_start_qwen_thread` |
| D | 898-931 | Whisper 探测+标准主引擎选择 | **是**(探测/标志) | 探测内联；qwen-switch(920-931)抽 helper |
| E | 933-988 | Whisper 转录+段迭代 | **是**(transcribe_whisper) | 调用内联；迭代循环(947-981)抽 `_whisper_output_to_segments` |
| F | 990-1004 | 等 Qwen + 串行 fallback | 否 | 抽 `_collect_qwen_chunks` |
| G+H | 1006-1036 | 智能路由+FA需求+L1段 | 否 | 抽 `_route_primary_engine` |
| I+J+K | 1038-1146 | SV+FA 派发/等待/应用 | 否 | 抽 `_run_sensevoice_and_fa`（可能再子拆） |
| L | 1147-1159 | FA 质量标注 | 否 | 并入上一步或抽 `_annotate_fa_quality` |
| M-Q | 1161-1226 | 语言推断/人声地图/SV标签/L1/组装 | 否 | 抽 `_postprocess_segments` |
| R-T | 1228-1247 | 五层过滤/空检查/说话人 | 否 | 抽 `_apply_filters_and_speaker` |

### 执行纪律
- 一次抽一个 helper → 跑 transcribe 相关测试子集 → 绿 → 下一个
- 全部抽完 → 跑全量 614 → 必须零退化
- 铁律：搬=逐字搬，一个字节逻辑不改。测试需改断言=停。

### 拆分相关测试子集（快速回归用）
test_p266_mlx_whisper.py test_arch7_pipeline.py test_standard_precise_mode.py
test_p268_p269_pipeline.py test_p276_standard_language_env.py
**⚠️ 教训**：快速子集必须加上 test_harden_v307_sprint2.py、test_phase1d_memory.py、
test_p199_p200_parallel.py——它们是源码文本检查测试，会被任何抽函数撞红。

## ③ BUILD — 拆分完成 — 2026-06-21
**执行人**：Claude
**依赖类型**：📖 读指定文件 + 🏃 跑测试

### 结论
- **transcribe 复杂度 41 → 10**（radon），抽出 16+ 个职责 helper，全部 ≤10。
- **615 测试零退化**（基线 614 passed + 1 skipped；现 615 passed 0 skipped——把一个原本因 `segments_iter` 字符串永不匹配而静默跳过的测试改成了真断言）。
- 顺手擦：删空壳 `_assemble_qwen_on_whisper_boundaries`（内联直通目标）。

### 关键决策（用户拍板）
1. **6 个源码文本检查测试**（检查字符串是否在 transcribe 源码里，非行为测试）随重构更新为检查模块/对应 helper。涉及：`_fa_quality`×2（test_harden_v307_sprint2）、`release_whisper_model`（test_phase1d_memory）、probe 顺序（test_p266）、SV/Qwen 顺序×2（test_p199_p200）。意图全部保留，行为可证明未变。
2. **Phase C 闭包 late-binding 竞态**（detected_lang）刻意保留不动 → 登记 P296。`_should_parallel_qwen` 只抽布尔条件，闭包留 transcribe 内联保 late-binding。

### 对象标识/闭包安全性核对（关键）
- FA 线程闭包捕获的 `_fa_target`/`_fa_holder`/`detected_lang` 作为参数传入 helper，是同一对象，且线程存活期（dispatch→join）内不被重新赋值 → 行为一致。
- `_sv_target = _qwen_l1_segments if qwen-primary else results`——用参数（同对象）计算后返回给 transcribe 给 Phase O 用，标识一致。
- `results` 在 helper 内可能 `= _fa_target`，作为返回值传回 transcribe 重新绑定，与原地等效。

### 重大旁支发现
- **P297**：`.gitignore:14 test/` 把整个 `v3/test/` 排除出版本控制（`git ls-files v3/test/`=0）。测试套件从未被跟踪，本轮 6 个测试改动落不了盘。待用户决策。

### 下一步
- HARDEN（/review 扫 diff + 设计一致性走查）尚未跑。
- 提交前需先解决 P297（否则测试改动进不了 commit）。
- 本轮范围只拆了 transcribe；另 5 个高复杂度函数 + 启动健康检查在后续轮次。
