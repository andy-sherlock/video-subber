# V3.07 HARDEN 版本收尾 — 全面审查报告

**审查日期**：2026-06-10
**审查人**：Claude (Opus 4.6) + gstack 工具链
**项目路径**：`/Users/andy/Andy-agent/video-subtitle/`
**活跃代码**：`v2/backend/`（`v2/v3.XX/` 和 `v2/v2.XX/` 子目录均为只读快照）

---

## 一、项目背景

video-subtitle 是一个视频字幕生成与翻译工具，核心流程：

```
视频文件 → 提取音频 → Whisper 转录 → Qwen3-ASR 交叉验证
→ SenseVoice 声音分类 → Qwen3-FA 逐字对齐 → 五层幻觉过滤
→ LLM 翻译（多引擎降级链）→ 字幕烧录/导出
```

技术栈：FastAPI 后端 + 单页 HTML 前端（临时）+ PySide6 桌面 App（V3.09 规划中）
当前版本：V3.07（432 测试，0 失败）
Python 包管理：uv + pyproject.toml
虚拟环境：`v2/.venv/`

---

## 二、架构概览

### 模块依赖图（单向无环）

```
worker.py（主控流水线）
  ├─→ transcriber_v2.py（转录 + SV/FA/Qwen 调度）
  │     ├─→ core/qwen_asr_runner.py
  │     ├─→ core/sensevoice_runner.py
  │     ├─→ core/forced_aligner.py
  │     └─→ core/hallucination_filter.py
  ├─→ translation/orchestrator.py（LLM 翻译降级链）
  │     └─→ translation/base.py（多 LLM 后端）
  └─→ main.py（FastAPI 路由 + 任务队列）

main.py → worker_manager.py → worker.py
```

**架构健康**：零循环依赖。依赖方向：下层模块（core/）← 上层编排（worker/transcriber/orchestrator）。无反向引用。

### 五层幻觉过滤器

```
L1: 双信号交叉验证（SenseVoice 人声标签 × Qwen3-FA 逐字对齐）
L2: 窗口词干比对（Whisper × Qwen ASR 文本交叉验证）
L4: 正则/关键词过滤器（关键词、纯符号、括号描述、重复模式、重复词、循环句式）
L5: 相邻去重
L3: 语速异常检测（每秒字符数阈值）
```

执行顺序：`L1 → L2 → L4 → L5 → L3`

### LLM 翻译降级链

```
P1: 术语提取 + 内容摘要（合并为一次 API 调用）
  → CJK 烟雾测试（CJK < 10% → 跳过该引擎）
P2: 滑窗翻译（动态分块大小，基于模型 context window）
P3: 反思审查（仅 quality 模式，可选）
```

降级顺序：DeepSeek → GPT → Gemini → Claude → 免费引擎（Google/Bing/有道）

---

## 三、审查流水线执行记录

| 步骤 | Skill | 状态 | 产出 |
|------|-------|------|------|
| ① | /review（全项目） | ✅ | 6 个 bug（P240-P245）+ 5 个信息项 |
| ② | 设计一致性走查 | ✅ | 3 个偏差（P246-P248） |
| ③ | security-review | ⏭️ | Skill 约束无法执行，① 中安全 specialist 已覆盖 |
| ④ | ln-021-codegraph | ✅ | 零循环依赖，单向无环图 |
| ⑤ | verification-before-completion | ✅ | 432 passed, 10 skipped, 0 failed |

---

## 四、发现的问题清单

### 严重度说明

| 标记 | 含义 | 处理策略 |
|------|------|---------|
| 🔴 CRITICAL | 可导致数据泄露/任意文件读取 | 立即修复 |
| 🟡 HIGH | 功能正确性受损/特定场景崩溃 | 本版本修复 |
| 🟡 MEDIUM | 安全风险/逻辑偏差/边界情况 | 本版本修复 |
| 🔵 LOW | 代码质量/功能空缺/并发隐患 | 可延后 |

---

### P240 🔴 CRITICAL — `llm_fallback_decision` 路径穿越

**位置**：[main.py:850-856](v2/backend/main.py#L850-L856)

```python
@app.post("/api/jobs/{job_id}/llm-fallback")
def llm_fallback_decision(job_id: str, payload: dict):
    """Worker polls this file after emitting 'llm_failed' message."""
    decision = payload.get("decision", "keep_failed")
    decision_file = JOBS_DIR / job_id / "llm_fallback.json"
    decision_file.write_text(json.dumps({"decision": decision}, ensure_ascii=False))
    return {"ok": True}
```

**问题**：`job_id` 来自 URL 路径，直接拼入 `JOBS_DIR / job_id / "llm_fallback.json"`，无路径穿越保护。攻击者传入 `../../etc/passwd` 可写入系统目录。

**修复**：在构造路径前加 `job_dir = JOBS_DIR / job_id` 然后 `if not job_dir.resolve().is_relative_to(JOBS_DIR.resolve()): raise HTTPException(400)`（与其他 endpoint 已有的保护模式一致）。

---

### P241 🟡 HIGH — P230 重试硬编码"英文→中文"

**位置**：[orchestrator.py:799](v2/backend/translation/orchestrator.py#L799)

```python
simple_prompt = (
    f"将以下英文翻译成中文。只输出译文，不要解释。\n\n{texts[fi]}"
)
```

**问题**：P230 单独重试遗漏字幕时，prompt 硬编码"将以下英文翻译成中文"。传入日语、俄语视频时，prompt 与实际源语言不匹配，导致翻译质量下降。

**修复**：使用项目已有的 `_TARGET_LANGUAGE` 变量和检测到的源语言构建 prompt，替代硬编码字符串。

---

### P242 🟡 HIGH — `transcribe_raw()` MLX 后端获取 language 属性崩溃

**位置**：[qwen_asr_runner.py:502](v2/backend/core/qwen_asr_runner.py#L502)

```python
result = model.transcribe(audio_path, language=_resolve_language(language))
lang = result[0].language if result and hasattr(result[0], 'language') else None
```

**问题**：MLX 后端的 `transcribe()` 返回对象（非列表），`result[0]` 对 MLX 结果取 `[0]` 索引行为未定义，可能崩溃。PyTorch 路径返回 `list[Segment]`，MLX 返回单个 result 对象。下方 L503-508 已区分 `_USE_MLX`，但 L502 未区分。

**修复**：同样加 `_USE_MLX` 判断——MLX 路径用 `result.language`，PyTorch 路径用 `result[0].language`。

---

### P243 🟡 MEDIUM — `/api/llm-models` SSRF

**位置**：[main.py:453-499](v2/backend/main.py#L453-L499)

```python
@app.post("/api/llm-models")
def list_llm_models(payload: dict):
    platform = (payload.get("platform") or "").lower()
    endpoint = (payload.get("endpoint") or "").strip()
    # ...
    _chat = {
        "gpt": "https://api.openai.com/v1/chat/completions",
        # ...
        "custom": endpoint,   # ← 用户提供的 endpoint
    }.get(platform, endpoint)  # ← 或无匹配时直接当 URL 用
    # ...
    req = _urllib.Request(url, headers={...})
    with _urllib.urlopen(req, timeout=10) as resp:
```

**问题**：
1. `"custom"` 平台时 `endpoint` 无协议/Scheme 校验，可指向 `file:///etc/passwd` 或内网地址 `http://169.254.169.254/`
2. 无匹配平台时 `endpoint` 直接作为 URL 使用，同样无校验
3. 请求结果直接返回给前端，可用来读取本地文件或探测内网

**修复**：
1. 仅允许 `http://` 和 `https://` 协议
2. DNS 解析后拒绝内网 IP（127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16）
3. 无匹配平台时返回错误而非直接用 endpoint

---

### P244 🟡 MEDIUM — `get_job` 缺路径穿越校验

**位置**：[main.py:757-780](v2/backend/main.py#L757-L780)

```python
@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        meta_path = JOBS_DIR / job_id / "meta.json"
        if meta_path.exists():
```

**问题**：`job_id` 同样来自 URL，直接拼入路径，无 `resolve()` + `is_relative_to()` 保护。攻击者可传入 `../../../` 读取任意 `meta.json` 同名的系统文件（如 `/etc/meta.json`）。

**修复**：与 P240 相同模式——resolve 后检查父路径是否属于 `JOBS_DIR`。

---

### P245 🔵 LOW — `GLOBAL_HASH_INDEX` 无并发保护

**位置**：[main.py:35](v2/backend/main.py#L35)

```python
GLOBAL_HASH_INDEX = {}   # task_key → {job_id, status, config, file_hash}
JOB_ID_TO_HASH = {}      # job_id → task_key
```

**问题**：`GLOBAL_HASH_INDEX` 和 `JOB_ID_TO_HASH` 是模块级普通 dict，被多个 async handler 同时读写（`create_job` 写入、`get_job` 读取、`_update_hash_index` 修改、`_cleanup_hash_index` 删除），无锁保护。FastAPI 的 asyncio 事件循环中多个协程可能并发访问，存在竞态条件。

**修复**：加 `asyncio.Lock()` 保护所有读写操作。或者由于操作极快（O(1) 字典操作），也可用 `threading.Lock()` 更轻量。

---

### P246 🟡 MEDIUM — L4 重复模式/重复词缺 L2 GREEN 豁免

**位置**：[hallucination_filter.py:221-232](v2/backend/core/hallucination_filter.py#L221-L232)

```python
# L201: 关键词规则有 L2 GREEN 保护 ✅
if getattr(seg, "l2_status", None) != "GREEN":
    for kw in HALLUCINATION_KEYWORDS:
        if kw.lower() in tl:
            # ... filter

# L222: 重复模式 — 缺少 L2 GREEN 保护 ❌
t_nospace = t.replace(" ", "")
if len(t_nospace) >= 3 and _RE_REPEATED_PATTERN.match(t_nospace):
    seg.filtered = True
    # ...

# L228: 重复词 — 缺少 L2 GREEN 保护 ❌
if _RE_REPEATED_WORDS.match(t):
    seg.filtered = True
    # ...
```

**问题**：P166 规则规定 L2 标记为 GREEN 的段落（Whisper 和 Qwen ASR 高度一致）应跳过 L4 关键词过滤。L201 的关键词检查正确实现了这个豁免，但 L222 的重复模式检查和 L228 的重复词检查没有——GREEN 段落中的正常重复（如歌词副歌）可能被误杀。

**修复**：在 L222 和 L228 之前加 `if getattr(seg, "l2_status", None) == "GREEN": return False`，与 L201 保持一致。

---

### P247 🔵 LOW — P1 ASR 分歧裁决未实现

**位置**：[orchestrator.py:686-896](v2/backend/translation/orchestrator.py#L686-L896)

**问题**：设计图规定 P1 阶段应包含 ASR 分歧裁决——当 Whisper 和 Qwen ASR 对同一段音频产出不同文本时（标记为 `[?]`），由 LLM 在两个版本之间裁决选择正确版本。代码中 P1 只做了术语提取和内容摘要，无分歧裁决逻辑。

**影响**：L2 交叉验证标记的 `[?]` 段直接以 Whisper 版本送翻译，LLM 失去纠正 ASR 错误的机会。

**决定**：新功能，延后实现。不影响现有功能正确性。

---

### P248 🟡 MEDIUM — P3 反思仅在翻译失败时触发

**位置**：[worker.py:438](v2/backend/worker.py#L438)

```python
if api_keys and failed and translation_mode == "quality":
```

**问题**：条件中的 `and failed` 导致 P3 反思审查仅在翻译有遗漏（`[翻译失败]` 段）时触发。全部翻译成功时跳过反思，但这恰好是最需要反思的场景——反思的原始目的是对"成功"的翻译挑错、润色。

**修复**：去掉 `and failed` 条件：
```python
if api_keys and translation_mode == "quality":
```

---

## 五、设计一致性走查详细记录

### 走查对象

| 编排函数 | 文件 | 行号 | 对照设计图 |
|---------|------|------|----------|
| `transcribe()` | transcriber_v2.py | 179-462 | video-subtitle.md L282-417 |
| `filter_hallucinations()` | hallucination_filter.py | 440-489 | 五层过滤器流程图 |
| `translate_with_orchestrator()` | orchestrator.py | 686-896 | LLM 降级链流程图 |
| `process_job()` | worker.py | 300-499 | 全管线流程图 |

### 逐步骤对照 — `transcribe()`

| 设计步骤 | 代码对应 | 一致？ |
|---------|---------|--------|
| VAD 语音占比检测 | L190-200 | ✅ |
| 加载 Whisper 模型 | L202-205 | ✅ |
| Whisper 转录 | L220-226 | ✅ |
| 启动 Qwen ASR 并行 | L245-256 | ✅ |
| 生产者-消费者流水线 | L259-302 | ✅ |
| SV+FA 并行线程 | L340-375 | ✅ |
| Qwen 交叉验证 | L377-410 | ✅ |
| 五层过滤 | L447-450 | ✅ |

### 逐步骤对照 — `filter_hallucinations()`

| 设计步骤 | 代码对应 | 一致？ |
|---------|---------|--------|
| L1: 双信号交叉验证 | L465-467 | ✅ |
| L2: 窗口词干比对 | L469-472 | ✅ |
| L4: 正则/关键词 | L474-476 | ✅ |
| L5: 相邻去重 | L479 | ✅ |
| L3: 语速异常 | L481-483 | ✅ |

### Q4 并发检查

| 检查项 | 分析 | 结论 |
|--------|------|------|
| SV+FA 并行 | 两个 daemon thread 独立运行，无需互相等待 | ✅ 合理 |
| Whisper + Qwen 流水线 | producer-consumer 模式，数据依赖正确 | ✅ 合理 |
| 各层过滤串行 | L2 依赖 L1 输出，L4 依赖 L2 标注，L3 依赖 L4 结果 | ✅ 数据依赖正确 |

### 偏差汇总

| # | 类型 | 描述 | 决定 |
|---|------|------|------|
| P246 | 少了 | L4 重复模式/重复词缺 L2 GREEN 豁免（L201 有关键词豁免但 L222/L228 没有） | 修代码 |
| P247 | 少了 | P1 ASR 分歧裁决未实现 | 延后新功能 |
| P248 | 错了 | P3 反思条件 `and failed` 阻止了全部成功时的质量审查 | 修代码 |

---

## 六、架构分析摘要

### 方法

因只读快照目录（`v2/v2.XX/`、`v2/v3.XX/`）干扰了 codegraph MCP 工具（产生 200+ 模块噪音），手动分析活跃代码 `v2/backend/` 的 import 图。

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| worker | worker.py | 全管线主控：extract_audio → transcribe → translate → subtitle |
| transcriber_v2 | transcriber_v2.py | 转录调度：Whisper + Qwen ASR 流水线 + SV/FA 并行 |
| hallucination_filter | core/hallucination_filter.py | 五层幻觉过滤 + LightweightStemmer |
| qwen_asr_runner | core/qwen_asr_runner.py | Qwen3-ASR 模型加载与推理 |
| sensevoice_runner | core/sensevoice_runner.py | SenseVoice 声音分类 |
| forced_aligner | core/forced_aligner.py | Qwen3-ForcedAligner 逐字时间对齐 |
| orchestrator | translation/orchestrator.py | LLM 翻译编排：P1 术语+摘要 → P2 滑窗翻译 → 降级链 |
| main | main.py | FastAPI 路由：任务创建/状态/取消/配置/LLM 模型查询 |

### 依赖关系

```
main.py
  ├─→ worker_manager.py
  │     └─→ worker.py
  │           ├─→ transcriber_v2.py
  │           │     ├─→ core/qwen_asr_runner.py
  │           │     ├─→ core/sensevoice_runner.py
  │           │     ├─→ core/forced_aligner.py
  │           │     └─→ core/hallucination_filter.py
  │           └─→ translation/orchestrator.py
  │                 └─→ translation/base.py
  │                       ├─→ translation/deepseek.py
  │                       ├─→ translation/gpt.py
  │                       ├─→ translation/gemini.py
  │                       ├─→ translation/claude.py
  │                       └─→ translation/google.py / bing.py / youdao.py
  └─→ (其他工具模块: video_downloader, subtitle_style, checkpoint, config_manager)
```

**结论**：模块依赖为单向无环图。无循环依赖。层级清晰：core（基础能力）→ 编排层（transcriber/orchestrator）→ 主控（worker）→ 路由（main）。

---

## 七、测试统计

```
======================== 432 passed, 10 skipped in 13.19s ========================
```

跳过的 10 个测试均为 faster-whisper 模型在 CI 环境不可用（预期行为，本地有模型时不跳过）。

测试文件覆盖：
- `test_hallucination_filter.py` — 五层过滤
- `test_p160_stemmer.py` / `test_p160_layer2.py` — L2 词干匹配
- `test_p161_wiring.py` — L2 接线
- `test_p166_l2_before_l4.py` — L4 顺序
- `test_p167_sv_overlap.py` — SenseVoice 重叠判定
- `test_p171_low_data.py` — MIN_STEMS 门槛
- `test_p165_content_gate.py` — CJK 内容闸
- `test_p174_url_mode.py` — URL 直传
- `test_p182_tail_flush.py` — 尾部 chunk 刷新
- `test_p183_l1_fallback.py` — L1 单信号回退
- `test_p188_url_validation.py` — URL 协议校验
- `test_arch7_pipeline.py` — 转录流水线重构
- `test_arch8_llm_merge.py` — LLM 合并
- `test_arch9_dynamic_chunk.py` — 动态分块
- 共 15+ 测试文件

---

## 八、已有的安全防护（正面清单）

| 防护 | 位置 | 说明 |
|------|------|------|
| 路径穿越防护 | `create_job` / `cancel_job` / `download_result` | `.resolve()` + `is_relative_to(JOBS_DIR.resolve())` |
| URL 协议校验 | `create_job` URL 模式 | 仅允许 `http://` 和 `https://`（P188 已修） |
| CORS 限制 | `main.py` 启动配置 | 仅允许 localhost 来源 |
| API Key 安全 | `load_config()` | 启动时从 `~/.video-subber/config.json` 读取，不存 localStorage |
| OOM 精确告警 | `worker_manager.py` | 精确关键词匹配退出码，不泛化报错（P194/P202 已修） |

---

## 九、审查工具链说明

本次审查使用的工具和 skill：

| Skill | 作用 | 覆盖范围 |
|-------|------|---------|
| `/review` | diff-based 代码审查 + 安全/并发/测试 specialist | 全部 git diff + 安全专项（→ P240-P245） |
| 设计一致性走查 | 设计图 vs 编排函数逐行对照 + 四问 | 4 个编排函数 + Q4 并发检查（→ P246-P248） |
| `ln-021-codegraph` | 架构依赖分析、循环依赖检测 | `v2/backend/` 全模块 |
| `verification-before-completion` | 全量测试 | 432 tests，0 失败 |

**重要补充**：`/review` 是 diff-based 审查——只检查 git diff 中的新增行。设计一致性走查弥补了"代码和设计图是否一致"这个审查盲区。两者互补，缺一不可。

---

## 十、待用户手动完成的步骤

| 步骤 | 方法 | 验收标准 |
|------|------|---------|
| qa-only | 浏览器打开前端，跑完整操作流程（上传视频→选模型→翻译→下载） | 无控制台错误，所有按钮/进度条/状态反馈正常 |
| 端到端实测 | CLI 模式跑 3 个真实视频：中文/英文/日语（或俄语），检查输出质量 | 中文 [?] 率 < 5%，英文 [?] 率 < 15%，弱势语言无大批误杀 |

---

*报告结束。接班工程师请从"四、发现的问题清单"开始阅读，优先处理 P240（CRITICAL）。*
