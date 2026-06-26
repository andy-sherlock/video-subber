# V3.08 进度日志

**日期**：2026-06-21
**版本**：V3.08（搬家完成 ✅ → transcribe 拆分完成 ✅ → 余下 5 函数 + 健康检查待做）

## 2026-06-26 V3.08 发布线完成

**发布结果**：V3.08 已发布。

- 发布前完整全量测试：`633 passed, 8 warnings in 93.75s`。
- 版本号：`v3/pyproject.toml` 已 bump 到 `3.08`。
- 发布提交：`690e8bc release: V3.08`。
- 发布 tag：`v3.08`。
- 只读快照：`snapshots/v3.08/`，大小 `5.2M`，已排除 `.venv/` 和缓存目录，权限已设为只读。
- 发布线记录：`pipelines/release-pipeline-2026-06-26-v308.md`。
- 选择性提交边界：未使用 `git add .`；未纳入工作区里已有的无关删除 `v1.2/video_subber/subtitler.py`。

**下一步**：进入 V3.09，做 PyWebView 桌面壳 + 精度报告。V3.08 不再继续加功能。

## 2026-06-26 V3.08：P300 worker_manager 循环依赖修复

调用图验证发现旧结构 bug：`v3/backend/main.py` 顶部导入 `worker_manager`，而 `v3/backend/worker_manager.py::_forward()` 运行时又反向导入 `.main.JOBS`，形成 `main -> worker_manager -> main` 循环依赖。

- **根因**：`worker_manager` 既负责 worker 子进程 stdout 转发，又直接认识并修改主服务 `JOBS`；同时 `main.py` 还用 `_tracking_send()` 包住 `ws_manager.send` 同步 `JOBS`，同一件事有两条路径。
- **修复**：给 `WorkerManager` 增加 `set_progress_callback()`；`main.py` 注册 `_sync_job_from_worker_message()`；删除 `worker_manager` 对 `.main.JOBS` 的反向导入；取消 `ws_manager.send` monkeypatch。
- **验证**：TDD 红灯 `2 failed, 4 passed` → 修复后目标测试 `6 passed, 2 warnings`；追加 `done/error/token_usage` 状态同步覆盖后目标测试 `7 passed, 2 warnings`；最终全量测试 `627 passed, 8 warnings in 130.25s`。
- **服务**：旧 8766 服务 PID `78466` 为 6 月 21 日启动且无 `--reload`，已停止；新服务由 `screen` 会话 `25302.video-subtitle-v308-cycle` 托管，PID `25324` 监听 `*:8766`；`/api/health` 返回 `{"ok":true,"version":"3.07"}`。
- **记录**：详见 `pipelines/bug-pipeline-2026-06-26-worker-manager-cycle.md`。
- **判断**：这个循环依赖 bug 已修，可回到 V3.08 收尾；下一步仍是按收尾流程做全项目 HARDEN。

## 2026-06-25 V3.08：hallucination_filter 对话 A 函数复杂度拆分

承接 `pipelines/refactor-remaining-plan.md`，本轮只拆 `v3/backend/core/hallucination_filter.py` 四个目标函数，纯重构，只搬代码不改逻辑。

- **路径修正**：规划旧写 `backend/hallucination_filter.py`，真实活跃路径是 `v3/backend/core/hallucination_filter.py`。
- **拆分结果**：
  - `LightweightStemmer` **F(46) → B(8)**
  - `clean_and_stem` **F(45) → B(7)**
  - `_extract_qwen_sentences` **D(21) → A(4)**
  - `filter_hallucinations` **D(24) → B(8)**
- **抽出 helper**：词干器按语言拆出 `_stem_*`/`_strip_*` helper；Qwen 句子提取拆出词块收集和句子合成 helper；五层过滤主函数拆出 `_apply_layer1/_apply_layer2/_apply_layer4/_apply_layer3` 等阶段 helper。
- **全文件结果**：`hallucination_filter.py` 不再有 D/F 级函数，最高为 `_layer4_regex C(19)`、`_layer2_cross_validate C(18)`。
- **验证**：每抽一步均跑全量测试；最终 `cd v3 && .venv/bin/python -m pytest test/ -q` → **624 passed, 8 warnings**。
- **环境说明**：沙盒内全量测试会卡在 Hugging Face/Metal 访问；本轮全量测试均用非沙盒权限跑同一条命令。
- **记录**：详见 `pipelines/refactor-pipeline-hallucination-filter-2026-06-25.md`。
- **下一步**：推荐继续对话 C，拆 `v3/backend/translation/orchestrator.py::_translate_whole_with_markers`。

## 2026-06-23 V3.08：worker.main 函数复杂度拆分（F109 → C15）

纯重构，只搬代码不改逻辑（含日志/进度/emit/异常路径逐字保留）。worker.py 的 `main()` 是全项目最绕的函数（radon F109，比已拆 transcribe 的 41 还大 2.5 倍），单独开会话拆。

- **方法**：找出 main 干的 12 件大事 → 每件抽成顶层 helper → main 只剩编排调度。每抽一步「先替换 main 为调用、再插 helper 定义」，每批后台跑全量测试当安全网，绿了再下一步。
- **抽出 12 个 helper**：`_validate_config`(嵌套→模块级)、`_invalidate_stale_caches`、`_download_source_if_url`、`_step_extract_audio`、`_step_transcribe`、`_run_transcript_refiner`、`_llm_fallback_if_needed`、`_reflection_review`、`_step_translate`、`_step_subtitle`、`_step_burn_or_mux`、`_copy_outputs_to_save_path`。
- **结果**：`main` **F(109) → C(15)**（7 倍下降），全文件不再有任何 F/D 级函数。最复杂的 helper 是 `_step_translate` C(19)（翻译步骤内聚分支，远低于 F，不再碎拆）。
- **settings 字段提取刻意跳过**：那块直筒赋值、只 2 分支，抽出要改 main 里十几个变量名，churn 高收益低。
- **测试**：基线核实为 **624 passed**（交接旧数 615 已更正）。全程 5 个测试批次每次 624 passed，**零回归**。
- 详见 `pipelines/refactor-pipeline-worker-main-2026-06-23.md`。**未提交**，待用户确认或进发布线。
- 待拆名单剩余 9 个 D/F 函数（orchestrator `_translate_whole_with_markers` F44、hallucination_filter `LightweightStemmer`/`clean_and_stem` F46/F45 等），建议各开干净会话照先例处理。

## 2026-06-22 V3.08：外部工程师审查 → 修 P298（check_qwen_asr 异常吞噬）

外部工程师审本会话两个提交（已推 GitHub）：
- **`de6eb18`（④ 重构）：通过。** 核对了执行顺序不变、translated 三态都在、failed_indices 仍是同一列表（P230 重试删项 → 免费补全只补剩余，没断）。判定符合「纯重构、行为不变」。
- **`7a2089c`（健康检查）：发现 1 个 bug → 已修（P298）。** 见下。

### P298：check_qwen_asr 只接 ImportError，无 Metal 环境拖垮整份健康摘要
- 根因：`check_qwen_asr()` 第一步 `from mlx_qwen3_asr import transcribe` 只 `except ImportError`，但 mlx 在无 Metal 设备 import 抛 **RuntimeError** → 冒泡出 `check_all()` → startup 摘要在打印前异常退出，整份丢失。接进开机 startup 后每次启动都触发。
- 修法（两层，比工程师字面建议更治本）：① `check_qwen_asr` 改 `except Exception`（mlx 失败落 pytorch 兜底、pytorch 失败返回 `{ready:False,error}`）；② `check_all` 改循环逐个调用、每个裹 try/except，任一检查炸只标该键 not-ready，摘要必出（防住 sensevoice/resemblyzer 同类隐患）。
- TDD：`TestCheckAllResilience` 2 测试先红后绿。**全量 623 passed**。真活：本机 qwen_asr 仍 `{ready:True,backend:mlx}`，正常路径不变。
- **教训**：单机 /review 看不见跨环境失败路径——我在有 Metal 的 Mac 上判 clean，漏了这条。第二双眼睛/换环境才抓出，印证"先工程师审、再放大 worker.main"的排序。

### P299：qwen_asr_runner 顶层 MLX 导入只接 ImportError（工程师复验 P298 时连带发现）
- 工程师复验：`1896f13` 修好 P298 ✅；启动日志完整打出（他无 Metal 环境 qwen_asr 走 PyTorch 显示可用）；新增测试 8 passed。但全量测试仍跑不完——卡点已不是 check_all，而是 `qwen_asr_runner.py:26` 的**模块级** MLX 导入只接 ImportError，无 Metal 抛 RuntimeError → 模块 import 崩 → 收集到 test_p182 时断。
- 修法：模块级两个 `except ImportError` → `except Exception`，落 PyTorch 兜底。**扫全 backend 确认模块级 MLX 顶层导入仅此一处**（whisper_runner 的 8 处全在函数体内懒加载、安全；env_check 已 P298 修），所以补完整。
- TDD：`test_p299_qwen_runner_import.py`。**全量 624 passed**，本机正常路径不变。
- **追加修复（2026-06-23，工程师复验 + 乔布斯把关）**：0d940f3 后 MLX import 失败 → `_mlx_load_model`/`_mlx_transcribe` 名字未绑定 → test_arch7 的 `patch(..._mlx_load_model)` 报 AttributeError（无 Metal 2 failed）。修：try 前加模块级占位 `= None`（不占位 Qwen3ASRModel——探针、函数内本地 import、无人 patch）。**测试踩坑**：第一版用 pop+reload 复现，污染父包属性带崩 test_phase1c；改用「隔离命名空间 exec 源码」零污染。红→绿均验，全量 624 passed。
- 待工程师在无 Metal 环境复跑确认是否还有非 MLX 后续拦截点。

---

## 2026-06-22 V3.08：启动健康检查（后端）+ B路 P207 复核

### B路：核实「除法兜底静默造假时间戳」是否删净（乔布斯建议先焊船底）
- 读 `backend/core/qwen_aligner.py` 全文 + `problems.md` P207 记录。
- **结论：焊死了**。`align()` 走官方 `Qwen3ForcedAligner.align()`，失败 `raise`（不再静默），除法兜底 `duration/len(words)` 代码已整段删除，每词打 `_quality:"official"` 标签。
- 行为验证：`test_p207_fa_official_api.py` + `test_p201_qwen_aligner_check.py` **11 passed**（真音频）。
- P207 真根因（静默假数据）在**代码层**消除，非仅文档。残留 P212（真人声精度验证）属测试质量项，与假数据漏洞无关，另行跟踪。

### 启动健康检查（决策：只补后端，可见面板留 V3.09 PyWebView）
- 乔布斯视角分析（用户要求）：B 真活自检（开机喂音频跑模型）= 砍。交叉验证抓不到 P207（它比文字、烂的是时间戳），且每次开机跑模型太慢。启动只做 A 轻量静态检查。
- **发现 A 后端早已存在**：`env_check.py:check_all()` 查 6 类依赖/模型，挂 `/api/env`。缺口：前端没调（红绿灯没亮）、缺 yt-dlp 检查、无开机摘要。
- **本次补强（TDD：先红 5 failed → 实现 → 绿）**：
  - `env_check.py`：加 `check_ytdlp()`（包 `is_ytdlp_available`）塞进 `check_all()`；加纯函数 `health_summary_line()`（一行 ✅/❌ 摘要）+ `missing_checks()`（列未就绪项）。
  - `main.py`：`@app.on_event("startup")` 开头调 `check_all()` 打健康摘要日志，未就绪项额外 warning。
  - 新测试 `test/test_health_check_startup.py`（6 测试）。
- 真活输出（本机）：`✅ ffmpeg ✅ whisper ✅ resemblyzer ✅ sensevoice ✅ qwen_asr ✅ qwen_aligner ✅ ytdlp`，未就绪：无。
- 验证：**全量 621 passed, 0 failed**（615+6）。
- **未做（刻意）**：临时前端的可见红绿灯面板——前端 V3.09 换 PyWebView，留到那时做，不在要拆的墙上贴瓷砖。

### HARDEN：轻量 /review（clean）
- 对 commit `7a2089c` 的 diff 跑了 /review 核心：关键扫雷 + 交叉影响 + 对抗视角（<50 行真实逻辑，按 /review 自身门槛跳过专家军团/codex）。
- **交叉影响（重点）**：给 `check_all()` 加 `ytdlp` 键是纯 additive。逐一核对消费方——`/api/env`（多字段向后兼容，不假设固定键集）、前端（根本没调）、startup 钩子/health_summary_line/missing_checks（`_entry_ok` 泛化处理任意键）、`test_p201`（assertIn 不破坏）——**无一被污染**。
- **异常安全**：startup 健康检查整段裹 `try/except Exception`，抛错只 warning、startup 继续，不会拖垮启动。无循环导入（video_downloader 懒加载）。
- 1 条 INFORMATIONAL（不修）：check_all() 在 async startup 同步执行短暂阻塞事件循环，pre-flight 固有代价、服务对外前、符合预期。
- **结论：CLEAN，0 修复项，不动一行。**

---

## 2026-06-22 V3.08：拆分 ④ translate_with_orchestrator F61→B7 + ⑤ 核实不拆

**管道文件**：`pipelines/refactor-pipeline-2026-06-22-remaining5.md`（④⑤ 节 + 末尾「待拆名单」）

### ④ translate_with_orchestrator（本会话核心）
| 指标 | 结果 |
|------|------|
| 复杂度 | **F(61) → B(7)**（radon） |
| 抽出 helper | 6 个，全部 ≤17 |
| 测试 | **615 passed, 0 failed**（零退化） |

- 拆法分两波，每波跑全量测试：WAVE 1 抽 `_translate_llm_per_batch` + `_translate_free_only`（F61→F43）；WAVE 2 context_aware 内部细拆，再抽 `_run_llm_fallback_chain`(C17)/`_retry_failed_with_llm`(B9)/`_fill_gaps_with_free`(B7)/`_translate_llm_context_aware`(B7)。主函数精简为「准备 backends + 三分支调度」。
- 行为保真：translated 三态、failed_indices 同一 list 对象贯穿 retry→fill 的原地 mutation、guard 内置——逐字保留含日志。
- **又撞 1 个源码文本测试**（`TestP241RetryLanguage`，同 ③ P170 同类）：`inspect.getsource` 断言主函数含 `_TARGET_LANGUAGE`，重试逻辑搬进 helper 后误伤。按先例改成行为测试（mock 假 LLM 捕获实际 prompt 断言）。

### ⑤ build_ass_styled —— 核实后判定「不拆」
- 预估约 16（搬家前旧数据），**实测 radon B(6)**，62 行直路。已属低复杂度，硬抽 helper 是过度工程 → 判定不拆。
- **本轮「余下 5 函数拆分」收官**：①②③真拆(已提交) + ④真拆(待提交) + ⑤核实不拆。

### 新发现：待拆名单（留新会话单独干）
- 扫全 `v3/backend/` 发现真热点：`worker.py:main` **F(109)**、`hallucination_filter` 词干器 F46/F45、`orchestrator._translate_whole_with_markers` F44，及一批 D 级。均不在原 5 函数清单内。
- worker.main 体量超 transcribe 2.5 倍，按先例单独开干净会话拆。完整名单 + 下会话话术存管道文件末尾。

### 待提交
- ④ 的 orchestrator.py + test_harden_v307_sprint2.py 改动**尚未提交**，工作区有改动。

---

## 2026-06-21 V3.08 开发线：拆分 transcribe() 复杂度 41→10

**流水线**：开发线 DECIDE → BUILD（HARDEN 待跑）。管道文件 `pipelines/dev-pipeline-2026-06-21-v308-transcribe-split.md`

### DECIDE
- planning-with-files-zh 恢复上下文 + 测试基线：真机复现 **614 passed, 1 skipped, 0 failed**（97s）
- steve-jobs-perspective 减法：15 件事砍到「本轮只杀一条龙」=拆 transcribe；启动健康检查（FEATURE）剥离出本轮，不与纯重构同版本

### BUILD（核心成果）
| 指标 | 结果 |
|------|------|
| transcribe 复杂度 | **41 → 10**（radon），降 76% |
| 抽出 helper | 16+ 个职责函数，全部 ≤10 |
| 测试 | **615 passed, 0 failed, 0 skipped**（零退化；1 个原静默跳过的测试改为真断言通过）|
| 顺手擦 | 删空壳 `_assemble_qwen_on_whisper_boundaries` |

### 拆分方法（逐块搬运 + 每步验证）
按职责把 transcribe 的 20 个阶段块逐个搬进 helper：VAD gate → Whisper段迭代 → 等Qwen路由 → SV+FA派发/等待/应用/质量(再子拆5叶子) → 语言推断 → 人声地图 → L1交叉验证 → 组装合并 → 五层过滤 → 收尾。每搬一块跑一次快速子集，全搬完跑全量。

### 关键决策与发现
- **6 个源码文本检查测试**随重构更新（用户批准 Option A）：它们检查字符串是否在 transcribe 源码里（非行为测试），抽函数必撞红。改为检查模块/对应 helper，意图保留、行为可证明未变。
- **P296**：Phase C Qwen 并行线程闭包 late-binding 竞态（原代码就有），刻意保留不动，单独登记。
- **P297**：`.gitignore:14 test/` 把整个 `v3/test/` 排除出版本控制——测试套件从未被 git 跟踪。重大旁支发现，待用户决策。

### 待办
- HARDEN（/review 扫 diff + 设计一致性走查）未跑
- 提交前需先解决 P297，否则 6 个测试改动进不了 commit

## 2026-06-21 V3.08 搬家 + 清理

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 停后端服务 | ✅ | PID 60923 端口 8766，停掉后搬家 |
| v2/ → v3/ 重命名 | ✅ | 活跃代码目录从 `v2/` 改为 `v3/` |
| 快照移出 | ✅ | v2.10~v3.07 共 10 个快照移到 `snapshots/` |
| v2/v2 嵌套目录 | ✅ | v3.06 快照先救出，v2/v2 删除 |
| 根目录 .venv 清理 | ✅ | 1.7G V1 遗留虚拟环境 → _trash |
| v3.05/.venv 清理 | ✅ | 1.2G 快照虚拟环境 → _trash |
| .git.* 垃圾清理 | ✅ | .git.broken/.git.old/.git.trash/.git.trash2 → _trash |
| .hex-skills 清理 | ✅ | 根目录(20M) + v3/(19M) → _trash |
| egg-info 清理 | ✅ | video_subber.egg-info + video_subber_web.egg-info → _trash |
| 快照 test 目录清理 | ✅ | v3.02~v3.05 的 test/ 目录 → _trash（P271 pytest 撞名修复） |
| .superpowers/.pytest_cache 清理 | ✅ | → _trash |
| CLAUDE.md 更新 | ✅ | v2/ → v3/，快照说明更新 |
| .gitignore 更新 | ✅ | 删除已不存在的 .git.* 条目，新增 _trash/ snapshots/ |
| run-test.sh 注释更新 | ✅ | v2/.venv → v3/.venv |
| 全量测试 | ✅ | 614 passed, 1 skipped, 0 failed（零退化） |
| 后端服务重启 | ✅ | PID 78466 在 v3/ 下监听 127.0.0.1:8766 |

### 清理统计

- 垃圾暂存 `_trash/`：13 项，共 2.9G
- 活跃虚拟环境：仅保留 `v3/.venv`（1.5G）
- 快照统一目录：`snapshots/`（10 个版本）

---

## 2026-06-21 V3.07 发布

### 版本收尾线（7 步全部通过）
- ① 双签 diff ✅ — 发现并修复 P294（Mac check_whisper 误报绿灯）、P295（ffprobe ×2 缺异常保护）、代理 URL 日志泄漏密码
- ② 设计一致性走查 ✅ — 4 处代码比设计图多（VAD/SV语言推断/人声地图/短字幕合并），均合理，归入 V3.08 更新设计图
- ③ 安全审查 ✅ — 代理 URL 已脱敏，XSS 延至框架切换，Refiner 双模式均有注入保护
- ④ 架构分析 ✅ — 无循环依赖，transcribe() 复杂度 41 归入 V3.08 拆分
- ⑤ 全量测试 ✅ — 614 passed, 1 skipped, 0 failed
- ⑥ QA ⏭ 跳过（CLI 项目）
- ⑦ 端到端 ✅ — 12 项真实视频验收已通过

### 发布线
- ① verification ✅ — 614 passed, 0 failed
- ② 版本号 ✅ — pyproject.toml version="3.07"（已是正确版本）
- ③ git commit + tag ✅ — `5302d9d` + tag `v3.07`
- ④ 只读快照 ✅ — `v2/v3.07/`（22MB，chmod a-w）
- ⑤ 进度文件更新 ✅

### P1/P2 遗留项（8 项归入 V3.08）
详见 task_plan.md V3.08 章节第 256-267 行

## 2026-06-21 真实验收全部通过

### MIDA-659.ts（8191秒日语）标准 vs 精准对比

| 指标 | 标准模式 job `16a692d74e` | 精准模式 job `98ac3271c2` |
|------|--------------------------|--------------------------|
| [?]率 | **2.0%** (43/2057) | 38.4% (794/2068) |
| 耗时 | ~34分钟 | ~65分钟 |
| Refiner | 317处修正 single mode | 238处修正 single mode |
| 主引擎 | Qwen3-ASR（标准路由） | Qwen3-ASR（P254修复后正确路由） |

**P285 验收通过**：标准模式 Whisper 4秒探语言 → 释放 → Qwen 单独跑，不再卡住。
**P289 验收通过**：L2 [?]率从95.2%降到38.4%，剩余问号均为真实重复叫声（うんうんうん/ああああ等）。
**P291 验收通过**：Refiner 滑窗生效，9窗口共238处实际修正，不再 corrections=0。
**P287 验收通过**：精准模式 786/1022 段带 whisper_text，数据回填正常。

### London VLOG（英语）P284 验收

**P284 验收通过**：用户清缓存后重新跑 London VLOG，clock/claw 问题已修复，"Hey Taylor" 断句正常。（cache 已清，日志不保留，用户人工确认）

**P266 验收通过（基础）**：标准模式短探测使用 MLX Whisper 正常返回日语识别结果（4秒，概率0.92）。

### 观察/待修记录
P292（观察结论）：Mac Apple Silicon 上精准模式质量反不如标准模式，根因是 Whisper 幻觉污染 L2，70% 窗口 LOW_DATA。登记为已知行为，V3.08 阶段评估是否改善。
P293（记录待修）：标准模式 Qwen 跑期间前端进度条卡在"正在启动语音识别…"，换 PyWebView 框架（V3.09）时修。

**V3.07 所有验收项全部通过，可进入发布线。**

---

**日期**：2026-06-20
**版本**：V3.07 P289 L2 交叉验证修复
**上一版本**：V3.07 P287 whisper_text 回填

## 2026-06-20 P289 修复

- P289：L2 交叉验证在千元主引擎时失效（左右两边都放千元数据，95.2% 字幕标 [?]）
- 修复方案：路由层交换主副引擎时，同步交换 L2 的左右输入（`alt_segments` 参数）
- 完成 bug 修复流水线 ①-⑧（investigate → 双签审查 → 双签影响面 → 双签 diff → 设计走查 → TDD → verification）
- 测试：P289 专项 6/6 passed，全量回归 600 passed（1 个环境相关已有失败不影响）
- 待真实 GPU 环境用 MIDA-659.ts 精准模式端到端验收

---

**日期**：2026-06-17
**版本**：V3.07 问题状态全面核实 + 规划文件整理
**上一版本**：V3.07 P247 安全阀完成 + P265 重组碎片化定位

---

## 2026-06-18 — 竞品对比落盘（JAV 场景专项）

**分支**：l1-voice-timeline-and-standard-mode

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 外部项目对比 | ✅ | 对比 `SubForge / VideoCaptioner / MioSub / VideoLingo / SmartSub / pyVideoTrans / sub2api` 的公开 README 和功能说明 |
| JAV 问题口径统一 | ✅ | 按用户澄清，将这次对比聚焦到 JAV 场景下的 `[?]/uncertain`、单字字幕、短促叫声、时间轴可信度 |
| 新建长期对比文件 | ✅ | 新增 `competitor-comparison.md`，作为以后持续记录“我们 vs 其他产品”的专用文件 |
| 乔布斯式减法分析 | ✅ | 明确本阶段不建议再加 ASR 模型，也不建议立刻走模型微调；优先做人声地图、短词并段、低分段返修 |

### 本轮结论

- 这批项目里 **VideoLingo** 最值得借：词级对齐、字幕分割、术语库、暂停恢复，都直接碰到我们当前痛点。
- **SubForge** 值得借的是“低分段定点返修”，而不是再堆模型。
- 公开资料里没看到谁明确靠“自训 / 微调 ASR 模型”解决 JAV 问题，主流做法还是 **换模型 + 对齐 + 分段 + 后处理**。
- 对我们来说，当前更值得做的是 **减法**：把内部质量信号和用户问号继续分开，把短词和非人声孤立段继续收紧，而不是继续加链路。

---

## 2026-06-19 — 后台任务实时监控

**分支**：main

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 后台任务快照 | ✅ | 扫描 `~/.video-subber/jobs/*/meta.json`，确认当前唯一 `running` 任务为 `48b739a158`（文件 `MIDA-659.ts`） |
| 运行中任务日志追踪 | ✅ | 连续轮询 3 次，`user.log` / `debug.log` / `meta.json` 时间戳都停在 `2026-06-19 21:41:38` |
| 卡点定位 | ✅ | `48b739a158` 最后一条日志停在 `加载 Whisper large-v3 模型…`；`checkpoint.json` 仍写在 `extract_audio` 阶段，说明状态和真实进度不一致 |
| 最近失败任务复核 | ✅ | `2049795afe`、`05cbec0727`、`01bdd52151` 都是 URL `https://www.youtube.com/watch?v=abc`，被 `yt-dlp` 判定为截断链接，不是转录链路回归 |
| 本地接口可达性检查 | ✅ | `lsof` 显示 `127.0.0.1:8766` 有 Python 进程监听，但 `curl /api/active-jobs` 连不上，当前监控改走 job 目录和日志实物 |

### 本轮结论

- 现在真正值得盯的是 `48b739a158` 这一个本地文件任务，不是多任务同时跑满。
- 这条任务大概率已经卡住，不像正常运行中任务。理由是 15 秒轮询期间没有任何新日志、新时间戳或阶段推进。
- 最近那 3 个 URL 报错属于输入链接本身无效，不代表下载器或转录流程整体坏了。

---

## 2026-06-19 — 标准模式为什么先看到 Whisper

**分支**：main

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 标准模式代码口径核对 | ✅ | 当前 `transcriber_v2.py` 的设计就是“先用 Whisper 探语言，再决定继续 Whisper 还是切 Qwen” |
| 日志含义拆分 | ✅ | `user.log` 里的 `tier=precise` 来自硬件推荐档位，不等于用户实际选择了精确模式 |

### 本轮结论

- `meta.json` 里这条任务的真实配置仍是 `transcriptionTier=standard`。
- 之所以先看到 `加载 Whisper`，是因为标准模式要先拿 Whisper 做语言判断；只有识别出 `ja/zh/en/ko` 且置信度够高，才会释放 Whisper 再切到 Qwen。
- 当前监控到的 `48b739a158` 卡在 `加载 Whisper large-v3 模型…` 之前这一步，所以还没走到“是否切 Qwen”的分支日志。

## 2026-06-19 — P285 标准模式 Whisper 语言探测卡住诊断

**分支**：main

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 后台状态复核 | ✅ | 新任务 `a58b786e34` 为唯一活跃任务，后端返回 `running/transcribe/progress=10/process_alive=true` |
| CPU/内存复核 | ✅ | worker `59930` 持续运行，CPU 可到 `110.7%`，累计 CPU 时间增长；`top` 显示内存曾到 `20G` 级别 |
| 输入音频复核 | ✅ | `audio.wav` 时长 `8191.168s`，约 2 小时 16 分 31 秒 |
| 代码路径复核 | ✅ | 标准模式直接调用 `transcribe_whisper(audio_path, ...)`，没有截取第一段；`mlx_whisper.transcribe()` 对整条音频启用 `word_timestamps=True` |
| 问题登记 | ✅ | 新增 `problems.md` P285，记录标准模式语言探测误跑整条长音频的问题 |

### 本轮结论

- 这不是前端误显示，也不是 worker 已崩溃；worker 还活着，并且在 MLX/Metal 的 Whisper 路径里消耗 CPU。
- 标准模式的设计是“Whisper 只跑第一段判断语言”，但当前实现把 2 小时多的完整音频交给 Whisper。
- 前端一直显示“正在启动语音识别…”是因为进度回调要等 `transcribe_whisper()` 返回后才继续触发；当前调用长时间不返回。
- 本轮只读诊断，没有修改代码。下一步应按 bug 修复流水线处理 P285。

## 2026-06-19 — P285 标准模式 MLX Whisper 短探测修复

**分支**：main

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| bug 管道文件 | ✅ | 新增 `pipelines/bug-pipeline-2026-06-19-p285-standard-mlx-language-probe.md` |
| MLX 短探测入口 | ✅ | `whisper_runner.py` 新增 `detect_language_with_whisper()`，标准模式只抽短音频做语言判断 |
| 标准模式路由修复 | ✅ | `transcriber_v2.py` 先探语言；适合 Qwen 时直接进入 Qwen，只有 fallback 才完整跑 Whisper |
| 回归测试 | ✅ | 覆盖短临时音频、`word_timestamps=False`、标准模式 Qwen 路径不调用完整 Whisper |

### 验证

- 目标测试：`v2/.venv/bin/python -m pytest v2/test/test_p266_mlx_whisper.py v2/test/test_standard_precise_mode.py v2/test/test_p276_standard_language_env.py -q` → `25 passed`。
- 语法检查：`python3 -m py_compile v2/backend/core/whisper_runner.py v2/backend/transcriber_v2.py` → 通过。
- 全量测试：`v2/.venv/bin/python -m pytest v2/test -q` 在当前 Codex/headless 环境收集阶段被 Metal/GPU 限制挡住，错误为 `No Metal device available`。
- 后台检查：未匹配到 `backend.worker`、`ffmpeg`、`mlx_whisper`、`Qwen`、`Whisper` 残留进程；当前高 CPU 主要来自 `WindowServer` 和 Codex。

### 当前边界

- 代码层已修复。
- 仍需在普通 Mac 前台环境用标准模式重跑 `MIDA-659.ts`，确认前端不再卡在“正在启动语音识别…”，并能进入 Qwen 转录。

---

## 2026-06-17 — 问题状态全面核实 + 规划文件整理

**分支**：l1-voice-timeline-and-standard-mode

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 逐个核实所有待办问题的代码状态 | ✅ | 对照 git 历史和当前工作区代码，确认每个问题的真实修复情况 |
| 更新 problems.md | ✅ | 重写遗留未修汇总：P181/P223/P245/P258/P260/P261 确认关闭；P271+P282 合并；P272/P237 确认仍待修 |
| 更新 task_plan.md | ✅ | V3.07 待做表重新分类：已完成 16 项、待修 2 项、待观察 1 项、后续版本 5 项、已关闭 1 项 |
| 更新全局 task_plan.md | ✅ | 同步最新状态 |

### 核实结论

- **P181**：commit `0a8b5fa` 早就修了，从遗留表移除
- **P223**：核心问题（O(n²) 内存爆炸）被 P220 `align_segments()` 逐句对齐解决，关闭
- **P237**：`_crash_snapshot()` 函数写好了但没接入异常处理，差一行闭环
- **P271+P282**：合并为一个目录清理任务，V3.08 搬家时统一处理
- **P273**：P274 修了 5 秒偏移，P275 Phase 1 人声地图数据已生成，Phase 2 接入待后续
- **P276-P279、P281**：代码全部已修，在工作区未提交

---

## 2026-06-15 — ASR 后端服务器重启

**分支**：ASR

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 停旧服务 | ✅ | 已停止旧 PID `7820`，该服务启动于 2026-06-14，未加载 2026-06-15 的 P273 修改 |
| 前台验证 | ✅ | 前台启动 uvicorn 后，健康接口返回 `{"ok": true, "version": "3.07"}` |
| 后台启动 | ✅ | 已用 `screen` 启动后台会话 `video-subtitle-asr-server` |
| 端口确认 | ✅ | 新 PID `57763` 正在监听 `*:8766` |
| 目录确认 | ✅ | 新进程工作目录是 `/Users/andy/Andy-agent/video-subtitle/v2`，当前仓库分支是 `ASR` |

### 补充

- 后台启动日志：`/Users/andy/.video-subber/server-restart-2026-06-15.log`
- 后台健康接口二次检查因当前 Codex 提权额度限制未能执行；端口监听、启动日志、前台健康检查均已确认服务可运行。

---

## 2026-06-15 — 当前服务器状态检查

**分支**：ASR

### 检查结果

| 项目 | 状态 | 说明 |
|------|------|------|
| 服务端口 | ✅ | `8766` 有 Python 进程监听，PID `7820` |
| 服务目录 | ✅ | 进程工作目录是 `/Users/andy/Andy-agent/video-subtitle/v2` |
| 当前分支 | ✅ | 仓库当前分支是 `ASR` |
| 代码是否进入当前服务 | ⚠️ | 服务启动时间是 `2026-06-14 22:15:42`，P273 修改文件 `transcriber_v2.py` 修改时间是 `2026-06-15 10:45:26` |
| 是否需要重启 | ✅ | 启动命令是 `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8766`，没有 `--reload`，因此当前服务未自动加载 P273 修改，需要重启后再重跑 FJIN-141 |

### 补充

- 当前沙盒禁止我直接 `curl localhost:8766`，报 `Operation not permitted`；端口监听状态通过 `lsof` 确认。

---

## 2026-06-15 — FJIN-141 P273 重跑前缓存清理

**分支**：ASR

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 缓存清理 | ✅ | 已将 `/Users/andy/.video-subber/jobs/65b1f1b3db/cache` 移到 `/Users/andy/.video-subber/archives/cache-clear-2026-06-15-p273-rerun/65b1f1b3db/cache` |
| 日志保留 | ✅ | `/Users/andy/.video-subber/logs/65b1f1b3db/` 原样保留，便于新旧日志对比 |
| 字幕文件保留 | ✅ | `FJIN-141.srt` 和 `FJIN-141_zh_sub.mp4` 原样保留在 job 目录，便于新旧字幕/成品对比 |
| 状态复位 | ✅ | `meta.json` 的 `status` 已从 `done` 改为 `interrupted`，避免前端继续复用旧完成态 |

### 结论

- 现在活跃 job 目录已无 `cache/`，下一次提交/重跑应重新生成转录、翻译和字幕缓存。
- 本次没有删除日志，也没有删除旧字幕文件。

---

## 2026-06-15 — FJIN-141 三次测试口径澄清

**分支**：ASR

### 追加记录（外部工程师 P273 方案复核）

- 外部工程师对 49:37-50:00 的拆解基本成立：主要根因应从“翻译错位”前移到 Qwen 组装层。
- 已核对代码：`_words_to_segment()` 没保存 `seg.words`；`word_align_segments()` 遇到 `words=[]` 会跳过；`_build_split_segments()` 和 `_rule_based_split()` 使用中文字符比例分配时间。
- P273 已补充为三点修复：保留词级时间戳、改善 Qwen 长短段组装、拆分字幕时优先用词级时间戳而不是字符比例。
- 四个暂缓观察项重新评估：当前只解冻“组装 fallback/组装质量”这一项，并升级为 P273 主组装修复；L3 策略、翻译 prompt 场景化、对白价值判断层继续暂缓，等 P273/P272 修完并真实重跑后再决定。
- 二次审查吸收：工程师指出 Qwen 日语输出无标点，导致 `_SENTENCE_ENDERS` 断句命中率为 0。P273 主方案已调整为“保留词级时间戳 + 日语句尾/微停顿断句 + 温和硬限制兜底”，砍掉复杂输出前整形。
- 双签审查吸收：P273 最终采用强/弱两级日语句尾规则；删除高误判的 `れ/くる`，`の/さ/で` 提高到 `gap>=0.3s`，补 `これ+0.2s` 不断句测试。方案可进入编码阶段。
- P273 TDD 实现：新增 `test_p273_qwen_ja_assembly.py`，先确认 RED；随后修改 `transcriber_v2.py`，让 `_words_to_segment()` 保留 `seg.words`，并在 `_assemble_qwen_primary()` 中启用日语强/弱句尾 + 微停顿断句，日语硬限制改为 `5s/25字`。
- P273 验证：相关测试集合 66 条通过（P273 + P268/P269 + Qwen 窗口 + Qwen 健康 + 时间戳相关）。真实 FJIN-141 长视频仍待重跑确认最终字幕质量。

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 三次测试结论对齐 | ✅ | Run 3 相比 Run 1/Run 2 确实有底层改善：Whisper 正常产出 3813 段，L1 没有再硬删，L4 CJK 重复保护生效，Refiner 进入 single 模式 |
| 最终字幕问题拆分 | ✅ | 将用户看到的 49:37 空洞、49:44 错成“啊啊啊啊”、50:01 多行瞬闪拆成两个后段问题：翻译写回错位/污染，以及 Qwen 词级组装产生长空洞和极短段 |
| 证据补充 | ✅ | 复核 `FJIN-141.srt` 49:20-50:20、`transcript.json` 455-474、`translated.json` 455-474、`debug.log` 接缝审查返回内容 |

### 澄清结论

- 其他工程师给出的三次测试表，证明 P268/P269/P270 的底层修复部分生效；这不否定 P272/P273。
- P272/P273 关注的是最终字幕层：ASR 识别到了，不等于翻译写回正确；字幕段存在，不等于时间轴可读。
- 下一步仍建议按 bug 修复流水线继续，不直接跳代码。

---

## 2026-06-15 — ASR 分支 FJIN-141 新旧结果对比

**分支**：ASR

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| 新旧字幕对比 | ✅ | 对比 `65b1f1b3db` 新结果、`b544184558` 旧结果、`4754e3947e` 更早结果，重点检查 49:20-50:20 |
| 用户观察复核 | ✅ | 49:37 左右确有字幕空洞；49:44 出现“啊啊啊啊”；50:01 附近连续出现多条 0.1s 级短字幕 |
| 翻译污染定位 | ✅ | 新结果 `translated.json` 至少 90 条译文含“版本A/版本B/选B”解释文本，根因指向 `_review_seam()` 接缝审查返回未过滤 |
| 组装问题定位 | ✅ | 新结果 1049 段中 157 段短于 0.2 秒，433 个大于 3 秒空洞；49:20-50:20 内全部 14 段均为 `uncertain=True` |
| P272/P273 登记 | ✅ | 已写入 `problems.md` 和 `task_plan.md`，下一步应按 bug 修复流水线继续，不直接改代码 |

### 初步结论

- P268/P269/P270 有效果但不够：数字污染减少，SenseVoice 覆盖从旧的 3 段恢复到新结果 1326 段，但最终字幕仍不可用。
- 现在有两个新主因：① 翻译接缝审查把解释写进字幕；② Qwen 词级组装产出空洞和瞬闪短字幕。
- 下一步应进入 P272/P273 的完整 bug 修复流水线：先方案审查，再影响面，再 TDD 修复。

---

## 2026-06-14 — ASR 分支进度冲突确认 + 两次 FJIN-141 测试初步分析

**分支**：ASR

### 记录同步补充

| 事项 | 状态 | 说明 |
|------|------|------|
| P258 状态同步 | ✅ | 代码已把 `total` 和 `qm_count` 放在同一阶段计算，问题库顶部待修表已改为已修 |
| P254 状态同步 | ✅ | 主引擎切换已实现，`task_plan.md` 和全局 `/Users/andy/task_plan.md` 已同步；标准模式 Qwen 自检语言仍作为后续独立优化保留 |
| P237 状态纠偏 | ✅ | 代码已有 `_crash_snapshot()`，但异常处理处未调用快照写入，不能算闭环；已从问题库顶部“已关闭/已修”汇总中移除，正文状态改为“部分实现，未闭环”，待做项保留 |

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| P265 进度冲突核查 | ✅ | `task_plan.md` 仍写 P265 待做，但 `progress.md` 和 `problems.md` 都写 P265 已修。判断：冲突真实存在，`task_plan.md` 未同步 |
| P265 状态同步 | ✅ | 已把 `task_plan.md` 待做表中的 P265 从 ⬜ 改为 ✅，与 `progress.md` / `problems.md` 保持一致 |
| 两次 FJIN-141 测试对比 | ✅ | 第一次 2249 段、1418 段纯数字；第二次 944 段、纯数字 0，但 883 段 uncertain。判断：第二次修掉数字污染，但暴露覆盖率、切段和 L2 标记问题 |
| 问题库读取 | ✅ | P265 已修；P260/P261 已修；第二次测试后的段密度不均、SenseVoice 依赖 Whisper、L2 日语失效尚未登记为新 P 编号 |
| SenseVoice 数据依赖核查 | ✅ | SenseVoice 的声音判断来自音频本身，但当前长音频分段输入来自 `_sv_target`；Qwen 主引擎时 `_sv_target = _whisper_segments`，所以 Whisper 段数会影响 SenseVoice 覆盖范围 |
| L1/Whisper 规划偏差复查 | ✅ | 规划要求 `SenseVoice × Qwen ASR 内部 FA 时间戳` 验证 Qwen 原稿；当前代码实际是把 SenseVoice 标签贴到 Whisper 段，L1 遍历 Whisper 段，再用 Qwen 时间戳判断重叠，最后把结果搬到 Qwen 组装段 |
| P268 登记 | ✅ | 已新增到 `problems.md` 和 `task_plan.md`：L1 不应跟主引擎绑定，应固定使用 SenseVoice × Qwen 时间戳验证；当前仍以 Whisper 段为承载段。编号改为 P268，避免与顶部路线图中已预留的 P266/P267 冲突 |
| 两次日志分层病因分析 | ✅ | 除 L1 外，还确认了组装段密度不均、L2 日语验证失效、L4/L3 对叫声/短促声音过度触发、P247 Refiner corrections=0、第一次翻译受数字污染影响严重；第二次翻译本身成功但输入素材已缺段 |
| P269 登记 | ✅ | 已新增到 `problems.md` 和 `task_plan.md`：P247 Refiner 当前只有双稿冲突裁判，Whisper 参考稿缺失时不会对 Qwen 单稿做句意疏通，导致第二次 FJIN-141 corrections=0 后直接放行 |
| 外部审查文档 | ✅ | 新增 `ASR成人片场景问题-外部审查文档.md`，完整记录 FJIN-141 两次测试的起因、经过、根因、P268/P269/组装/L2/L3/L4 解决方案和代码级落点 |
| Prompt 方案补充 | ✅ | 已把 Refiner prompt 问题补入 `ASR成人片场景问题-外部审查文档.md`：区分 Refiner 原文校准 prompt 和翻译场景化 prompt，补充日语成人片多义词、语气词、叫声处理规则 |
| 外部二轮审查吸收 | ✅ | 重新对照代码确认：最终方案从原 8 项收敛为 4 项，保留 P268、L2/Refiner 统一健康判断、P269 dual/single、L4 CJK 重复最小保护；组装只加日志，L3 和翻译 prompt 暂缓 |
| 四步实现方案草案 | ✅ | 已根据外部二轮回复整理可转发的代码修改方案：P268、Whisper 健康判断共用、P269 dual/single、L4 CJK 重复保护；明确 NFKC 标准化和 single 模式 corrections=0 不清 uncertain |
| 四个实现缺口补齐 | ✅ | 已吸收第三轮技术回复：补齐 L1 标记搬运、SV 误杀保护、Refiner single 候选函数、L4 `_RE_REPEATED_WORDS` 分支；确认这些是编码细节，不改变 4 步方案边界 |
| 修复方案最终锁定 | ✅ | 对方工程师确认 4 个缺口全部通过：L1 标记搬运、SV 误杀保护、Refiner single 候选函数、L4 重复词 CJK 保护；下一步进入 TDD 实现，不再继续方案审查 |
| bug 流水线纠偏启动 | ✅ | 用户指出应先走 bug 流水线，提醒成立；已删除误新增的 TDD 测试文件，新增 `pipelines/bug-pipeline-2026-06-14-p268-p269.md`，完成 ① investigate、② 双签审查、③ 双签影响面 |
| P268/P269/P270 bug 流水线实现 | ✅ | 已按 10 步流水线补齐：P268 SenseVoice+L1 改走 Qwen L1 时间线；P269 Refiner 增加 dual/single，single corrections=0 不清 uncertain；P270 L4 CJK 重复短句不硬删。相关路径 110 测试通过；worker 冒烟生成 SRT 且保留 `[?]`；完整 FJIN 长视频待真实环境重跑 |
| 四个暂缓观察项 | ✅ | 已在管道文件记录，且在 `task_plan.md` 补成 FJIN 重跑后的观察项：组装 fallback、L3 策略、翻译 prompt 场景化、对白价值判断层。先不做，等真实 FJIN 重跑结果决定是否加入 |
| P271 测试目录污染 | ⏸ | 已登记到 `problems.md` 和全局 `task_plan.md`：当前 `v2/` 混有旧版本测试目录，pytest 全量收集会撞名，影响当前版本验证。建议提前整理测试范围 |
| FJIN 缓存清理 | ✅ | 已保留 `/Users/andy/.video-subber/logs/`，将 FJIN 旧缓存和旧输出归档到 `/Users/andy/.video-subber/archives/cache-clear-2026-06-14-p268-rerun/`；`4754e3947e` 和 `b544184558` 状态从 `done` 改为 `interrupted`，避免继续显示旧完成结果 |

### 初步结论

- 当前不是封版状态。ASR 分支已经进入真实视频验证后的新问题分析阶段。
- P268/P269/P270 已完成代码修复和相关路径验证。
- 当前不能说“全量验证全绿”：pytest 全量受 Metal/GPU、旧版本测试目录撞名、`yt_dlp.__spec__` 测试污染影响，无法在当前沙盒完成。
- P271 测试目录污染已经影响当前版本验证，建议提前修，不等到下个版本。
- FJIN 旧缓存已清理并归档，历史日志保留；当前环境无法启动 8766 后端端口，重跑需用户从本机前端重新提交或等权限恢复后再由后端接口触发。
- 下一步应在真实开发环境重跑 FJIN-141 端到端，重点看 SenseVoice/L1 覆盖率、问号数量、CJK 重复短句保留情况、Refiner single 模式修正数量。

---

## 2026-06-13 — P247 安全阀 + P262 短词豁免恢复 + P263/P264 双稿裁判重构 + P265 重组碎片化定位

**分支**：ASR

### 完成事项

| 事项 | 状态 | 说明 |
|------|------|------|
| P247 Plan C 安全阀 | ✅ | `_clean_dist()` 归一化编辑距离，阈值 0.80，逐段拦截。FJIN-141 实测 843→0 [?] |
| P262 短词豁免恢复 | ✅ | `_is_short_word_exempt` 恢复并加固（短词 + SV Speech 标签 → 豁免） |
| P263+P264 双稿裁判重构 | ✅ | 预过滤 + 差异标注 + 锚点分组，token 浪费从 80%+ 降到按需 |
| P265 重组碎片化修复 | ✅ | 两个 bug 已修：CJK 无空格拼接 + 中点落入判定 + 防御性排序。外部工程师 review 通过 |

### 改动文件

- `worker.py`：新增 `_clean_dist()`、`_PUNCT_RE`，替换 corrections 循环为单段隔离安全阀
- `hallucination_filter.py`：恢复 `_is_short_word_exempt`

### 新增文档

- `P265-重组碎片化问题-工程师文档.md`：自包含文档，交给外部工程师阅读

### 新增问题

- P265：Qwen 词级重组碎片化（CJK 语言），50.4% 段带不该有的空格，46% 内容错位

---

## 2026-06-12 — P256 L2 智能窗口边界（Mac 端完成）

**分支**：ASR

### 完成事项

裁剪版 6 步流水线全部通过：

| 步骤 | 状态 | 关键结论 |
|------|------|---------|
| ① office-hours | ✅ | 5 步实施计划 |
| ② TDD | ✅ | 21 新测试，修 2 个实现 bug |
| ③ 双签影响面（codegraph + bill-gates） | ✅ | 零外部 API 变更，系统安全 |
| ④ 双签 diff（/review + elon-musk） | ✅ | 7 findings（2 confirmed + 5 plausible） |
| ⑤ 设计一致性走查 | ✅ | 通过，1 处设计文档缺口 |
| ⑥ verification | ✅ | 512/513 全量零 P256 回归 |

### 改动文件

- `hallucination_filter.py`：新增 `_extract_qwen_sentences()`、`_build_smart_boundaries()`，修改 `_assign_by_timestamps()`、`_assign_qwen_to_windows()`、`_layer2_cross_validate()`
- `test_hallucination_filter.py`：21 新测试（6 提取 + 10 边界 + 3 归窗 + 2 端到端）

### 审查发现（记入未来清理）

- 死代码 L556-557、BGM O(W×S)、常量双源、三次迭代、多次调用、ffprobe 重复

---

## 2026-06-11 — V3.08 规划重整（乔布斯审查 + 换库影响分析）

### 换库影响分析结论

换 MLX ASR 库（gabrimatic→moona3k）后，千问自带词级时间戳 + MLX FA + 独立运行能力。三个问题自然关闭：

- ❌ **P179**（L2 接入 Whisper 逐字时间戳）— 千问自带词级时间戳，L2 归窗已精确化（`win = int(seg["start"] // 30)`），加 Whisper 维度是画蛇添足
- ❌ **P212**（FA 真人语音验证）— 外部 FA 调用（`QwenAligner` + `align_segments`）随换库被删除，FA 验证并入启动健康检查（心机音频）
- ❌ **P149/P171**（L2 门槛校准）— 精确时间归窗替代相似度猜窗口，门槛从需人工校准变默认值够用

### 保留并更新的问题

- **P247** 描述修正：Whisper 版本 → 主引擎版本，严重度 LOW→MEDIUM，换库后 L1 信号更可靠
- **P254** 优先级提升：从"未来想法"移入 V3.08 HARDEN 延后项，HIGH 优先级。换库后 Qwen 可独立运行，路由价值翻倍
- **P237**（黑匣子日志）不受影响，保留。P245 已由 bug 流水线判假 bug（asyncio 单线程原子）

### 文件同步

- `task_plan.md`：5 处更新（P149/P171 关闭、P179/P212 删除、P247 描述修正、P254 提升到 V3.08、总览表更新）
- `problems.md`：6 处更新（P179/P212 标关闭、P247/P254 描述更新、顶部汇总表更新）
- `MLX-ASR-换库方案.md`：未来想法描述更新（影响范围扩大）

### 当前 V3.08 待处理清单

| 编号 | 问题 | 类型 | 严重度 |
|------|------|------|--------|
| P254 | 转录智能路由 | 🏗 新功能 | 🟡 HIGH |
| P247 | ASR 分歧裁决 | 🏗 新功能 | 🟡 MEDIUM |
| P237 | 黑匣子日志 | 🔍 架构改进 | 🔴 P0 |
| P242 | MLX 后端崩溃 | 🐛 bug（换库后可能自然解决） | 🟡 HIGH |

**V3.08 主要工作**：搬家 + 复杂度拆分 + 启动健康检查（心机音频）+ P254 + P247 + P237

---

## ~~2026-06-10~~（历史记录保留）

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

---

### 2026-06-10（晚）V4 RAG 技术方案决策

- **已决议**：V4 RAG 底层使用 gbrain（garrytan/gbrain），不自己造 embedding + 向量搜索
- **Embedding 方案**：Ollama 本地模型（nomic-embed-text），完全免费离线
- **架构分层**：gbrain 负责存储 + embedding + 语义搜索（水管层），我们负责四层记录 + 双层文件夹 + 投票晋升（业务层）
- **数据映射**：四层记录 → gbrain page frontmatter，双层文件夹 → tag scope:local/public + confidence 字段
- **方案已写入** video-subtitle.md V4 RAG 章节「RAG 底层实现方案：gbrain」

---

## 2026-06-15 — FJIN-141 第四次真实重跑对比（P273 复核）

### 对比对象
- Run 1：`4754e3947e`，large-v3，旧结果，日志在 `/Users/andy/.video-subber/logs/4754e3947e/`
- Run 2：`b544184558`，tiny，旧结果，日志在 `/Users/andy/.video-subber/logs/b544184558/`
- Run 3：`65b1f1b3db`，P268/P269/P270 后结果，日志在 `/Users/andy/.video-subber/logs/65b1f1b3db/`
- Run 4：`723b866ccb`，P273 后结果，日志在 `/Users/andy/.video-subber/logs/723b866ccb/`

### 结论
- P273 修改不是完全无效：Run 4 的 `transcript.json` 中 1127/1127 段都有 `words`，说明词级时间戳已保留；49:20-50:20 局部比 Run 3 少了几条 0.07s 级连续闪屏；Run 4 未再出现“版本A/版本B/选B”审查解释污染。
- 但真实长视频验证不通过：Run 4 全片仍有 179 条 `<0.2s` 字幕、435 个 `>3s` 空洞、最大空洞 67.733s；日志仍有 143 条 `L3 不可能时长` 和 157 条 `L3 语速异常`。
- 当前问题继续归入 P273：Qwen 主引擎组装仍会把单词切成 0.08s 字幕，并留下几十秒未覆盖区间。下一步不是回退 P268，而是在 `_assemble_qwen_primary()` 加最小时长/短词并入/空洞诊断。

---

## 2026-06-15 — FJIN-141 21:39 时间轴错位专项诊断（P274）

### 用户观察点
- 用户指出：21:39 人物在说话但没有字幕，后面才出现字幕；22:00 后尤其明显，像是字幕文字落在说话之后。

### 本次复查
- 对当前 run `723b866ccb` 的 `transcript.json` 和 `FJIN-141.srt` 抽取 21:20-22:40。
- 结论：SRT 和 transcript 的时间完全一致，所以这不是翻译阶段或 SRT 生成阶段推迟，而是 ASR 源稿时间已经错位。
- 21:39 附近，源稿只有 `1297.268-1297.348` 的 0.08s 字幕；它前面有 18.416s 空洞，后面到 `1301.588` 又空 4.24s，`1324.760` 前还有 9.412s 空洞。
- Run 3 `65b1f1b3db` 和 Run 4 `723b866ccb` 在同一段的 SRT 时间几乎一样，说明 P273 的词级时间戳下传/日语断句修改没有解决这段时间骨架。

### 根因判断
- `v2/backend/core/qwen_asr_runner.py` 对长音频启用 5 分钟切片；FJIN-141 总长 7431s，所以被切成 25 个 chunk。
- 问题时间点 21:39 位于 `chunk 5/25 (1200.0s-1500.0s)`。
- `_extract_audio_chunk()` 实际提取 `t_start-5s` 到 `t_end+5s`，但 `_transcribe_chunk()` 把 Qwen 返回的相对时间直接加 `t_start`。
- 正确做法应加真实提取起点 `extract_start`，并裁掉 padding 区间。当前代码会让第二个及之后的 chunk 出现约 5 秒系统性后移。
- 复查 Git 历史后确认：P168 旧问题确实修过。`v3.07` 的旧 `_process_single_chunk()` 使用 `extract_start` 回填；6 月 12 日 `874ab188` 换 MLX ASR 库后，新 `_transcribe_chunk()` 的 MLX `result.segments` 路径又改成 `start + t_start`。这是换库/新路径漏继承旧修法，不是用户记错。
- 组装阶段也有问题，但性质不同：`_words_to_segment()` 让段时间等于第一个词到最后一个词；当前 `transcript.json` 中 1127/1127 段的 segment 时间都和内部 words 对齐。说明组装没有额外把字幕整体推迟；它的问题是把短词切成 0.08s 字幕、留下大空洞，使上游时间偏移更难看。

### WhisperJAV 代码层面对照
- WhisperJAV 的 Qwen 管线不是固定 5 分钟硬切后直接相信 ASR 时间。README 说明它有场景切分、VAD 切片、Qwen3-ASR 加 ForcedAligner 的管线。
- 它的 Qwen pipeline 默认 safe chunking，场景范围约 12-48 秒；Qwen 阶段走 `DecoupledSubtitlePipeline`，即“先生成文本，再对齐时间”。
- 这个方向对我们有参考价值：不要只靠 Qwen ASR 自带时间戳撑完整长视频，应把“文本识别”和“时间对齐”拆开，至少先修当前 padding offset bug，再考虑引入短场景/VAD/强制对齐策略。

### 记录
- 已新增 P274：Qwen 长音频切片 padding 导致字幕时间整体后移。
- P273 仍保留：它处理短字幕/空洞/组装问题；P274 是更上游的时间回填错误。下一轮应优先修 P274，再继续修 P273。

## 2026-06-15 — ASR 时间轴专项 bug 流水线启动（P274/P273/VAD/WhisperJAV）

### 本次执行
- 按用户确认的四步顺序启动整体 bug 流水线：① 先修 P274；② 再修 P273；③ 再评估 VAD 辅助；④ 最后评估 WhisperJAV 式短段文本生成 + FA 对齐 + StableTS 重组。
- 新建管道文件：`pipelines/bug-pipeline-2026-06-15-p273-p274-asr-timeline.md`。
- 已完成流水线 ① investigate：确认 P274 是确定的上游时间轴 bug，P273 是下游组装质量 bug，VAD 和 WhisperJAV 属于后续评估项。
- 已完成流水线 ② 双签审查：`plan-eng-review` 结论要求 P274/P273 拆开修；`steve-jobs-perspective` 结论要求先解决用户肉眼看到的字幕延迟，再谈架构升级。
- 已完成流水线 ③ 双签影响面：`ln-021-codegraph` 手工 fallback 追踪确认 `_extract_audio_chunk()` 被 SenseVoice 复用，因此 P274 最稳实现是新增边界 helper，不直接改变 `_extract_audio_chunk()` 返回语义；`bill-gates` 结论要求四步每一步都有量化出口。

### 当前结论
- P274 实现范围锁定为小 diff：新增或复用边界计算 helper；MLX segments 用 `extract_start` 回填；裁掉 padding 区间；测试覆盖 padding 前中后和 MLX/PyTorch 双路径。
- P273 必须等 P274 修完并真实重跑后再做，避免分不清是时间回填问题还是组装问题。
- VAD 不能作为硬过滤重新接入，只能先评估为切片、断句、对齐恢复的辅助信号。
- WhisperJAV 方案只作为后续架构实验，不混入当前 P274/P273 bug 小修。

### 下一步
- P274 最小实现已完成：Qwen MLX chunk 时间回填改用 `extract_start`，并裁掉 padding 区间；MLX chunk `text` 也改为由裁剪后的 segments 重建，避免 padding 文本继续污染 L2。
- 已通过相关窄测试：P274 单测、P268/P269、P273、Qwen 参数/健康检查，共 23 条通过。
- 全量验证仍不能给绿灯：`test_arch7_pipeline.py::TestIndependentQwenASR` 在当前环境导入真实 MLX 时触发 `No Metal device available`；完整 `test_phase1c_chunking.py` 跑到 SenseVoice/funasr 旧测试时 Python 段错误。
- 下一步：真实重跑 FJIN-141，重点复查 21:39、22:00、49:37、50:00；确认 P274 解决整体 5 秒滞后后，再进入 P273 组装质量修复。

---

## 2026-06-15 — FJIN-141 第五次真实重跑对比（P274 复核）

### 对比对象
- Run 3：`65b1f1b3db`，P268/P269/P270 后结果。
- Run 4：`723b866ccb`，P273 后结果，也是用户指出 21:39/22:00 字幕延迟的对照对象。
- Run 5：`e5494b352d`，P274 修复后最新真实重跑结果。

### 核心指标
| 指标 | Run 3 | Run 4 | Run 5 |
| --- | ---: | ---: | ---: |
| SRT 字幕条数 | 1157 | 1141 | 1099 |
| `<0.2s` 瞬闪字幕 | 173 | 179 | 188 |
| `<0.5s` 短字幕 | 383 | 375 | 380 |
| `>3s` SRT 间隔 | 433 | 435 | 417 |
| `>10s` SRT 间隔 | 161 | 161 | 151 |
| 最大 SRT 间隔 | 67.733s | 67.733s | 126.920s |
| 问号/不确定标记 | 997 | 945 | 846 |
| 审查解释污染进入 SRT | 242 行 | 0 行 | 0 行 |

### 结论
- P274 有效：21:39 附近字幕从旧 run 的 `1297.268` 前移到最新 `1292.268`，22:00 附近同一句从 `1306.867-1310.468` 前移到 `1301.867-1305.468`，正好修掉 5 秒系统性后移。
- P273 部分改善但未关闭：最新仍有 188 条 `<0.2s` 字幕、846 条 `[?]`。417 个 `>3s` 只能先叫 SRT 间隔，不能直接叫漏字幕；用户已确认最大间隔所在的 36:27-38:45 没有人说话。
- P272 未根治：最新成品 SRT 没有审查解释污染，但 `debug.log` 仍能看到模型输出“字幕质量审查员/版本A/版本B/选A”。这次只是没写入成品，输出约束仍要修。
- 日志侧有改善：`L3 不可能时长` 从 Run 4 的 143 降到 Run 5 的 13，`L3 语速异常` 从 157 降到 83，`L4 CJK重复保留` 从 142 降到 29。说明 P274 裁 padding 后过滤层压力下降，但最终观看体验还没过关。

### 下一步
- P274 标记为已修：它只负责 5 秒系统性后移。
- P273 口径修正：不能再把所有 SRT gap 直接叫“漏字幕空洞”。用户已人工确认 `2187.702-2325.101`（36:27.702-38:45.101）和 40:10-41:35 是无对白。P273 当前剩余主问题是 `[?]` 太多和少量短字幕，真实漏字幕要等 P275 的人声地图后再判定。
- VAD 暂不作为硬过滤重新接入，只能评估为辅助切片、断句或对齐恢复信号。

### 问号来源复核
- 用户人工浏览后反馈：5 秒延迟已解决；大部分内容已经变成句子，之前那种连续闪出多个字幕、正常句子被切成小词的情况明显减少。
- 用户人工复核 `2187.702-2325.101`（36:27.702-38:45.101）后确认：这段视频确实没有人说话，因此它不是“有人说话但没有字幕”的空洞 bug。后续报告中应区分“字幕覆盖空洞”和“有对白漏字幕”，不能把长时间无对白直接算成字幕错误。
- 本轮继续追查 `[?]` 来源：`[?]` 不是翻译阶段加的，也不是 SRT 生成时随便加的；SRT 只是把 `seg.uncertain=True` 显示成 `[?]`。
- 最新 run `e5494b352d` 的 L2 窗口比对结果为 248 个窗口：GREEN 38、YELLOW 68、LOW_DATA 139、EMPTY 3。L2 会把 YELLOW 和 LOW_DATA 窗口里的字幕标为 `uncertain=True`，这是当前问号的大头。
- L3/L4 只贡献少量新增问号：`L3 语速异常` 83 条，`L4 CJK重复保留为疑似` 29 条；`L3 不可能时长` 13 条是过滤/删除，不是保留问号。
- Refiner 本轮是 single 模式，`corrections=0 accepted=0`，所以没有清掉这些 `uncertain` 标记。最终 SRT 才显示为 `[?]`。
- 用户抽查 `01:10.896-01:13.376` 的中文字幕“小香奈最喜欢纳豆了，所以能吃哦”。对应转录源稿是 `カナ猫大好きだから食べれるよ`，并非明确的 `納豆`。本条更像 ASR 源稿不顺 + 翻译上下文/术语表把“猫”带成“纳豆”，暂不能归因于 API 成人内容限制。
- 用户追问为什么无对白区间没有被 L1 确认成非人声：当前 L1 只处理“已经存在的字幕段”，不会为空白时间段生成“非人声确认段”。因此 SRT gap 统计只能表示“字幕之间有间隔”，不能表示“L1 判定这里漏字幕”。

---

## 2026-06-15 — P275 L1 全片人声/非人声时间轴登记

### 用户纠正
- 用户确认：40:10-41:35 没有对白，因此它不是字幕空洞。
- 用户确认：85:35 附近有字幕，问题是翻译不准，不是空洞。
- 结论：此前用 SRT gap 直接称“空洞”的口径错误。长时间无字幕只有在“人声区内没有字幕”时才是 bug。

### 当前代码事实
- 当前 L1 是字幕段级检查：先有 Qwen/字幕段，再对每个段做 SenseVoice + Qwen FA 判断。
- 当前 L1 不是全音频时间轴：不会输出 `40:10-41:35 = 非人声` 这种确认记录。
- 因此当前报告无法自动区分“正常无对白间隔”和“有人声但没字幕”。

### 新增问题
- 已登记 P275：L1 缺少全片人声/非人声时间轴。
- 修复方向：SenseVoice + Qwen FA 生成 `voice_regions/non_voice_regions`；质量报告只把“人声区内无字幕”记为真实漏字幕。

### 后续分支规划
- 当前 ASR 分支不再继续动代码。下一步只做选择性提交：提交 P268/P269/P270/P273/P274 已验证改动和记录文件，不提交 `.db-shm/.db-wal`、`v2/v2/` 嵌套旧目录和不确定报告文件。
- 提交后合并到 `main`。合并说明必须写清四点：P274 的 5 秒系统性延迟已修；P273 的句子化明显改善；`[?]` 仍多；P275 还没修。
- 新分支一：`l1-voice-timeline`，修 P275。目标是生成全片人声/非人声时间轴，避免把无对白 SRT gap 误报为空洞。
- 新分支二：`qwen-standard-mode`，落实既定两档模式：标准 = Qwen ASR + SenseVoice；精确 = Qwen + Whisper + SenseVoice。旧 fast/快速档废弃。
- 新分支三：`mlx-asr-library-swap`，继续既定换库/主模型更换规划，处理 Whisper/MLX 相关模型路径。

## 2026-06-15 — ASR 分支下一步规划记录（本次不动代码）

### 用户确认
- 用户要求：这次只做规划和记录，不再改任何代码；代码修改留到下一次。
- 用户判断：本轮真实视频验证已经证明 5 秒字幕延迟已解决，大部分内容已经恢复成句子，当前唯一明显未解决的是 `[?]` 太多。

### 决策
- 当前 ASR 分支先 commit，再合并主线。
- P275 不在当前分支修。合并主线后新开 `l1-voice-timeline` 分支，按 bug 流水线补 L1 全片人声/非人声时间轴。
- 标准/精确模式改造单独开 `qwen-standard-mode` 分支，不和 P275 混在一起。
- Whisper/MLX 换库单独开 `mlx-asr-library-swap` 分支，不和模式改造混在一起。

### 提交注意事项
- 不使用 `git add .`。
- 应提交：已验证代码改动、`task_plan.md`、`progress.md`、`problems.md`、`video-subtitle.md`、相关 `pipelines/bug-pipeline-*.md`。
- 暂不提交：`.hex-skills/codegraph/index.db-shm`、`.hex-skills/codegraph/index.db-wal`、`v2/.hex-skills/`、`v2/v2/`。
- 不确定报告文件先让用户确认，再决定是否提交。

## 2026-06-16 — 标准模式真实重跑对比分析（P276 登记）

### 对比对象
- 最新标准模式 run：`79066a0965`，`transcriptionTier=standard`，日志在 `/Users/andy/.video-subber/logs/79066a0965/`。
- 上一轮精确模式 run：`e5494b352d`，`transcriptionTier=precise`，日志在 `/Users/andy/.video-subber/logs/e5494b352d/`。

### 量化结论
| 指标 | 标准模式 `79066a0965` | 精确模式 `e5494b352d` |
| --- | ---: | ---: |
| transcript 段数 | 651 | 1099 |
| SRT 条数 | 664 | 1099 |
| `[?]` / uncertain | 87/651 = 13.4% | 846/1099 = 77.0% |
| `<0.2s` SRT 短字幕 | 109 | 188 |
| `<0.5s` SRT 短字幕 | 225 | 380 |
| Qwen chunk segments 合计 | 1947 | 2776 |
| 底稿文本字符数 | 3526 | 4903 |
| 拉丁字母占比 | 17.2% | 0% |

### 判断
- 改善是真的：`[?]` 大幅下降，短字幕数量也下降，观看上更像句子。
- 但不能判定准确率全面提高：标准模式少了 448 个 transcript 段，文本量少约 28%，还混入 `nine/love/want/Mom/Goodbye` 等英文和 `习 惯 相` 这类中文样文本。
- 单字“好/吧”不是纯翻译问题。底稿中存在 `だ -> 吧`、`いだろう -> 吧`、`いい -> 好`、`good -> 好` 这类孤立段，翻译器只是把上游短词照着翻了。
- 根因指向标准模式语言未锁定：`transcriber_v2.py` 在 Qwen 跑全片前仍是 `detected_lang=auto`，`qwen_asr_runner.py` 会把 `auto` 解析成 `None`，让 Qwen 自己猜语言；SenseVoice 语言推断发生在 Qwen 之后，太晚。

### 已记录
- 新增 P276：标准模式未先锁定语言，Qwen 日语长片底稿混入中文/英文/数字。
- 下一步建议按 bug 流水线处理 P276，不直接把标准模式结果当封版证据。

## 2026-06-16 — 标准模式/L1 问题拆分登记

### 用户纠正
- 用户指出：这次发现的不只是 P276 语言未锁定，还包括 SenseVoice、L1、语言判断、启动环境、标准模式路由等多个问题。
- 结论：P276 只能作为“标准模式语言路由”的问题，不能吞掉全部根因。

### 已拆分问题
- P276：标准模式模型路由错误。标准版应该先用 Whisper 第一段当语言检测模型，再决定继续 Whisper 还是切 Qwen；不能把 `lang=auto` 直接交给 Qwen 跑完整片。
- P277：启动环境不一致，worker 继承启动 uvicorn 的 Python；如果不是 `v2/.venv`，FunASR/SenseVoice 可能不可用。
- P278：SenseVoice 日志缺失/误导，先打印“并行模式：SenseVoice...”再检查依赖，且缺少完成、人声地图、L1 输入数量等日志。
- P279：标准模式是单主引擎，没有第二份稿子时仍可能进入 L2，质量报告被空参考稿干扰。
- P280：已并入 P277/P278。“L1 人声地图真实 run 未落盘”不是独立根因，而是启动环境和 SenseVoice 日志的验收项。
- P281：单字字幕/切句问题。单字“好/吧/十”不是单纯语言路由问题，而是缺少“说话区短切片 → ASR → 对齐 → 字幕重组”的流程。后续参考 WhisperJAV 思路：VAD 做粗人声候选，SenseVoice 做标签确认，两者都不直接删字幕。

### 方案同步
- 标准模式语言方案已按用户意见修正：先跑 Whisper 第一段拿语言；如果是 Qwen 擅长语言且置信度足够，再停止 Whisper、切 Qwen 跑全片；否则继续 Whisper。
- `task_plan.md` 已同步拆出 P276-P279，并把 P280 归并到 P277/P278 验收标准；新增 P281 记录单字字幕/切句方案。

## 2026-06-16 — P276/P277/P278/P279/P281 bug 流水线修复

### 已修代码
- P276：标准模式改为先用 Whisper 第一段判断语言，再决定继续 Whisper 或切 Qwen。若 Qwen 不可用或内存不足，不释放 Whisper，继续 Whisper 主引擎。
- P277：服务端启动日志新增 `Server Python`；worker 文件日志新增 `Worker Python`，用于确认真实运行环境是否都是 `v2/.venv`。
- P278：SenseVoice 日志改为先检查可用性，再打印启动/跳过；不可用时明确写 `funasr` 和人声地图跳过；线程无返回事件时 warning。
- P279：标准模式调用过滤器时传 `skip_l2=True`，避免单主引擎继续跑 L2。
- P281：新增短词/单字合并止血函数，日语短片段会并入相邻字幕，避免“だ/吧”这类独立短字幕。

### 验证结果
- `test/test_p276_standard_language_env.py`：7 passed。
- 相关 ASR/L1/L2/时间戳回归：73 passed。
- 目标文件 `py_compile` 通过。
- 全量 `pytest test -q` 被当前 Codex/headless 环境阻塞：导入 `mlx_qwen3_asr` 时 `No Metal device available`。这不是本轮逻辑失败，但不能报全量绿灯。

### 待真实重跑确认
- 日志里必须看到 `Server Python` 和 `Worker Python` 都指向 `v2/.venv`。
- 标准模式必须写出 Whisper 判到的语言、置信度、最终主引擎。
- SenseVoice 必须写出启动/跳过/完成和 P275 人声地图摘要。
- 标准模式必须写出 `L2 跳过`。
- 真实字幕里继续统计单字字幕数量、`<0.5s` 短字幕数量、段数和文本量。

### 后续
- P282 环境和历史文件归档不在本轮代码修复里做。等真实重跑确认功能链路后，再单独列清单处理两个 `.venv`、历史报告、临时数据库和旧目录。

## 2026-06-17 — 真实重跑前预飞检查与缓存清理

### 代码检查
- 运行 `./.venv/bin/python -m pytest test/test_p276_standard_language_env.py test/test_p273_qwen_ja_assembly.py test/test_p268_p269_pipeline.py test/test_p251_qwen_window_assign.py test/test_phase1d_qwen_health.py test/test_phase1d_integration.py test/test_p228_timestamp_fix.py -q`。
- 结果：73 passed。
- 运行 `./.venv/bin/python -m py_compile backend/transcriber_v2.py backend/worker.py backend/main.py backend/core/hallucination_filter.py backend/core/sensevoice_runner.py backend/worker_manager.py`。
- 结果：通过。

### 环境检查
- `v2/.venv/bin/python` 能 import `funasr`，`backend.core.sensevoice_runner.is_sensevoice_available()` 返回 True，`faster_whisper` 可 import。
- 在 Codex/headless 环境中导入 `mlx_qwen3_asr` 仍会报 `No Metal device available`，这是当前沙箱没有 Metal/GPU 的限制，真实前台 Mac 环境需通过真实 run 验证。
- 发现旧服务使用错误命令：`cd v2 && ../.venv/bin/python -m uvicorn ...`，这是项目根 `.venv`，不是 `v2/.venv`。
- 已停止旧错误服务和孤儿 uvicorn 进程，重新用正确命令启动：`cd /Users/andy/Andy-agent/video-subtitle/v2 && ./.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8766`。
- 健康检查：`curl http://127.0.0.1:8766/api/health` 返回 `{"ok":true,"version":"3.07"}`。
- 最终进程列表只剩正确服务命令；旧日志 tail 监控进程已清理。

### 缓存清理
- 为避免后端去重直接复用旧 done job，不只移动 `cache/`，也移动了旧 job 的 `meta.json`。
- 已归档：
  - `/Users/andy/.video-subber/jobs/79066a0965/cache` → `/Users/andy/.video-subber/cache-archive/2026-06-17-pretest/79066a0965/cache`
  - `/Users/andy/.video-subber/jobs/79066a0965/meta.json` → `/Users/andy/.video-subber/cache-archive/2026-06-17-pretest/79066a0965/meta.json`
  - `/Users/andy/.video-subber/jobs/e5494b352d/cache` → `/Users/andy/.video-subber/cache-archive/2026-06-17-pretest/e5494b352d/cache`
  - `/Users/andy/.video-subber/jobs/e5494b352d/meta.json` → `/Users/andy/.video-subber/cache-archive/2026-06-17-pretest/e5494b352d/meta.json`
- 保留原地：
  - `/Users/andy/.video-subber/jobs/79066a0965/FJIN-141.srt`
  - `/Users/andy/.video-subber/jobs/e5494b352d/FJIN-141.srt`
  - `/Users/andy/.video-subber/logs/79066a0965/user.log`
  - `/Users/andy/.video-subber/logs/79066a0965/debug.log`
  - `/Users/andy/.video-subber/logs/e5494b352d/user.log`
  - `/Users/andy/.video-subber/logs/e5494b352d/debug.log`

### 测试时必须确认
- 新 job 日志中应出现 `Worker Python`，并指向 `v2/.venv`。
- 标准模式应输出 Whisper 判语言、置信度和最终主引擎。
- SenseVoice 应输出启动/跳过/完成和 P275 人声地图摘要。
- 标准模式应输出 `L2 跳过`。

## 2026-06-18 — 启动 P272/P237/P275/P281 bug 修复流水线

### 用户决策
- 按当前顺序进入 bug 修复流水线：先修 P272，再补 P237，再做 P281 Phase 2 和 P275 Phase 2。
- 运行环境问题拆成两层：V3.07 只固定真实测试启动环境，确保 server/worker 都跑 `v2/.venv`；P271+P282 目录清理、旧 `.venv` 归档、历史文件整理挪回 V3.08 搬家时统一做。

### 已完成
- 新建管道文件：`pipelines/bug-pipeline-2026-06-18-p272-p237-p275-p281.md`。
- 完成流水线第 ① 步 investigate：确认 P272、P237、P275 Phase 2、P281 Phase 2、P277 验收补强的根因和修复边界。
- 更新 `task_plan.md`：P276-P279 改为 6/17 真实重跑已验证；P281 拆为 Phase 1 止血完成和 Phase 2 待修；P275 Phase 2/P281 Phase 2 纳入当前待修。
- 更新 `problems.md`：顶部待修清单改为 4 个 bug + 1 个验收补强；P271+P282 明确回到 V3.08。

### 下一步
- 流水线第 ② 步：双签审查（plan-eng-review + steve-jobs-perspective），只读第 ① 步结论，不动代码。

## 2026-06-18 — 完成 P272/P237/P275/P281 bug 修复流水线

### 已完成流水线
- 已完成 `pipelines/bug-pipeline-2026-06-18-p272-p237-p275-p281.md` 的 ①-⑩ 步。
- 前六步完成根因诊断、方案双签、影响面、diff 简化、设计一致性走查和安全审查。
- 第 ⑦ 步按 TDD 修复代码，第 ⑧ 步跑验证，第 ⑨ 步做 Web QA，第 ⑩ 步做轻量链路验证。

### 已修代码
- P272：`_review_seam()` 写回前会拒绝“版本A/版本B/建议/原因”等 LLM 审查解释文字。
- P237：worker 异常路径会写入安全 `crash_snapshot`，不记录 API Key、环境变量、prompt、字幕全文。
- P281 Phase 2：短词合并接入 P275 人声地图；同一人声区内放宽合并，非人声区孤立短词标疑似。
- P275 Phase 2：新增 `P275 人声地图质量` 日志，输出人声区、无字幕人声区、非人声短词、最长疑似漏字幕区间。
- P277：新增 `v2/run-test.sh`，固定 `v2/.venv` 且无 `--reload`；环境大整理仍留 V3.08。

### 验证结果
- RED 测试先失败：7 failed, 11 passed，失败点对应 P272/P237/P281/P275/P277 缺口。
- GREEN 后新增/扩展测试：18 passed。
- 相关回归测试：99 passed。
- 重点回归测试：91 passed。
- 编译检查通过：`backend/transcriber_v2.py`、`backend/worker.py`、`backend/main.py`、`backend/core/hallucination_filter.py`、`backend/translation/orchestrator.py`。
- 全量 `pytest test -q` 受当前 headless/模型环境阻塞：Metal GPU 不可用、`yt_dlp.__spec__ is None`、HuggingFace 网络/模型路径卡住。不能报全量绿灯。

### Web QA
- 8766 当前存在旧 Python 监听但不可访问，本轮未擅自杀进程。
- 临时使用同样无 reload 命令在 8767 启动服务，健康接口返回 `{"ok":true,"version":"3.07"}`，首页返回 200。
- 临时 8767 服务已关闭。

### 待用户真实重跑验收
- 下一轮真实测试前先处理 8766 旧监听，再用 `v2/run-test.sh` 启动服务。
- 新 job 日志必须确认 `Server Python` 和 `Worker Python` 都指向 `v2/.venv/bin/python`。
- 对比 6/17 job `28a951e756`：单字段、`<0.3s & <=2字` 超短段、`uncertain`、`P275 人声地图质量` 日志。

## 2026-06-18 — 真实重跑前环境清场

### 已完成
- 清理旧监听：8766 上的旧 Python 服务 PID 49409 已停止；停止前 cwd 是 `v2`，但健康接口不可访问，属于旧服务/卡住服务。
- 清理缓存：`/Users/andy/.video-subber/jobs` 下旧 `cache/` 和旧 `meta.json` 已移动到 `/Users/andy/.video-subber/cache-archive/2026-06-18-pretest-v307-fix/`。
- 保留对比材料：原地保留旧 `.srt`、`.mp4`、`input.mp4` 和 `/Users/andy/.video-subber/logs` 日志文件。
- 启动新服务：使用 `screen` 会话 `video-subtitle-v307-test` 托管服务，监听 `8766`。
- 健康检查：`http://127.0.0.1:8766/api/health` 返回 `{"ok":true,"version":"3.07"}`。

### 当前验证结论
- 当前服务 cwd 是 `/Users/andy/Andy-agent/video-subtitle/v2`。
- 当前服务加载 `v2/.venv` 下的依赖包。
- 启动日志写入 `/Users/andy/.video-subber/server-run-test-2026-06-18.log`。
- 本轮没有清理 `.hex-skills` / CodeGraph 索引文件；这些是代码记忆/索引用途，提交前只需排除即可。

### 下一步真实测试要看
- 新 job 日志第一屏必须出现 `Worker Python`，并指向 `v2/.venv/bin/python`。
- 标准模式必须出现 Whisper 判语言、主引擎选择、SenseVoice 日志、`L2 跳过`。
- 对比 6/17 job `28a951e756`：单字段、超短段、`uncertain`、`P275 人声地图质量`。

## 2026-06-18 — FJIN-141 新旧三轮 SRT 对比

### 对比对象
- 6/15 导出：`/Users/andy/Downloads/Video-Subber/FJIN-141_上下文_20260616_142930.srt`
- 6/16 导出：`/Users/andy/Downloads/Video-Subber/FJIN-141_上下文_20260617_114948.srt`
- 6/17 导出：`/Users/andy/Downloads/Video-Subber/FJIN-141_上下文_20260618_104539.srt`，对应 job `28a951e756`
- 6/18 本次：`/Users/andy/Downloads/Video-Subber/FJIN-141.srt`，对应 job `5c67f7a69b`

### 结论
- 本次文件大小从 46KB 降到 44KB，不等于质量整体变差。
- Qwen 原始输出没有变：6/17 和 6/18 都是 25 个 chunk，合计 2776 个 raw segments。
- SenseVoice 输出也没有变：两次都是 1088 个 segments。
- 变化发生在过滤、短词合并和标疑似阶段：最终段数 971 → 922，少 49 段。
- 正向变化：超短碎片减少，`<0.3s 且 <=2字` 从 114 降到 85。
- 负向变化：`[?]` 从 91 增到 129，需要继续追 P275/P281/L3 标疑似策略。
- P275 新质量日志生效：`人声区=15, 无字幕人声区=0, 非人声短词=80, 最长疑似漏字幕=None`。

### 下一步观察
- 文件大小只作为参考，不作为质量结论。
- 继续重点看 `[?]` 反弹来源、非人声短词标疑似是否过严、L3 对 0.08s 短词是否仍然过敏。

## 2026-06-18 — P283 问号噪音修复流水线

### 已完成
- 按 bug 修复流水线执行 P283：管道文件为 `pipelines/bug-pipeline-2026-06-18-p283-question-mark-noise.md`。
- 根因确认：6/18 的 Qwen 原始输出和 SenseVoice 输出没有变差，`[?]` 反弹主要来自后处理把内部质量信号直接显示成用户问号。
- 已修改 `v2/backend/transcriber_v2.py`：P281 非人声区孤立短词只保留 `filter_reason=voice_timeline_non_voice_short_fragment`，不再直接设置 `uncertain=True`。
- 已修改 `v2/backend/core/hallucination_filter.py`：L3 对 `zh/ja/ko` 的超短碎片只记录内部原因，不再直接显示 `[?]`。

### 验证
- RED 测试先失败：`2 failed, 1 passed`，失败点对应 P281 和 L3 的旧问号行为。
- GREEN 测试通过：`3 passed in 1.37s`。
- 相关回归通过：`83 passed in 1.41s`。
- 编译检查通过：`py_compile backend/transcriber_v2.py backend/core/hallucination_filter.py`。

### 下一步真实重跑要看
- 8766 服务已重启，当前由 `screen` 会话 `video-subtitle-v307-test` 托管，新监听 PID 为 `41198`。
- 健康接口已确认：`{"ok":true,"version":"3.07"}`。
- 已清理旧结果复用风险：job `5c67f7a69b` 的 `cache/` 和 `meta.json` 已移动到 `/Users/andy/.video-subber/cache-archive/2026-06-18-post-p283/`；原 `.srt`、视频和日志保留。
- 重新跑 FJIN-141，重点对比 `[?]`、单字段、`<0.3s 且 <=2字`、`P275 人声地图质量`。
- 预期：`[?]` 应明显低于 6/18 的 129/922，但 L4 明显重复和强风险仍然可以保留 `[?]`。

## 2026-06-18 — P283 后 FJIN-141 真实重跑验收

### 对比对象
- 最新：job `23e2621360`，日志 `/Users/andy/.video-subber/logs/23e2621360/`，字幕 `/Users/andy/.video-subber/jobs/23e2621360/FJIN-141.srt`
- 上一轮：job `5c67f7a69b`，P283 前 `[?]` 反弹版本
- 更早对照：`28a951e756`、`79066a0965`、`e5494b352d`、`65b1f1b3db`

### 机械统计
- `[?]`：`23e2621360` 为 52/931，低于 `5c67f7a69b` 的 129/922，也远低于 `e5494b352d` 的 846/1099、`65b1f1b3db` 的 997/1157。
- P275 人声地图：`人声区=15, 无字幕人声区=0, 非人声短词=80, 最长疑似漏字幕=None`。
- L3 语速异常：从上一轮 45 条降到 17 条；L3 不可能时长从 8 条降到 1 条。
- 翻译：929/929 条成功；最新字幕和翻译缓存未发现“版本A/版本B/建议/原因”等接缝审查污染。
- 49:20-50:20 老问题窗口：最早 `65b1f1b3db` 存在约 5 秒系统性后移、大量 `[?]` 和瞬闪短段；最新 `23e2621360` 时间回到正确区间，问号消失，可读性明显提高。

### 结论
- P272/P275/P281/P283 这轮修复通过真实长视频验收。
- P273 剩余问题不再是“有人声但没字幕”的阻塞问题，而是成人片素材里的短促叫声、重复声、极短字幕和正常无对白间隔。
- 继续为 JAV 干扰项追 100% 完美会拖慢主线。当前接受残留，不再改生产代码。

### 下一步
- 选择性提交当前分支。提交时排除 `.db-shm/.db-wal`、`v2/v2/`、`.hex-skills` 临时索引等杂项。
- 提交后合并主线。
- 合并后进入 V3.08：`v2/` 搬家到 `v3/`，再做复杂度拆分和启动健康检查。

## 2026-06-18 — 版本规划口径调整

### 本轮决定
- 用户确认：`P266` Mac 端 Whisper 换 `mlx-whisper` 前移到 `V3.07`，不再挂在 `V3.09`。
- 执行顺序改为：先提交当前分支并合并主线，再开 `mlx-asr-library-swap` 分支完成换库和测试；换库完成后，`V3.07` 再算彻底收尾。
- `P259` 不再单列成独立待办。原因是当前代码已经按语言设置 L3 阈值，这件事后面只在真实使用中被动观察，不专门安排一轮测试。

### 已同步文件
- `task_plan.md`
- `problems.md`
- `/Users/andy/task_plan.md`

## 2026-06-18 — 项目级索引缓存规则落地

### 已完成
- 已把 `.hex-skills/`、`v2/.hex-skills/`、`*.db-shm`、`*.db-wal` 加入 `.gitignore`。
- 已把已跟踪的 `.hex-skills/codegraph/index.db` 从 git 索引中移除，但文件仍保留在本机。

### 结论
- 这类文件以后按“项目级本地缓存”处理：允许跨对话、跨分支复用，但不进入版本库。
- 后续新对话如果读取 `video-subtitle` 项目，可以继续直接利用本地索引，不必把它当某个分支的代码成果。

## 2026-06-18 — P266 Mac 端 Whisper 换 mlx-whisper

### 完成事项
- 新增 `v2/backend/core/whisper_runner.py`：Mac Apple Silicon 走 `mlx-whisper`，其他平台继续走 `faster-whisper`。
- 修改 `v2/backend/transcriber_v2.py`：Whisper 调用改为统一入口 `transcribe_whisper()`，后续仍转成项目原有 `Segment` 字段。
- 修改 `v2/backend/env_check.py`：Mac 端检测 `mlx_whisper` 包和 Hugging Face 缓存，不实际 import MLX，避免在 Codex/headless 环境触发 `No Metal device available`。
- 修改 `v2/pyproject.toml`：新增 `mlx-whisper>=0.4.3`，仅限 `Darwin arm64` 安装；Windows 端仍保留 `faster-whisper==1.2.1`。
- 新增 `v2/test/test_p266_mlx_whisper.py`：锁定 Mac/Windows 分流、MLX 输出字段转换、环境检测不触发 MLX 初始化。

### 模型下载
- 已安装 `mlx-whisper==0.4.3` 到 `v2/.venv`。
- 已下载 `mlx-community/whisper-large-v3-mlx` 到本机 Hugging Face 缓存。
- 权重文件：`/Users/andy/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx/blobs/05ff791ce3630fae47e7c51004e9666204d786246ec07cac6110af768099b40d`
- 权重大小：`3083520416 bytes`。
- 处理过下载问题：`huggingface-cli` 走台湾节点卡住，改用 `curl --http1.1 -C -` 断点续传；切香港节点后稳定到约 9-10MB/s。

### 验证
- RED：`test/test_p266_mlx_whisper.py` 先失败，缺少 `backend.core.whisper_runner`。
- GREEN：`test/test_p266_mlx_whisper.py` 通过，`4 passed in 0.01s`。
- 相关回归：`test_p266_mlx_whisper.py + test_standard_precise_mode.py + test_p276_standard_language_env.py + test_arch7_pipeline.py + test_phase1c_chunking.py + test_p251_qwen_window_assign.py`，`82 passed in 13.01s`。
- 模型缓存验证：`snapshot_download('mlx-community/whisper-large-v3-mlx', local_files_only=True)` 能定位到本地 snapshot。

### 当前边界
- 当前 Codex/headless 环境没有 Metal/GPU 访问权限，直接 import `mlx_whisper` 会报 `No Metal device available`，因此不能在本沙盒内完成真实 MLX 转录。
- 下一步需要在普通 Mac 前台环境跑一次真实短音频/真实视频，确认 `mlx-whisper` 能实际转录并输出时间戳。

## 2026-06-19 — Whisper 换库分支 London VLOG 字幕回归只读诊断

### 现象
- job `a2789ff719` 前两句复现用户反馈：
  - 第 1 条字幕把 `Hey Taylor what time is it` 和 `Oh gosh I don't` 合到一起，中文成品为「嘿泰勒，现在几点了？天哪，我」。
  - 第 2 条 `clock` 再次被识别为 `claw`，中文成品为「真希望我有爪子」。

### 查证
- `cache/f82cadc4bac1/transcript.json` 中第 0 段英文源文本已经是 `Hey Taylor what time is it Oh gosh I don't`。
- 第 1 段英文源文本已经是 `know I wish I had claw`。
- 两段 `whisper_text` 都是空字符串。
- 日志显示 Whisper 实际转录出 `643 段`，但 P247/P269 Refiner 进入 `single` 模式，`corrections=0 accepted=0 mode=single`。

### 结论
- 这不是翻译器把中文翻错，翻译器只是照着坏英文翻。
- `clock/claw` 旧修复依赖 P247/P263+P264：让 LLM 同时看 Qwen 主稿和 Whisper 参考稿。现在 Qwen 主引擎组装结果没挂 `whisper_text`，LLM 没看到 `clock` 参考答案，所以旧问题复发。
- 第一条边界问题来自 `_assemble_qwen_primary()` 只按 Qwen 词时间戳和停顿切句。`it` 到 `Oh` 的间隔约 `0.24s`，低于英文默认 `0.5s` 断句阈值，因此被合成同一条。
- 已新增问题库 P284，后续按 bug 修复流水线处理。

### 2026-06-19 追加机械统计
- Qwen 日志原始小片段：`569+587+570+526+322=2574`。
- Whisper 日志段数：`643`。
- 最终字幕段数：`416`。
- 最终 `transcript.json` 中 `with_whisper_text=0/416`，说明 Whisper 参考稿完全没有挂到最终字幕段上。
- 最终 416 条中：`<0.5s` 短字幕 `31` 条，`<1s` 字幕 `94` 条，单词字幕 `35` 条，2 个词以内字幕 `60` 条。
- 典型异常样例：`Let's pack I don't think this will me too`、单独 `long`、单独 `Cannot`、`Travel Bookstore You grand was`、单独 `You` 持续 15.04 秒、单独 `Lauren` 持续 5.92 秒。

## 2026-06-19 — P284 英文 Whisper 参考稿链路修复

### 完成事项
- 按 bug 修复流水线新增管道文件：`pipelines/bug-pipeline-2026-06-19-p284-whisper-qwen-english.md`。
- 新增 P284 回归测试：
  - 英文 Qwen 主路径不能把 `Hey Taylor what time is it` 和 `Oh gosh I don't` 合到同一条。
  - 英文 Qwen 主路径必须把 Whisper 文本写入 `whisper_text`，让 Refiner 恢复 dual。
  - 日语 Qwen 主路径保持 P273 原有组装行为，不受英语修复影响。
  - 短字幕合并时同步合并 `whisper_text`，避免参考稿在合并后丢失。
- 修改 `v2/backend/transcriber_v2.py`：
  - `_smart_assemble()` 中仅对 `lang == "en" and whisper_ok` 的 Qwen 主路径启用 Whisper 边界组装。
  - 新增 `_assemble_qwen_on_whisper_boundaries()`，字幕正文来自 Qwen 词，参考稿来自 Whisper 段落。
  - 新增 `_merge_whisper_text()`，短字幕合并时保留参考稿。

### 验证
- RED：旧代码下英文测试失败，第一条输出 `Hey Taylor what time is it Oh gosh I don't`。
- GREEN：`./.venv/bin/python -m pytest test/test_p260_boundary_fix.py test/test_p268_p269_pipeline.py test/test_p273_qwen_ja_assembly.py -q` → `25 passed`。
- 相关回归：`./.venv/bin/python -m pytest test/test_p260_boundary_fix.py test/test_p268_p269_pipeline.py test/test_p273_qwen_ja_assembly.py test/test_p276_standard_language_env.py test/test_phase1d_qwen_health.py test/test_phase1d_integration.py test/test_p251_qwen_window_assign.py test/test_p228_timestamp_fix.py -q` → `80 passed`。
- Git 跟踪测试：`./.venv/bin/python -m pytest test/test_harden_v307_sprint2.py test/test_phase1d_qwen_health.py test/test_p160_layer2.py test/test_p160_stemmer.py -q` → `59 passed, 2 warnings`。
- 全量 `pytest test -q` 未能完成：当前 Codex/headless 环境无 Metal/GPU，`mlx` 在收集 `test_p182_tail_flush.py` 时触发 `No Metal device available`。这属于运行环境限制，不是 P284 修改导致。

### 当前边界
- 代码层已修复。
- 仍需在普通 Mac 前台环境重跑 London VLOG，确认真实成品第 1 条断句恢复，第 2 条 `clock/claw` 恢复。

## 2026-06-19 — London VLOG 重跑前缓存清理

### 完成事项
- 按用户要求直接删除 `/Users/andy/.video-subber/jobs/a2789ff719/cache`。
- 保留 job 根目录下的字幕和视频文件。
- 保留 `/Users/andy/.video-subber/logs/a2789ff719/user.log` 和 `debug.log`。

### 验证
- 已确认 `cache/` 不存在。
- 已确认 `London VLOG： Exploring the City, Markets, & Hidden Gems_video.srt` 仍存在。
- 已确认 `user.log` 和 `debug.log` 仍存在。

## 2026-06-19 — 旧完成卡片清理规则固化 + stale done 自愈

### 现象
- 用户点击“重新处理”无反应。
- 页面仍恢复出旧的已完成卡片。

### 根因
- 前端 `startBtn` 点击事件在 `State.videoFile / localPath / sourceUrl` 全为空时直接 `return`，所以“重新处理”外观存在，但没有真实输入源时不会提交。
- 只删 `cache/` 不够；旧 job 的 `meta.json`、成品文件和后端内存里的 `JOBS[job_id]` 仍可能让前端继续恢复“已完成”卡片。

### 本轮处理
- 按新规则清理 job `a2789ff719`：
  - 保留 `video.srt`
  - 保留 `user.log` / `debug.log`
  - 删除 job 目录其余文件
- 通过 `DELETE /api/jobs/a2789ff719` 清理后端完成记录。
- 新增后端自愈：`get_job()` 发现内存里有 job，但磁盘 `meta.json` 已不存在且进程不活时，自动清掉内存脏状态并返回 404。
- 重启本地 `8766` 服务，使前端刷新后不再恢复旧完成卡片。

### 验证
- `curl http://127.0.0.1:8766/api/jobs/a2789ff719` → `404 Not Found`
- `curl http://127.0.0.1:8766/api/active-jobs` → `{"jobs":[]}`
- `find ~/.video-subber/jobs/a2789ff719 -maxdepth 1 -type f` 只剩 `.srt`
- 跟踪测试：`./.venv/bin/python -m pytest test/test_harden_v307_sprint2.py test/test_phase1d_qwen_health.py test/test_p160_layer2.py test/test_p160_stemmer.py -q` → `60 passed, 2 warnings`

## 2026-06-19 — Whisper 换库分支合并主线

### 完成事项
- 已在 `Whisper换库` 分支提交：`a1c1758 feat(V3.07): Whisper 换库并修复双稿参考链路`。
- 已切回 `main` 并使用 `git merge --no-ff Whisper换库` 合并，生成主线合并提交：`c9824bb merge: Whisper换库分支合入主线`。
- 合并提交没有包含 `.hex-skills/codegraph/index.db` 和 `v2/v2/`；前者继续作为本地索引缓存，后者留到 V3.08 目录整理时处理。

### 合并后验证
- 命令：`cd v2 && .venv/bin/python -m pytest test/test_p266_mlx_whisper.py test/test_harden_v307_sprint2.py test/test_phase1d_qwen_health.py test/test_p160_layer2.py test/test_p160_stemmer.py -q`
- 结果：`65 passed, 2 warnings`
- 两个 warning 都是 FastAPI `on_event` 弃用提醒，不是本次 Whisper 换库或 P284 修复导致的失败。

## 2026-06-20 P291 Refiner 滑窗润色（bug 修复流水线 ①-⑧ 完成）

**问题**：Refiner（LLM 润色模块）所有模式下都失效——把全部段（日语约 1009 段/~22000 token）一次性扔给 LLM，注意力被稀释，1 秒返回 `corrections=0`，字幕碎片句（如「父さんとお母さん今」）从未被润色。取代 P269。

**流水线①-⑧**：
- ① investigate：三根因坐实——RC1 无滑窗单次全量调用、RC2 `_looks_broken_sentence` 对无标点日语全选导致筛选失效、RC3 双稿只裁决不润色。
- ② plan-eng-review + steve-jobs：滑窗改「不重叠活跃区 + 两侧只读上下文 pad」（非 20% 重叠）；拆两 pass；窗口 120；`[?]` 改了就清没改保留（用户拍板）。
- ③ ln-021-codegraph + bill-gates：删函数零风险；签名兼容（按窗口传子集）；盖茨标记二阶效应——下游翻译首次吃润色稿，⑩ 必须端到端测完整链路。
- ④ /review + elon-musk：5 个旧代码雷；删假公式硬编码 120、删 2 筛选函数；Pass1 保留（L2 只标记不修正）。
- ⑤ 设计走查：五问通过，Pass1→Pass2 真数据依赖串行正确。
- ⑥ 安全：补 dual prompt 注入防护。
- ⑦ TDD：新建 `test_p291_refiner_sliding_window.py` 13 用例 RED→GREEN 13/13；新增 `_chunk_refiner_windows`/`RefinerWindow`/`_run_refiner`，`_build_refiner_prompt` 加 context 参，删 3 个函数。
- ⑧ verification：P291 13/13、相关旧测试 53/53、全量 613 passed。唯一失败 p201 经证明是预存测试隔离 flake（排除我的测试后全量仍 1 failed），非 P291 引入。

**改动文件**：`v2/backend/worker.py`（Refiner 段）；新增 `v2/test/test_p291_refiner_sliding_window.py`。

**待办**：⑨⑩ 端到端验收需 Mac 前台 GPU + DeepSeek API，用 MIDA-659.ts 跑标准+精准模式，看 `P291 Refiner 完成` 日志 pass2>0、`[?]` 数量、碎片句是否变通顺、进度条窗口进度。

**预存 flake（与 P291 无关，单独标记）**：`test_p201_qwen_aligner_check::test_returns_false_when_model_not_downloaded` 在全量套件里因 `sys.modules` mock 被加载真实 qwen 的测试污染而失败，单独跑通过。建议后续加测试隔离（reload 后恢复 mock）。

## 2026-06-22 V3.08 transcribe() 拆分 + P297 修复（HARDEN 七步全绿）

**本轮范围**：纯重构 `transcribe()` 上帝函数（412 行 → 115 行编排）；修复 P297（.gitignore 误伤测试套件）。零行为变更。

**HARDEN 七步**：
- ① /review + 对抗子审查：clean。对抗审查逐一核验 5 类「提取函数」重构风险（变量重绑定、别名语义、闭包时序、局变传回、执行顺序），结论 NO BEHAVIORAL DIFFERENCE。关键支柱：`apply_aligner_timestamps` 原地改+返回同对象，三别名（_sv_target/_fa_target/results）不分叉（已记 learning）。
- ② 设计一致性走查（走查 2.0）：14 阶段（A→T）一一对应；Q4 所有「等待」皆真数据依赖无假串行；Q5 results 数据链经手函数前后逐一对应无增减。重构顺手删掉一处「互不依赖」误导注释。
- ③ cso 安全审查（聚焦 diff）：clean。重构无新增 subprocess/eval/shell；65 个新纳入测试文件无真实密钥；敏感文件仍被忽略。
- ④ ln-021-codegraph：无循环依赖；22 个新 helper 调用链完整（无一进 unused 列表，抽查 2 个 conf=exact）。
- ⑤ verification：615 passed, 0 failed（100.94s）。
- ⑥ QA(Web)：跳过（本次是 CLI 转录链路，不涉前端）。
- ⑦ 端到端实测：trump 英文 18s 真实音频走完整 standard 链路，输出 5 段连贯字幕；日志确认 Phase A→T 各 helper 按序真实触发。

**改动文件**：`.gitignore`（test/→/test/）；`v3/backend/transcriber_v2.py`（拆 22 helper + _Routing dataclass，删空壳 _assemble_qwen_on_whisper_boundaries）；`v3/test/`（65 文件首次纳入版本控制）。

**已提交**：commit `1c4c4fa`（71 文件）。P296（闭包竞态）仍刻意保留待后续 bug 流水线。

## 2026-06-22 V3.08 余下函数拆分 ①②③（已提交 3258d40）

承接 transcribe 拆分（1c4c4fa）。策略（用户拍板 + 乔布斯副审一致）：**每拆一个跑全量 615 测试 + 详细记录**。

- ① `run_sensevoice` C13→B6（sensevoice_runner.py，抽 _load/_generate/_parse 3 helper）。615 passed。
- ② `_layer2_cross_validate` D30→C18（hallucination_filter.py，闭包 _find_best_window 提模块级 + _score_l2_window + _annotate_l2_segments）。615 passed。
- ③ `run_qwen_asr` C18→B8（qwen_asr_runner.py，抽 _resolve_qwen_duration + _qwen_transcribe）。**首跑测试抓到 1 失败**——P170 源码文本检查测试（assertIn "if not segments"）因守卫挪进 helper 而炸；按用户选择改成行为测试。615 passed。
- 详细记录见 `pipelines/refactor-pipeline-2026-06-22-remaining5.md`。
- **遗留新会话**：④ `translate_with_orchestrator` F(61)、211 行 + ⑤ `build_ass_styled` + 启动健康检查。
- **教训**：源码文本检查测试（inspect.getsource+assertIn）与重构天生冲突，应改行为测试；工具调用里 `| grep` 正则和超大 Edit 易触发解析退化，命令保持极简、改动拆小步。

## 2026-06-25 V3.08 函数复杂度拆分对话 C（orchestrator）

**本轮范围**：纯重构 `v3/backend/translation/orchestrator.py::_translate_whole_with_markers`，只搬代码，不改翻译逻辑、不改分块规则、不改接缝审查规则。

**完成事项**：
- `_translate_whole_with_markers` **F(44) → B(10)**。
- 抽出 7 个 helper：`_initialize_whole_translation`、`_build_overlapping_chunks`、`_build_whole_translation_prompt_prefix`、`_translate_chunks_with_prompt`、`_review_translation_seams`、`_finalize_whole_translation`。
- `test_arch9_dynamic_chunk.py` 中一条源码文本检查改为行为测试：patch `_compute_chunk_size=3`，确认 7 条字幕实际分成 3 次 LLM 调用。
- 新增管道文件：`pipelines/refactor-pipeline-orchestrator-whole-markers-2026-06-25.md`。
- 更新剩余规划：`pipelines/refactor-remaining-plan.md` 中对话 C 已打勾。

**验证**：
- 拆前基线：`624 passed, 8 warnings in 116.62s`
- 第一刀后：`624 passed, 8 warnings in 101.10s`
- 第二刀后：`624 passed, 8 warnings in 101.76s`
- 第三刀后：`624 passed, 8 warnings in 100.26s`
- 最终：`624 passed, 8 warnings in 98.63s`

**下一步**：剩余 B 组 `transcriber_v2.py` 三个函数，或 D 组 `main.py::create_job` + `sentence_splitter.py::llm_split_segments`。本轮未提交。

## 2026-06-25 V3.08 函数复杂度拆分对话 B（transcriber_v2）

**本轮范围**：纯重构 `v3/backend/transcriber_v2.py` 三个目标函数，只搬代码，不改转录逻辑、不改断句规则、不改模型路由、不改对外函数名。

**完成事项**：
- `_detect_speaker_changes` **D(28) → B(10)**。
- `_assemble_qwen_primary` **D(25) → A(2)**。
- `_whisper_is_usable` **D(22) → B(9)**。
- 新增管道文件：`pipelines/refactor-pipeline-transcriber-v2-2026-06-25.md`。
- 更新剩余规划：`pipelines/refactor-remaining-plan.md` 中对话 B 已打勾。

**验证**：
- 拆前基线：`624 passed, 8 warnings in 104.98s`
- `_whisper_is_usable` 拆后：`624 passed, 8 warnings in 105.24s`
- `_assemble_qwen_primary` 拆后：`624 passed, 8 warnings in 107.56s`
- `_detect_speaker_changes` 拆后：`624 passed, 8 warnings in 103.31s`

**下一步**：剩余 D 组 `v3/backend/main.py::create_job` + `v3/backend/core/sentence_splitter.py::llm_split_segments`。本轮未提交。

## 2026-06-26 V3.08 函数复杂度拆分对话 D（main + sentence_splitter）

**本轮范围**：纯重构 `v3/backend/main.py::create_job` 和 `v3/backend/core/sentence_splitter.py::llm_split_segments`，只搬代码，不改任务创建逻辑、不改断句规则、不改对外函数名。

**完成事项**：
- `llm_split_segments` **D(23) → A(3)**。
- `create_job` **D(25) → B(6)**。
- 新增管道文件：`pipelines/refactor-pipeline-main-sentence-splitter-2026-06-26.md`。
- 更新剩余规划：`pipelines/refactor-remaining-plan.md` 中对话 D 已打勾，A/B/C/D 四组全部完成。

**验证**：
- 拆前基线：`624 passed, 8 warnings in 115.10s`
- `llm_split_segments` 拆后：`624 passed, 8 warnings in 111.77s`
- `create_job` 拆后：`624 passed, 8 warnings in 109.84s`

**下一步**：按 `pipelines/refactor-remaining-plan.md` 跑调用图验证：`ln-021-codegraph analyze_architecture` + `ln-021-codegraph audit_workspace`。本轮未提交。

## 2026-06-26 V3.08 版本收尾 HARDEN 审查（未放行发布线）

**本轮范围**：V3.08 搬家 + 函数复杂度拆分 + P298/P299/P300 修复后的整体收尾审查。目标是查跨对话改动后的连贯性，不新增功能。

**完成事项**：
- 新增管道文件：`pipelines/harden-v308-2026-06-26.md`。
- 双签 diff + 设计一致性走查：主链路未发现 V3.08 拆分导致的漏传字段、顺序错位、状态丢失；P300 状态链方向正确。
- HARDEN 小修：后端服务标题从 V2 改为 V3；`v3/test/*.py` 顶部运行说明从 `cd v2` 改为 `cd v3`。
- 安全审查修复 P301：视频 URL 模式和 `/api/download-video` 复用 `_verify_safe_url()`，拦截本机/内网 URL。
- 安全审查修复 P302：`_validate_job_path()` 改用 `Path.relative_to()`，堵住同名前缀 sibling 路径绕过。
- 架构审查：`ln-021-codegraph` 当前无可执行命令，已用手工 `rg` + `radon` 降级审查；未发现 `worker_manager -> main` 循环依赖复活；复杂度平均 A。

**验证结果**：
- 安全相关测试：`14 passed, 2 warnings`。
- 排除 P207/P212 真实 ForcedAligner 模型测试后的主测试集合：`616 passed, 4 warnings`。
- 完整 `pytest test/ -q` 未完成：P207/P212 真实模型测试卡在 HuggingFace/Metal 路径；当前会话无 Metal 设备，非沙盒重跑被权限审查拒绝。
- Web QA：8766 可访问，PID `25324` 工作目录是 `v3`，但当前会话无法停止旧进程，不能证明服务加载了本轮最新改动。
- 真实短视频 E2E：`test/trump.mp4` 18.4 秒，worker 成功抽音频，但 standard 短 Whisper 探测在 `mlx_whisper` 导入时因无 Metal 设备失败，未生成 SRT。

**结论**：V3.08 目前**不能直接进入发布线**。代码审查和普通测试面通过，但发布前必须在可访问 Metal 的本机环境补跑：完整全量测试、重启 8766 到当前代码、真实短视频 standard E2E 并检查 SRT。

## 2026-06-26 V3.08 HARDEN 补验证（重启服务 + 完整全量测试）

**本轮范围**：只补两件发布前证据：把 8766 重启到当前 `v3` 代码；跑完整全量测试。真实短视频 standard E2E 留给用户手动跑。

**完成事项**：
- 已停止旧 8766 进程 PID `25324`。
- 已用当前 `/Users/andy/Andy-agent/video-subtitle/v3` 启动新服务：screen 会话 `88733.video-subtitle-v308-harden-verify`，监听 PID `88755`。
- `/api/health` 正常返回 `{"ok":true,"version":"3.07"}`；这里的版本号还没进入发布线 bump，不代表旧代码。
- 页面根路径 `/` 返回 HTTP `200 OK`。
- 进程工作目录确认是 `/Users/andy/Andy-agent/video-subtitle/v3`。

**验证**：
- 沙盒内完整测试卡在 HuggingFace/Metal 路径，人工中断时为 `266 passed, 1 skipped, 4 warnings`，不是断言失败。
- 提升权限后完整全量测试通过：`631 passed, 8 warnings in 101.94s`。

**下一步**：用户用真实短视频跑一次 standard 链路，检查 SRT 和日志正常。通过后再进入发布线。

## 2026-06-26 V3.08 用户真实短视频 standard E2E 监控

**本轮范围**：监控用户在网页上启动的真实短视频 standard 测试，并解释页面下方两个旧进度条的来源。

**测试结果**：
- 当前真实测试 job：`c6bcf446ec`，输入 `trump_zh.mp4`。
- 后端状态：`done`，进度 `100`，阶段 `burn`，消息 `完成`。
- 输出文件：`/Users/andy/.video-subber/jobs/c6bcf446ec/trump_zh.srt` 和 `/Users/andy/.video-subber/jobs/c6bcf446ec/trump_zh_zh_sub.mp4`。
- SRT 有 6 条中文翻译字幕。
- 日志显示 standard 链路正常：Whisper 短探测识别英文 → Qwen3-ASR 主引擎 → SenseVoice → Refiner → DeepSeek 翻译 5/5 成功 → 输出封装，总耗时 33 秒。

**新发现**：
- P303：页面下方两个橙色进度条不是当前任务，而是旧 URL 安全测试任务 `cc7d373829` 和 `e25373d4d3` 被 `/api/active-jobs` 当作 `interrupted` 恢复出来。

**下一步**：发布线前建议先处理 P303，或明确把 P303 记为不阻塞发布的已知 UI 问题；核心 E2E 证据已经补齐。

## 2026-06-26 P303 根因复查 + E2E 产物复核

**P303 根因补充**：
- 两个橙色旧卡片来自 `cc7d373829` 和 `e25373d4d3`。
- 它们的磁盘 `meta.json` 仍是 `queued`，不是 `interrupted`。
- 服务启动扫描历史 job 时，看到 `queued` 且没有 `output.srt`，就在内存 `GLOBAL_HASH_INDEX` 里改成 `interrupted`。
- `/api/active-jobs` 返回内存里的 `interrupted`。
- 前端本地 `activeJobs` 为空时拉取 `/api/active-jobs`，再把这些 id 写入本地并渲染卡片。
- 因为内存状态没有写回磁盘，所以服务重启后会再次重复这条链路。

**E2E 复核**：
- 最新 job 仍是 `c6bcf446ec`，`/api/jobs/c6bcf446ec` 返回 `done`、进度 `100`、阶段 `burn`。
- 输出文件存在：`/Users/andy/.video-subber/jobs/c6bcf446ec/trump_zh.srt`、`/Users/andy/.video-subber/jobs/c6bcf446ec/trump_zh_zh_sub.mp4`。
- 输出也已复制到 `/Users/andy/Downloads/Video-Subber/trump_zh.srt` 和 `/Users/andy/Downloads/Video-Subber/trump_zh_zh_sub.mp4`。
- 日志未命中 `ERROR`、`Traceback`、`Exception` 或“失败”。

## 2026-06-26 P303 修复完成：旧 interrupted/queued 任务不再自动恢复成页面卡片

**本轮范围**：只修 P303。目标是阻止两个旧 URL 安全测试任务 `cc7d373829`、`e25373d4d3` 在页面下方反复显示成橙色“任务中断”卡片；不新增功能，不改转录/翻译链路。

**流水线记录**：
- 新增 `pipelines/bug-pipeline-2026-06-26-p303-active-jobs.md`。
- 已完成 investigate、双签方案审查、双签影响面、diff/简化审查、设计一致性走查、安全审查、TDD、verification、Web QA。
- `ln-021-codegraph` 当前无可执行命令，本轮影响面按 Skill 降级规则用 `rg` 静态扫描完成。

**修复**：
- `v3/backend/main.py` 新增 `_is_recoverable_active_job()`。
- `/api/active-jobs` 对 `interrupted` 任务只返回可恢复任务：
  - 有 `input.*` 的上传/本地文件任务可以返回。
  - 有安全 `source_url` 的 URL 任务可以返回。
  - 本机/内网 URL、且没有真实输入文件的旧任务不再返回。
- 没有删除旧 job 目录；没有改前端；没有新增状态。

**验证**：
- TDD 红灯：P303 新测试先失败，证明旧代码会返回私网 URL interrupted job。
- P303 目标测试：`2 passed, 2 warnings`。
- 相关回归：`51 passed, 2 warnings`。
- 完整全量测试：`633 passed, 8 warnings in 97.44s`。
- 已重启 8766 到当前 `v3` 代码：screen 会话 `17117.video-subtitle-v308-p303-verify`，监听 PID `17139`。
- `/api/health` 返回 `{"ok":true,"version":"3.07"}`。
- `/api/active-jobs` 返回 `{"jobs":[]}`。

**状态**：P303 已修。V3.08 发布线前的已知 UI 阻塞项清掉；下一步可按发布线规则先跑 verification，再决定版本号/commit/tag。
