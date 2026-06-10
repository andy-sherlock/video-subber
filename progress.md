# V3.06 进度日志

**日期**：2026-06-10
**版本**：V3.07 HARDEN Sprint 2 完成（459 测试，0 失败）
**上一版本**：V3.06（git commit `415b89f`）

---

## 2026-06-10 — V3.07 HARDEN Sprint 2 TDD 修复完成

### 完成事项

#### TDD 并行修复（8 项代码修复全部 GREEN + 1 项配置）
- **P240** ✅ 路径穿越防护：`_validate_job_path()` 辅助函数 + `llm_fallback_decision` 调用校验
- **P243** ✅ SSRF 防护：`_verify_safe_url()` 辅助函数，scheme + 私有 IP 拦截
- **P244** ✅ 路径穿越防护：`get_job` 调用 `_validate_job_path` 校验
- **P241** ✅ retry prompt 从硬编码"英文→中文"改为 `_TARGET_LANGUAGE` 动态取值
- **P248** ✅ P3 反思条件去掉 `and failed`，quality 模式始终触发
- **P246** ✅ L4 重复模式 + 重复词加 `l2_status != "GREEN"` 豁免
- **P213** ✅ `apply_aligner_timestamps` 未匹配段计数 + WARNING 日志
- **P214** ✅ `_fa_quality` 标记（official/fallback/not_attempted）+ 降级 WARNING
- **Commit 4** ✅ pyproject.toml 全部 14 个依赖锁定为 `==`，新增 `transformers==4.57.6`，版本号升 3.07

#### 测试
- 27 个新增测试（test_harden_v307_sprint2.py）全部 GREEN
- 全量回归：459 passed, 10 skipped, 0 failed（7.31s）

### 待做
- git commit（5 个 commit 的代码已在工作区）
- qa-only + 端到端实测（用户手动验证）

---

## 2026-06-10 — V3.07 HARDEN 全面审查 + 修复决策

### 审查完成
- `/review` 全项目审查 → 发现 6 个问题（P240-P245）
- 设计一致性走查 → 发现 3 个偏差（P246-P248）
- `ln-021-codegraph` 架构分析 → 零循环依赖，单向无环图
- `verification-before-completion` → 432 passed, 10 skipped, 0 failed
- 审查报告：`HARDEN-V3.07-审查报告.md`

### 修复决策（用户拍板，含尾巴整合）
**本版本修（5 个 Commit · 8 项代码修复 + 1 项配置）**：
- Commit 1：P240 + P243 + P244（安全防线）
- Commit 2：P241 + P248（翻译质量）
- Commit 3：P246（过滤器豁免）
- Commit 4：pyproject.toml 版本锁定（决策二·静态防线）
- Commit 5：P213 + P214（尾巴——词数漂移 + FA 降级可见化）

**延后到 V3.08**：P242/P245/P247（bug+新功能）+ P179（新功能）+ P212（🧑 依赖人工）
**依赖人工**：P149/P171（L2 门槛校准，等真实视频数据）

### 宏观战略决策（2026-06-10 乔布斯审查 + 工程师联审，全部定稿 ✅）
1. 管线复杂度：功能不动，V3.08 拆 worker.py 为独立模块（转录/对齐/过滤/翻译/字幕） ✅
2. 模型防退化：四道防线——V3.07 锁版本 + V3.08 启动心机音频体检 + V3.09 精度报告 + 桌面端打包复用 ✅
3. 战略重心：V3.07 封版后全力转向 PySide6 桌面端 ✅

---

## 2026-06-09 — Bug 修复流水线 P197-P203 + I6 全部修复 ✅

### 完成事项

#### TDD 修复（8 项全部 GREEN）
- **P197** ✅ 配置加载不再把 API Key 混进全局设置——就像把钥匙单独锁起来，不跟其他东西混放
- **P198** ✅ 视频下载改为"分体下载"——画面和声音分开下载，各取最优画质/音质，最后再合并
- **P199** ✅ 语音识别和音频分析两个模型不再排队等，改为同时开工
- **P200** ✅ Qwen 对齐器不再卡住其他模型的工作
- **P201** ✅ 环境检测改用本地文件检查，不再误报"模型已安装"
- **P202** ✅ 内存不足的错误判断改为精确匹配关键词，不再误判
- **P203** ✅ 文件归档加时间戳，同一个视频处理两次不会覆盖上次的结果
- **I6** ✅ 测试从检查界面细节改为检查实际行为

#### P198 架构重设计（跨 3 个文件）
- 下载模块：完全重写，从"下一个最好的"改为"画面走画面通道、声音走声音通道"
- 工作流模块：适配新的分体下载返回值
- 字幕烧录模块：支持"画面文件 + 声音文件 + 字幕"三合一输出

#### 测试维护
- 删除 2 个引用已废弃功能的旧测试（P198 重写导致）
- 新增 P198 分体下载专用测试（5 个测试用例）

#### Bug 修复流水线步骤完成情况
- ① investigate ✅
- ② plan-eng-review ✅
- ③ ln-021-codegraph ✅
- ④ /review ✅
- ⑤ 设计一致性走查 ✅
- ⑥ security-review ✅
- ⑦ TDD ✅（8 项全部 RED→GREEN）
- ⑧ verification ✅（419/419 测试通过）
- ⑨ qa-only ✅（健康分 95/100，3 个低优先级小问题）
- ⑩ 端到端实测 ⏸ 用户稍后手动进行

#### QA 浏览器测试结果
- 健康分 95/100
- 所有功能测试通过，零控制台错误
- 3 个低优先级问题：按钮切换反馈不够明显、可选模块显示红色状态、部分元素缺少无障碍标签

---

## 2026-06-08 — 诊断审计（plan-eng-review + investigate）

### 完成事项

#### 诊断审计：7 个 HARDEN 问题逐条代码核查
- **P193** ✅ 已修：L2 Pass 1 跨层交叉验证代码在当前 codebase 中不存在，早期已删除
- **P194** ✅ 已修：`worker_manager.py:94-96` 已改为退出码+日志引导
- **P195** ✅ 已修：`orchestrator.py:90-93` 两处 except 已有 `_log.warning`
- **P199** ❌ 描述纠正：原写"四模型串行"→实际 standard 模式只跑 3 模型（无 Qwen3-ASR），需并行的是 SV+FA 两个模型
- **P198** ❌ 确认存在：`video_downloader.py:43` `format: "best"` 仍在
- **P200** ❌ 确认存在：`transcriber_v2.py:291-293` qwen join 仍阻塞 SV+FA
- **P201** ❌ 确认存在：`is_qwen_aligner_available()` 只查 import 不查模型文件，`env_check.py` 无 FA 检测
- **docstring stale order** ✅ 已修：`hallucination_filter.py:3` 已写 `L1→L2→L4→L5→L3`

#### 设计图补充
- `findings.md` 新增「执行模式分支」表：standard / precise+ram≥16GB / precise+ram<16GB 三条路径，明确 Step 2 和 L2 的 tier 差异

#### 文件同步
- `problems.md`：P193/P194/P195 标 ✅，P199 描述纠正，P201 确认
- `task_plan.md`：HARDEN 表同步更新，P201 条目补入
- `findings.md`：补 tier 分支表

### 待做（留给下次专门的审查+修复会话）
- P197（API Key localStorage 竞态）→ P198 → P199+P200 → P201 按 bug 修复流水线走完整流程
- 注：上一轮诊断审计漏审 P197，其 Steve Jobs 三步减法方案已在 problems.md 中锁定，待验证代码后执行

---

## 2026-06-07 会话 #3 — Bug 修复流水线 P182+P183+P185+P188 ✅

### 完成事项

#### P182 🔴 Producer-consumer 尾部 chunk 丢失（TDD ✅）
- **问题**：短于 300s 的视频在 finally 中只发 sentinel，不先 flush 剩余 segments → Qwen 交叉验证完全跳过
- **修复**：[transcriber_v2.py:280-289](v2/backend/transcriber_v2.py#L280-L289) — finally 块中先 flush `results[chunk_start_idx:]` 再发 sentinel
- **测试**：`test/test_p182_tail_flush.py`（3 tests，模拟 producer loop 逻辑）

#### P183 🟡 L1 单信号 fallback 缺失（TDD ✅）
- **问题**：FA=None/空时 `_layer1_dual_signal` 直接 return — 纯音乐/噪音无过滤
- **修复**：[hallucination_filter.py:147-155](v2/backend/core/hallucination_filter.py#L147-L155) — FA 不可用时 Trust SenseVoice alone，non-Speech tag → filter
- **测试**：`test/test_p183_l1_fallback.py`（6 tests）+ 更新 `test_p161_wiring.py`（3 tests 从 assertFalse 改为 assertTrue）

#### P185 🟡 死 import 清理 ✅
- **修复**：删除 `transcriber_v2.py:234` 中未使用的 `_chunk_segments` import

#### P188 🟡 URL 协议校验缺失（TDD ✅）
- **问题**：P174 链接直传接受 `file://`、`ftp://`、`javascript:` 等非 http 协议
- **修复**：[main.py:590-596](v2/backend/main.py#L590-L596) — 仅允许 `http://` 和 `https://`，其余返回 400
- **测试**：`test/test_p188_url_validation.py`（3 tests，FastAPI TestClient）

### 流水线走法
investigate ✅ → plan-eng-review ✅ → ln-021-codegraph ✅ → /review ✅ → security-review ✅ → TDD ✅ → verification-before-completion ✅ (397/397) → QA ✅ (curl API) → ⏸ 端到端实测待用户跑真实视频

---

## 2026-06-07 会话 #2 — ARCH ⑦ P152+P144+P168+P169+P173 转录流水线重构 ✅

### 完成事项

#### 转录流水线重构（347 测试全过）
- **P152**：生产者-消费者流水线 — Whisper 主线程边转录边喂千ASR，queue.Queue + threading.Thread 并行
- **P144**：SenseVoice 标签移到 FA 对齐之后 — Step 4 FA 对齐先改时间戳，Step 5 SV 贴标用精确时间戳
- **P168**：时间戳偏移修复 — `_process_single_chunk()` 用 `extract_start` 替代 `t_start` 做偏移基准
- **P169**：return_time_stamps 统一 — `_qwen_gen_kwargs()` PyTorch 路径自动加 `return_time_stamps=True`
- **P173**：模型加载去重 — 提取 `_load_qwen_model()` 统一 4 处加载代码，拆出 `_process_single_chunk()` + `_qwen_consumer()`
- 新增 10 个测试 (`test_arch7_pipeline.py`)，全量 347 测试 0 失败
- 改动 2 文件：`qwen_asr_runner.py` (580→460+ 行) + `transcriber_v2.py` (368→438 行)

### 流水线走法
brainstorming ✅ → plan-eng-review ✅ → ln-021-codegraph ✅ → TDD ✅ → verification-before-completion 待执行

---

## 2026-06-05 会话 — P156 + P160 千问交叉验证全面修复

### 背景

V3.05 录入了 P147（千问坍缩）和 P156（千问不说话）。新会话启动后发现 P147 的"修复"矫枉过正——千问从 185 句掉到 12 句。

### 完成事项

#### P156：千问参数二次校准 ✅
- `_QWEN_REPETITION_PENALTY` 1.4→1.1（对齐 WhisperJAV）
- `_QWEN_TOKENS_PER_SEC` 10→20（5min×20=6000 token 预算）
- 247 测试全过，端到端翻译 254/254 成功

#### P160：交叉验证架构重构 ✅
- 新增 `LightweightStemmer` 类（零外部依赖，中文/英/俄/日/韩五种语言）
- `_layer2_cross_validate` 从逐段逐字母比对改为 30 秒窗口 + 词干提取
- `filter_hallucinations()` L2 调用从 for 循环改为批量模式
- 新增 26 个测试（15 stemmer + 11 layer2）
- 273 测试全过，端到端一致率 89%（16/18 GREEN），黄章率 11%

#### 架构审查 ✅
- plan-eng-review：6 个发现
- ln-021-codegraph：影响面分析（2 文件改动 + 3 测试文件同步）
- cso/security-review：跳过（不涉及认证/权限/输入）

#### 版本迭代 ✅
- pyproject.toml: 3.05→3.06
- git commit `415b89f`：12 文件，+809/-31 行
- 只读快照 `v2/v3.06/` 已创建

### 改动文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/core/qwen_asr_runner.py` | ✏️ | P156 参数校准（2 行常量） |
| `backend/core/hallucination_filter.py` | ✏️ | +LightweightStemmer + L2 重写 |
| `backend/config_manager.py` | ✏️ | P158 字号 20→42 |
| `backend/translation/orchestrator.py` | ✏️ | P157 内容闸 |
| `frontend/index.html` | ✏️ | P158 字号选项更新 |
| `test/test_p160_stemmer.py` | 🆕 | 15 tests |
| `test/test_p160_layer2.py` | 🆕 | 11 tests |
| `test/test_phase1d_qwen_health.py` | ✏️ | 适配新 L2 |
| `pyproject.toml` | ✏️ | 3.05→3.06 |

### 当前状态
- 活跃代码：V3.06（`v2/`）
- 只读快照：V3.01-V3.06
- 测试：273 passed，零失败
- 千问参数：1.1 + 20t/s（稳定，无坍缩）

### 下一步
进入 V3.07 / Phase 2A：P160 第二步（qwen_asr_runner.py 旧对齐换词干）+ P159/P161 修复 + 门槛校准

---

## 2026-06-05 会话 #2 — V3.07 规划重整 + DeepSeek 日志调查 + P162

### 完成事项

#### 问题库全面扫描
- 扫描 problems.md 全部 162 条记录，识别出未解决问题
- 发现 P159/P161 在 task_plan.md（V3.07）和 problems.md（推迟到 Phase 2）之间的归属冲突

#### DeepSeek 翻译退化调查
- 对比 V2.12 → V3.06 的 prompt 演变：V2 短 prompt → V3 长 prompt（内容策略+术语+摘要），两个版本都没写"翻译成中文"
- DeepSeek 后端代码（`deepseek.py`、`base.py`）V2.08→V3.06 零改动
- 分析 4 次调用的 debug.log：
  - `97c57255ec` (Jun 4)：✅ 翻译成功
  - `1b520de51c` (Jun 5 AM)：✅ 翻译成功
  - `0a73f157b2` (Jun 5 AM)：❌ 俄语照抄（P157 触发）
  - `339e2f7717` (Jun 5 PM)：❌ 俄语照抄 → 内容闸拦截
- **结论**：DeepSeek 间歇性行为，非代码回归；prompt 相同但服务端不确定

#### Steve Jobs 审查（两次）
- P162 方案锁定：`_TARGET_LANGUAGE` 变量，今天=`"简体中文"`，未来从 config 读用户选择
- P159 → V3.09（"给纸板模型喷漆是浪费"）
- P161 → V3.07（真正的 bug，修它）

#### 规划文件更新
- task_plan.md：V3.07 移除 P159/P134，新增 P162；V3.09 新增语言选择 UI
- problems.md：P159 归属 V3.09，P161 归属 V3.07，P162 新增（方案+归属 V3.07）
- 未来规划新增：多语言翻译 + 双语字幕（不定版本）

### V3.07 最终待办清单（7 项）
| # | 事项 | 编号 |
|---|------|------|
| 1 | P160 第二步：旧对齐→LightweightStemmer | P160 |
| 2 | 门槛校准（中/日/英/俄） | P149 |
| 3 | 英语词干评估 | — |
| 4 | 端到端验证（四语言） | — |
| 5 | SenseVoice 标记不删除 | P161 |
| 6 | _TARGET_LANGUAGE 变量 + prompt | P162 |
| 7 | Whisper/千问流式并行 + LLM 断句合并 | P152 |

### 当前状态
- 版本：V3.06（git `415b89f`，273 测试全过）
- 下一版：V3.07（后端精度收尾，7 项）

---

## 2026-06-06 会话 — 历史讨论清理

### 完成事项

#### 历史悬而未决讨论清理
- 扫描 6 月 4-5 日全部会话讨论（S518-S530），识别出 6 个未有明确结论的讨论
- 逐一核查代码和规划，确认 5 个已被解决、1 个保持开放：
  1. MioSub 竞品分析 → 用户确认关闭
  2. 翻译并行化 → 代码已实现（ThreadPoolExecutor），关闭
  3. Dual alignment → 不需要（同一音频时间轴），关闭
  4. P149 L2 阈值方案 → 被 P160 词干架构根治，剩下的是调参，关闭
  5. 千问 ASR 与 FA 下载关系 → 保持开放，归属 V3.10
  6. 测试方法论 → 不建自动化端到端测试，用户手动测试够用，关闭
- findings.md 新增"已关闭的历史讨论"归档表

#### 千问 ASR/FA 下载关系定论 ✅
- ASR 和 FA 是两个完全独立的模型，各自有独立的 HuggingFace 仓库，各自独立下载
- ASR 的时间戳功能需要显式传入 `forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B"` 才能用，FA 不内嵌
- 实际大小修正：ASR ~4.7GB（非之前记录的 3.5GB），FA ~1.84GB BF16 全精度（非之前记录的 ~979MB 4-bit）
- V3.10 V3.10 下载按钮 UX 锁定：顶部 6 组件检测 + 3 个内联下载按钮（ASR/FA/SenseVoice）+ Whisper 在下方模型选择区点选圆点 + Resemblyzer/FFmpeg 安装包内置

### 当前状态
- 版本：V3.06（git `415b89f`，273 测试全过）
- 悬而未决讨论：**全部关闭** ✅
- 下一步：进入 V3.07 开发

---

## 2026-06-07 会话 — V3.07 HEAL 第一批 TDD

### 完成事项

#### P161 L2 双维度接线修正 ✅（4 子任务）
- ① 删除 `trim_segments_by_sensevoice` 调用和 import — SenseVoice 只贴标签不删段
- ② 删除 L1 单信号 fallback — 无对齐数据时 L1 跳过，不单方面过滤
- ③ 并行搭档修正 SV+ASR → SV+FA — 精确档并行线程改为 SenseVoice+Qwen3-FA
- ④ FA 解锁标准档 — `transcription_tier == "precise"` → `in ("standard", "precise")`
- 新增 `test_p161_wiring.py`（8 测试），更新 `test_hallucination_filter.py`（2 测试适配）

#### P166 L4 顺序调整 + l2_status 标注 ✅
- 过滤器顺序 L1→L4→L5→L2→L3 → L1→L2→L4→L5→L3
- L2 给每段标注 `l2_status`（GREEN/YELLOW/NO_DATA）
- L4 关键词规则对 GREEN 段跳过（结构规则不受影响）
- 新增 `test_p166_l2_before_l4.py`（6 测试），更新 `test_phase1d_qwen_health.py`（1 测试适配）

#### P160 词干匹配替换 ✅
- `_match_qwen_to_whisper` 中 SequenceMatcher → LightweightStemmer 词干比较
- 映射从字符级匹配升级为词干集合交集
- 新增 `test_p160_stemmer_matching.py`（5 测试）

#### P167 SenseVoice 标签改 overlap ✅
- transcriber_v2.py 中严格包含判断 → overlap 匹配（≥50% 重叠率取最大重叠事件）
- 新增 `test_p167_sv_overlap.py`（5 测试）

#### P162 prompt 目标语言 ✅
- `_TARGET_LANGUAGE = "简体中文"` 嵌入 `_SUBTITLE_PROMPT`
- 一行改动，修复 DeepSeek 面对复杂 prompt 返回原文的根因

### 测试统计
- 302 测试，0 失败，23 ERROR（全部是预先存在的 faster_whisper 缺失）
- 新增测试文件 4 个，新增测试 24 个
- 修改生产代码文件 3 个：hallucination_filter.py、transcriber_v2.py、orchestrator.py

### 当前状态
- 第一批 [BUG] 止血完成（5 个问题全部 TDD 通过）
- 下一步：第二批 [FEATURE] 调优（P149 门槛校准 → 英语词干评估 → P165 内容闸）

---

## 2026-06-07 会话 #2 — V3.07 第二批 [FEATURE] 代码实现

### 完成事项

#### P149+P171 校准基础设施 ✅
- L2 逐窗口诊断日志：每个窗口记录 w_count/q_count/shared/score/status（DEBUG 级别写入 debug.log）
- P171 最低词干门槛：MIN_STEMS=5，词干数<5 → LOW_DATA 状态（而非自信给 GREEN）
- LOW_DATA 触发 uncertain 标记，L4 关键词检查不跳过
- 新增 `test_p171_low_data.py`（6 测试）

#### 英语词干器扩展 ✅
- 新增 9 个后缀：-able, -ible, -ful, -ous, -ive, -ize, -ise, -ity, -al
- 双重辅音缩减保留（running→run），尾 i 保护保留（beauti 不缩减）
- 新增 `test_stemmer_english.py`（14 测试）

#### P165 内容闸前移 ✅
- orchestrator.py LLM 降级循环中，P1 摘要生成后检查 CJK 比例
- CJK < 10% → 判定该 LLM 不可靠 → `continue` 跳到降级链下一个，省掉 P2 token
- 新增 `test_p165_content_gate.py`（9 测试）

#### P174 YouTube/B站链接直传后端 ✅
- main.py `create_job` 新增第三种输入模式：`{"url": "https://..."}` → config 中设 source_url，hash 基于 URL
- worker.py 管线启动前检测 source_url → 透明下载到 job_dir → 更新 video_path → 继续正常流水线
- 进度推送：下载阶段 `stderr_progress("download", ...)` 接入 WebSocket
- video_downloader.py 无改动（download_video 直接复用）
- 旧 `/api/download-video` 端点保留
- 新增 `test_p174_url_mode.py`（7 测试）
- 前端 URL 输入框留给 V3.09

### 测试统计
- 337 测试，0 失败（从 302 新增 35 个）
- 新增测试文件 4 个
- 修改生产代码 4 个：hallucination_filter.py、orchestrator.py、main.py、worker.py
- 适配旧测试 3 个（test_p160_layer2.py 汉字数量增加以适配 MIN_STEMS）

### 当前状态
- 第二批 [FEATURE] 代码全部完成
- P149/P171/英语词干 待用户真实视频测试后读日志校准
- 下一步：用户跑中/日/英/俄视频 → 读 debug.log 校准阈值 → 第三批 [ARCH]

---

## 2026-06-07 会话 #3 — V3.07 第三批 ARCH ⑧ LLM 合并 ✅

### 完成事项

#### LLM 合并：术语提取 + 上下文摘要 → 一次 API 调用
- 新增 `_extract_terminology_and_summary()` — 合并 prompt，Markdown 标题（`## 术语表` + `## 内容摘要`）分隔两部分输出
- `translate_with_orchestrator()` call site 优先调合并函数，哪部分缺失回退单独补调
- 老函数 `extract_terminology()` / `_generate_context_summary()` 保留作为回退
- 新增 `test_arch8_llm_merge.py`（16 测试）
- 改动 2 文件：orchestrator.py + test_arch8_llm_merge.py

### 流水线走法
brainstorming ✅ → plan-eng-review ✅ → ln-021-codegraph ✅ → TDD ✅ → verification-before-completion ✅

### 测试统计
- 363 测试，0 失败（从 347 新增 16 个）

### 当前状态
- 第三批 [ARCH] ⑧ 已完成
- 下一步：⑨ 动态输入（砍掉 _CHUNK_SIZE=1200 硬编码）

---

## 2026-06-07 会话 #4 — V3.07 第三批 ARCH ⑨ 动态输入 ✅

### 完成事项

#### 动态分块大小：砍掉 _CHUNK_SIZE=1200 硬编码
- `LLMBackend.max_input_tokens` property — 从 LiteLLM 缓存 (`_MODEL_CONTEXT_CACHE`) 读模型 context window，exact match 优先 → suffix match 回退 → 默认 32K
- `main.py` `_fetch_community_pricing()` 同时存储 `max_input_tokens` 到 `_MODEL_CONTEXT_CACHE`
- `_compute_chunk_size(llm)` — 公式 `(max_input_tokens - 18000) // 8`，clamp [200, 3000]
- `_translate_whole_with_markers()` chunk building 改为动态计算
- 新增 `test_arch9_dynamic_chunk.py`（14 测试）
- 改动 3 文件：base.py + main.py + orchestrator.py + test_arch9_dynamic_chunk.py

### 流水线走法
brainstorming ✅ → plan-eng-review ✅ → ln-021-codegraph ✅ → TDD ✅ → verification-before-completion ✅

### 测试统计
- 377 测试，0 失败（从 363 新增 14 个）

### 当前状态
- 第三批 [ARCH] 全部完成 ✅
- 下一步：HARDEN（P170+P172+端到端）

---

## 2026-06-08 会话 — V3.07 HARDEN 版本收尾 ✅

### 完成事项

#### ① /review 全项目代码审查 ✅
- 5 个发现 (0 critical, 5 informational)
- 关键发现：worker_manager.py 误导性 OOM 错误消息、orchestrator.py silent exception swallowing、模块 docstring stale

#### ② 设计一致性走查 ⚠️
- **P193 不通过**：L2 Pass 1 (L314-325) SV 音频标签 × Qwen 文本交叉验证不在设计图中
- 模块 docstring (L3) 仍写旧顺序 L1→L4→L5→L2→L3，未同步 P166 新顺序

#### ③ security-review ✅
- CLEAN：无 shell injection、无 path traversal、URL 校验到位、CORS 受限

#### ④ ln-021-codegraph ✅
- 无循环依赖、架构健康、254 clone groups 为快照噪音

#### ⑤ verification-before-completion ✅
- **397 passed, 0 failed**

#### ⑥ qa-only ✅
- 8/8 API tests pass

#### ⑦ 端到端实测 🔧
- 需用户手动执行（无测试视频）

### 待发布前修复
- 🔴 P193：删 L2 Pass 1 跨域比对
- 🟡 worker_manager.py OOM 错误消息
- 🟡 orchestrator.py silent exception swallowing
- 🟡 模块 docstring stale order

---

## 2026-06-10 — V3.07 HARDEN 版本收尾体检

### ① /review 全项目审查 ✅
- 安全 specialist + 并发 specialist + 测试 coverage specialist 并行审查
- 发现 6 个新 bug（P240-P245）：1 CRITICAL（路径穿越）、2 HIGH、3 MEDIUM
- CRITICAL: P240 `llm_fallback_decision` 路径穿越漏洞
- HIGH: P241 retry 硬编码"英文→中文"、P242 transcribe_raw() MLX 崩溃
- MEDIUM: P243 /api/llm-models SSRF、P244 get_job 缺路径校验、P245 GLOBAL_HASH_INDEX 无锁
- +5 信息项（死代码、重复代码、日志级别误用等）

### ② 设计一致性走查 ✅
- 走查三个编排函数：transcribe() / filter_hallucinations() / translate_with_orchestrator() + worker.py
- 发现 3 处偏差（P246-P248）
  - P246: L4"重复模式"和"重复词"缺少 L2 GREEN 豁免（代码 bug）
  - P247: P1 ASR 分歧裁决未实现（功能空缺）
  - P248: P3 反思仅在翻译失败时触发（条件逻辑偏差）
- Q4 并发检查：transcribe() 中 SV+FA 并行合理，各层过滤数据依赖正确

### ③ security-review ⏭️
- Skill 无法运行（CWD 约束），跳过。P240/P243/P244（三个安全问题）已由 /review 中的安全 specialist 充分覆盖。

### ④ ln-021-codegraph ✅
- 活跃代码（`v2/backend/`）零循环依赖，单向无环图，架构健康
- MCP 工具被只读快照目录干扰（大量噪音），手动分析活跃模块依赖关系

### ⑤ verification-before-completion ✅
- **432 passed, 10 skipped, 0 failed**（较上次 +13 个新测试）

### ⑥ qa-only 🔧
- 需用户在浏览器中手动模拟全流程操作

### ⑦ 端到端实测 🔧
- 需用户用真实视频数据各场景跑一遍

### 本次发现的新问题（P240-P248）

| 编号 | 严重度 | 问题 | 位置 |
|------|--------|------|------|
| P240 | 🔴 | llm_fallback_decision 路径穿越 | main.py:850-856 |
| P241 | 🟡 | retry 硬编码"英文→中文" | orchestrator.py:799 |
| P242 | 🟡 | transcribe_raw() MLX 后端崩溃 | qwen_asr_runner.py:502 |
| P243 | 🟡 | /api/llm-models SSRF | main.py:453-499 |
| P244 | 🟡 | get_job 缺路径穿越校验 | main.py:757-780 |
| P245 | 🔵 | GLOBAL_HASH_INDEX 无锁 | main.py |
| P246 | 🟡 | L4"重复模式""重复词"缺 L2 GREEN 豁免 | hallucination_filter.py:222,228 |
| P247 | 🔵 | P1 ASR 分歧裁决未实现 | orchestrator.py |
| P248 | 🟡 | P3 反思仅在失败时触发 | worker.py:438 |

### 当前状态
- **测试**: 432 passed, 10 skipped, 0 failed
- **待修 bug**: P240-P248（9 个），P238/P239（2 个 BrokenPipe），P193（1 个设计偏差）
- **待用户手动操作**: qa-only（浏览器模拟全流程）、端到端实测（真实多语种视频）
