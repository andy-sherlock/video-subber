# V3.08 批量诊断报告 — 6 个代码 Bug

**日期**：2026-06-11
**诊断工具**：investigate (gstack)
**Git**：b1a602b59f（V3.07 HARDEN Sprint 2）

---

## 关联分析

这 6 个 bug 分属 4 个独立子系统，彼此不直接耦合：

| 子系统 | Bug | 关联 |
|--------|-----|------|
| L3 语速检测 | P250 | 独立 |
| Qwen-to-L2 数据管道 | P251 | 独立（但修复后 L2 精度大幅提升，可能让 P250 的假 [?] 更显眼） |
| 前端重连 | P181 | 独立 |
| Qwen ASR 后端兼容 | P225 + P242 | **强关联** — 都是 `model.transcribe()` 返回值取法不对，一个在 PyTorch 路径，一个在 MLX 路径 |
| 全局哈希索引 | P245 | 独立 |

**建议修复顺序**：P250 → P251 → P225+P242（一起修） → P181 → P245 → P253（独立，最后修）

---

## P250 — L3 语速单位用错 + 阈值过低（🔴）

**位置**：`hallucination_filter.py:419-439`

**根因**：`hallucination_filter.py:421` 用 `text_len = len(seg.text.strip())` 对所有语言数字符。英文一个单词平均 5-6 字符，`len()` 把字母当字数，导致英文语速被高估 5-6 倍。阈值表 `en: 25`（L116）用的是字符/秒单位，但 25 cps ÷ 6 ≈ 4 单词/秒，正常说话就触发。

**当前代码**：

```python
# hallucination_filter.py:113-119
_SPEECH_RATE_THRESHOLDS = {
    "zh": 15,
    "ja": 15,
    "en": 25,
    "ru": 25,
    "auto": 20,
}

# hallucination_filter.py:419-439
def _layer3_speech_rate(seg, source_lang: str = "auto") -> None:
    dur = seg.end - seg.start
    text_len = len(seg.text.strip())        # ← 字符数，不是语音单位

    if dur < 0.1 and text_len > 5:          # ← 英文 "Apple!" = 6 字符但只 1 词
        seg.filtered = True
        ...

    cps = text_len / dur                    # ← 字符/秒，英文高估 5-6x
    threshold = _SPEECH_RATE_THRESHOLDS.get(source_lang, ...)
    if cps > threshold:
        seg.uncertain = True
```

**已有方案审查**（problems.md 定稿版 — 按语种查表分治）：

方案完整，包含 `count_speech_units()` 函数 + 阈值表。边界审查：

1. **阿拉伯语/希伯来语**：RTL 语言不在字典里，走 `else` 兜底用 `split()` 数单词。阈值继承 `auto: 7`。阿拉伯语有空格分词，7 单词/秒合理。**无问题。**
2. **越南语**：有空格分词但分的是音节（每个方块一音节），走兜底 `split()` 会按音节数算。`auto: 7` 音节/秒偏低（越南语正常 8-10 音节/秒）。**低优先级，先不管。**
3. **L421 的粗筛**：方案改为 `dur < 0.1 and units > 2`。正确——`text_len > 5` 对英文 `"Apple!"` 是 6 字符但只有 1 个单词，会被误杀。
4. **`source_lang` 参数传递**：`filter_hallucinations()` 已接受 `source_lang` 参数并传给 `_layer3_speech_rate()`。**无问题。**

**结论：方案可以直接实施，无阻塞性遗漏。**

**改动量**：~50 行改 1 文件（hallucination_filter.py）
**风险**：低

---

## P251 — align_by_similarity 截流 Qwen 输出（🔴）

**位置**：`qwen_asr_runner.py:85-157, 377-379` + `hallucination_filter.py:341`

**根因**：`qwen_asr_runner.py:379` 的 `align_by_similarity(full_text, chunk_segments)` 把 Qwen 的每个句子 1:1 贴到某个 Whisper 碎片上。124 个 Qwen 句子 → 最多 124 个碎片有 `qwen_text`，524 个碎片中约 400 个 `qwen_text = None`。L2 在 `hallucination_filter.py:341` 按窗口汇聚 `seg.qwen_text`——空碎片占多数的窗口直接变成 `NO_QWEN`。

**当前代码**：

```python
# qwen_asr_runner.py:377-379（chunk 处理末尾）
        align_by_timestamps(ts_all, chunk_segments)
    else:
        align_by_similarity(full_text, chunk_segments)  # ← 截流

# hallucination_filter.py:341（L2 窗口聚合）
    qwen_windows[win] += " " + (getattr(seg, "qwen_text", None) or "")  # ← 被截流

# hallucination_filter.py:356-358（NO_QWEN 判定）
    if not q_text:
        window_stats[idx] = {"status": "NO_QWEN", ...}  # ← 26% 窗口静默通过
```

**已有方案审查**（方案 C — 砍掉映射）：

方向正确，但影响面比 problems.md 描述的更大。`seg.qwen_text` 的全部引用点：

| 文件 | 位置 | 用法 | 需要改 |
|------|------|------|--------|
| `transcriber_v2.py:46` | `Segment.__init__` | 字段定义 | 可以保留（或改为新字段） |
| `worker.py:337` | checkpoint 序列化 | 存 `qwen_text` | 需同步改 |
| `worker.py:349` | checkpoint 反序列化 | 取 `qwen_text` | 需同步改 |
| `hallucination_filter.py:277` | `_qwen_is_reliable()` | **坍缩检测** | **需要改为从 Qwen chunk 全文检测** |
| `hallucination_filter.py:341` | L2 窗口聚合 | **核心数据源** | **需要改为按窗口时间取 chunk 全文** |
| `qwen_asr_runner.py:129-130` | `align_by_timestamps` 内部 | 写入 `seg.qwen_text` | 随函数一起删 |
| `qwen_asr_runner.py:150,156` | `align_by_similarity` 内部 | 写入 `seg.qwen_text` | 随函数一起删 |
| `qwen_asr_runner.py:449` | `run_qwen_asr` 统计 | 日志 | 需同步改 |
| `qwen_asr_runner.py:522` | 初始化清空 | 设置 None | 需同步改 |
| `qwen_asr_runner.py:591` | 统计 | 日志 | 需同步改 |

**关键遗漏**：`_qwen_is_reliable()`（L277）用 `seg.qwen_text` 做坍缩检测（P148）。砍掉 `seg.qwen_text` 后，需改为从 Qwen chunk 全文列表检测重复。

**结论：方案 C 方向正确，影响面比预想大。实施时必须覆盖坍缩检测 + checkpoint + 统计日志。不阻塞实施。**

**改动量**：~120 行改 4 文件（qwen_asr_runner.py, hallucination_filter.py, worker.py, transcriber_v2.py）
**风险**：中

---

## P225 + P242 — Qwen ASR 返回值取法错误（🔴 + 🟡）

**P225 和 P242 是同一个 bug 的两个面，建议一个 commit 修。**

**位置**：`qwen_asr_runner.py:278-286`（chunked 路径） + `qwen_asr_runner.py:502-508`（直接路径）

**根因**：Qwen-ASR v0.0.6 的 `model.transcribe()` 返回值：
- **PyTorch 后端**：返回列表 `[TranscriptionResult(...)]`，需 `result[0].text`
- **MLX 后端**：返回单个对象，直接 `result.text`

代码问题：

```python
# 直接路径 qwen_asr_runner.py:502（MLX 判断之前！）
lang = result[0].language if result and hasattr(result[0], 'language') else None
#          ↑ MLX 下 result 不是列表，result[0] 崩溃

if _USE_MLX:
    text = result.text if result.text else ""   # L504: 正确但走不到
    ts = None
else:
    text = result[0].text if result else ""      # L507: PyTorch 正确
```

L502 的 `result[0].language` 在 `if _USE_MLX` 判断**之前**执行——MLX 后端直接崩溃。

chunked 路径 `qwen_asr_runner.py:278-286` 已经有 `if _USE_MLX` 分支，但同样的 bug 可能存在于分支前的代码。

**修复方案**：把所有结果提取统一放入 `if _USE_MLX` 分支内部：

```python
# 直接路径修复
if _USE_MLX:
    text = result.text if result and hasattr(result, 'text') else ""
    lang = getattr(result, 'language', None)
    ts = None
else:
    text = result[0].text if result else ""
    lang = result[0].language if result and hasattr(result[0], 'language') else None
    ts = result[0].time_stamps if result else None
```

同样的修复应用到 chunked 路径。

**改动量**：~10 行改 1 文件（qwen_asr_runner.py）
**风险**：低

---

## P181 — handleFoundJob 跳过 connectCard（🟡）

**位置**：`frontend/index.html:1069-1090`

**根因**：当 `handleFoundJob` 找到一个正在运行的任务且卡片已存在时（else 分支 L1080），代码只在 `!activeJobs[d.job_id]` 时才调 `connectCard`（L1085）。

**关键路径**：
1. 用户刷新页面 → `activeJobs` 从 localStorage 恢复 → `activeJobs[jid]` 存在但 `.ws=null`、`.pollTimer=null`
2. `handleFoundJob` 走 else 分支 → `activeJobs[d.job_id]` 已存在 → **跳过 `connectCard`**
3. WebSocket 和轮询都不启动，卡片进度条冻结

**当前代码**：

```javascript
// index.html:1080-1088
else{
    card.classList.remove('job-card-done');
    const fill=$('fill-'+d.job_id);const step=$('step-'+d.job_id);
    if(step){step.textContent='正在恢复处理…';step.style.color=''}
    if(fill){fill.style.width='0%';fill.style.background=''}
    if(!activeJobs[d.job_id]){                           // ← 已存在就跳过
        activeJobs[d.job_id]={ws:null,pollTimer:null,...};
        connectCard(d.job_id)                             // ← 不调用
    }
    card.scrollIntoView({behavior:'smooth'});
    showToast('✅ 已恢复任务，正在继续处理…')
}
```

**修复方案**：不管 `activeJobs[jid]` 是否存在，只要任务是 running/queued，都调 `connectCard`。`connectCard` 内部已处理 WebSocket 重连：

```javascript
if(!activeJobs[d.job_id]){
    activeJobs[d.job_id]={ws:null,pollTimer:null,
        filename:card.querySelector('.job-card-name').textContent,
        config:{...d.config}};
}
connectCard(d.job_id)  // 始终调用
```

**改动量**：~3 行改 1 文件（index.html）
**风险**：低

---

## P253 — 翻译滑窗阈值用上下文上限而非疲劳区间（🟡）

> **补充时间**：2026-06-11 14:xx，P253 后并入本批次（原批次 6 个 → 7 个）。
> 诊断来源：problems.md P253 自审方案 + 关联分析（非独立重型 investigate，因改动仅 ~5 行、根因明确）。

**位置**：`translation/orchestrator.py:329-338`（`_compute_chunk_size()`）

**根因**：当前公式 `(max_input_tokens - 18000) // 8`，clamp [200, 3000]，算的是「上下文还剩多少空间」——装得下就全塞。但 LLM 有「迷失在中间」(Lost in the Middle) 现象：输入超上下文窗口 30-40% 后，模型对中间内容关注度显著下降。公式没考虑这个疲劳区。实例：32K 模型当前允许 1750 行/批 ≈ 14000 tokens = 44% 上下文，已踩进疲劳区。

**已有方案审查**（problems.md 定稿）：把公式从「上下文上限」改为「疲劳上限」，疲劳系数 30%。

```python
def _compute_chunk_size(llm) -> int:
    ctx = getattr(llm, 'max_input_tokens', 32768) or 32768
    fatigue = int(ctx * 0.30)       # 30% 疲劳系数
    available = fatigue - 2000      # prompt 开销
    raw = available // 8
    return max(200, min(raw, 1200))
```

效果：32K 模型 1750→950 行/批，64K+ 上限 3000→1200。

**关联分析**：P253 在**翻译模块**（orchestrator.py 分块逻辑），与其余 6 个 bug 完全不同子系统——P250/P251/P225/P242 在转录、P181 在前端、P245 在 infra。**P253 独立，与其余 6 个无耦合**，可任意顺序修。

**改动量**：~5 行 / 1 文件（orchestrator.py）
**风险**：低

---

## P245 — GLOBAL_HASH_INDEX 无并发保护（🔵）

**位置**：`main.py:69-70`

**根因**：`GLOBAL_HASH_INDEX = {}` 被多个线程访问：
- asyncio 事件循环（async 端点）：L707 get、L752 set、L775 遍历
- worker 线程回调：L579-582 `_update_hash_status()`、L600-602 `_cleanup_hash_index()`
- startup 线程：L209-241 构建索引

GIL 保护了 dict 的单次操作（`dict.get()`、`dict.__setitem__()` 是原子的），但**复合操作**（check-then-set）不安全：

```
线程 A: existing = GLOBAL_HASH_INDEX.get(tk)  # None
线程 B: GLOBAL_HASH_INDEX[tk] = {...}          # 插入
线程 A: GLOBAL_HASH_INDEX[tk] = {...}          # 覆盖 B
```

**实际风险**：🔵 低。用户同一秒上传两个相同视频（相同 task_key）的概率极低。但作为防御性编程应该修。

**修复方案**：加 `threading.Lock()`：

```python
_HASH_LOCK = threading.Lock()
```

需要加锁的位置（6 处）：

| 行号 | 操作 | 说明 |
|------|------|------|
| L237 | `GLOBAL_HASH_INDEX[tk] = ...` | startup 构建 |
| L580-582 | get + set | status 更新 |
| L600-602 | pop | cleanup |
| L707 | get | lookup |
| L752-753 | set (两个 dict) | insert |
| L775 | 遍历 | list all jobs |

**改动量**：~20 行改 1 文件（main.py）
**风险**：低

---

## 总结

| Bug | 根因 | 方案状态 | 改动量 | 风险 |
|-----|------|----------|--------|------|
| **P250** | `len(text)` 数字符，英文被高估 5-6x | ✅ 定稿可实施 | ~50 行 / 1 文件 | 低 |
| **P251** | 历史遗留映射函数截流 Qwen 输出 | ⚠️ 方向正确，影响面比预想大 | ~120 行 / 4 文件 | 中 |
| **P225+P242** | `result[0]` 取法在 MLX 分支之前 | ✅ 新方案 | ~10 行 / 1 文件 | 低 |
| **P181** | localStorage 恢复后 activeJobs 存在但无连接 | ✅ 新方案 | ~3 行 / 1 文件 | 低 |
| **P245** | dict 复合操作无锁保护 | ✅ 新方案 | ~20 行 / 1 文件 | 低 |
| **P253** | 分块用上下文上限而非疲劳区间(30%) | ✅ 定稿可实施 | ~5 行 / 1 文件 | 低 |
