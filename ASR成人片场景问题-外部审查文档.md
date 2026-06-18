# ASR 分支：日语成人片测试问题复盘与修复方案 — 外部审查文档

**日期**：2026-06-14  
**分支**：ASR  
**项目**：video-subtitle  
**请求**：请审查本方案是否能解决 FJIN-141 日语成人片两次测试暴露的问题，尤其是 L3/L4 对叫声、喘息、碰撞声的处理策略。

---

## 一、给审查人的一句话摘要

这次问题不是 Qwen3-ASR 没有转出来。Qwen 两次都稳定输出 `25 chunks / 5132 segments`。

真正的问题是：系统里还有多处隐性依赖 Whisper。第一次用 `large-v3` 时 Whisper 大量数字幻觉；第二次改成 `tiny` 后 Whisper 只输出 3 段。这个选择本意是降低 Whisper 影响，但系统仍把 Whisper 当成 L1、SenseVoice、L2、Refiner 的重要支撑，导致多个阶段连锁失效。

修复原则：**Qwen 负责主转录和时间戳，SenseVoice 负责声音类型，Whisper 只在健康时做参考。L3/L4 不再负责判断“叫声值不值得翻译”，这件事新增一层“对白价值判断”来做。**

---

## 二、项目背景

### 这个项目做什么

这是一个视频字幕自动翻译工具。用户上传外语视频，系统自动完成：

1. **转录**：把语音识别成原语言字幕。
2. **过滤**：删除或标记幻觉字幕、非人声字幕、重复字幕。
3. **校准**：用 LLM 在翻译前修正原文字幕。
4. **翻译**：把原语言字幕翻译成中文。
5. **输出**：生成字幕或烧录视频。

### 当前 ASR 架构

当前精确模式会同时使用这些模型：

| 模型 | 当前职责 | 理想职责 |
|------|----------|----------|
| Qwen3-ASR | 日/中/英/韩主转录，输出词级时间戳 | 主转录 + 时间戳主证据 |
| Whisper | 参考转录，过去也常被当作句子骨架 | 健康时做参考，不健康时降级 |
| SenseVoice | 判断声音类型，如人声、音乐、呼吸 | 全片声音事件检测，不依赖 Whisper 段数 |
| LLM Refiner | 当前只做 Qwen vs Whisper 冲突裁判 | 双稿裁判 + 单稿疏通 |

术语说明：

- **词级时间戳**：每个词都有开始和结束时间。比如 `あ` 在 12.1-12.3 秒。
- **句级字幕**：最终字幕需要一句话一条，有开始和结束时间。
- **组装**：把 Qwen 的词级结果拼成一句一句字幕。
- **L1/L2/L3/L4/L5**：五层过滤。L1 看声音和时间戳，L2 看两个模型文字是否一致，L3 看时长/语速是否物理上可能，L4 看重复和黑名单，L5 做去重。
- **Refiner**：翻译前的 LLM 校对器。理想上用于修正转录原文。

---

## 三、起因：为什么做这两次测试

之前项目主要测过英语和俄语，没有系统测日语成人片。

这次选择 FJIN-141，是为了测试日语场景，尤其是成人片里大量非标准声音：

- 真实对白；
- 叫声、喘息、短促语气声；
- 碰撞声、摩擦声；
- 背景音乐或环境声；
- 噪声触发 ASR 幻觉。

### 第一次测试

第一次使用 Whisper `large-v3`。用户的想法是：用大模型做交叉验证，看看日语视频里 Whisper 能不能辅助 Qwen。

结果发现：Whisper `large-v3` 对这个视频大量输出数字幻觉，比如连续数字、乘法表式内容。它对日语成人片这类音频不可靠。

### 第二次测试

第二次改用 Whisper `tiny`。用户的想法是：既然 Whisper 整体不可靠，就选最小模型，让它尽量不影响主结果；Qwen 才是主引擎。

但是这里暴露出一个隐藏问题：Whisper 不只是“文字参考”。它还影响了：

- SenseVoice 的分段；
- L1 的工作对象；
- L2 的文本对比；
- Refiner 是否能看到参考稿；
- 组装策略是否能拿到 `whisper_text`。

所以第二次虽然消除了数字污染，但又出现段数下降、长空白、Refiner 不校准、L1 几乎失效等问题。

---

## 四、两次测试关键数据

测试视频：FJIN-141，日语，约 7431 秒。

| 项目 | 第一次 | 第二次 |
|------|--------|--------|
| Whisper 模型 | large-v3 | tiny |
| Qwen 输出 | 25 chunks / 5132 segments | 25 chunks / 5132 segments |
| Whisper 输出 | 3375 段 | 3 段 |
| SenseVoice 覆盖 | 25 chunks / 3375 segments | 2 chunks / 3 segments |
| 最终 transcript | 2249 段 | 944 段 |
| `whisper_text` | 全部有值 | 944 段全部为空 |
| `uncertain=True` | 2088 / 2249 | 883 / 944 |
| P247 Refiner | corrections=0 | corrections=0 |
| 翻译成功 | 983 / 2249 | 944 / 944 |
| 纯数字源文本 | 1418 段 | 0 段 |
| 大于 30 秒空白 | 11 个 | 32 个 |

结论：

1. Qwen 转录稳定。
2. Whisper 在这个日语成人片场景不可靠。
3. 第二次翻译本身是成功的，但输入字幕已经缺段、碎段、标记异常。
4. L1、L2、Refiner、组装都暴露出对 Whisper 的隐性依赖。

---

## 五、发现过程

### 第一步：先看 Qwen 是否崩了

两次 Qwen 都输出 `25 chunks / 5132 segments`。这说明主转录没有整体崩掉。

因此不能把问题归结为“Qwen 听不懂日语成人片”。

### 第二步：对比 Whisper 输出

第一次 `large-v3` 输出 3375 段，但大量是数字幻觉。  
第二次 `tiny` 只输出 3 段。

这说明 Whisper 在两次测试中都不能作为可靠文本来源。

### 第三步：发现 SenseVoice 和 L1 被 Whisper 段数拖住

当前代码里，Qwen 主引擎时仍有这段逻辑：

```python
# v2/backend/transcriber_v2.py
_sv_target = _whisper_segments if _whisper_segments else results
```

也就是说 SenseVoice 不是直接按 Qwen 时间线或固定窗口扫全片，而是跟着 Whisper 段跑。

第二次 Whisper 只有 3 段，所以 SenseVoice 也只处理了 2 个 chunk / 3 个 segments。L1 自然几乎失效。

### 第四步：发现 L1 的工作对象错了

当前代码实际逻辑：

```python
_l1_align = _qwen_segments_as_alignment(qwen_chunks)
for _ws in _whisper_segments:
    if not getattr(_ws, "filtered", False):
        _layer1_dual_signal(_ws, _l1_align, detected_lang)
```

这说明 Qwen 时间戳只是证据，但 L1 遍历的是 Whisper 段。

理想设计应该是：L1 直接遍历 Qwen 段或 Qwen 组装前的时间片，用 SenseVoice 标签和 Qwen 时间戳验证。

### 第五步：发现 Refiner 不是单稿校准器

当前 Refiner prompt 构造逻辑：

```python
_qw = seg.text
_wh = getattr(seg, "whisper_text", "")
if not _wh or _normalize_text(_qw) == _normalize_text(_wh):
    _prompt_lines.append(f"段落 {seg.id}: {_qw}")
else:
    _prompt_lines.append(f"\n[CONFLICT seg_id: {seg.id}]")
    _prompt_lines.append(f"- Qwen (主): {_qw}")
    _prompt_lines.append(f"- Whisper (参考): {_wh}")
```

系统提示又写：

```text
只有当 Qwen 明显听错、而 Whisper 听对时，才进行修正。
绝对不要抄写或输出没有任何冲突的上下文段落。
```

所以第二次 `whisper_text` 全空时，Refiner 看不到任何冲突。它不会对 Qwen 单稿做句意疏通，只会返回：

```json
{"corrections": []}
```

这就是 P269。

### 第六步：发现 L3/L4 对成人片声音没有专门策略

日志里大量出现：

- L3 不可能时长；
- L3 语速异常；
- L4 重复模式；
- L4 重复词。

成人片里常见的 `ああ`、`んん`、`はぁ`、`うん` 这类短促声音，本身可能是真实声音，但不一定有翻译价值。

当前 L3/L4 只能粗暴判断“短、快、重复”。它们不知道这是：

1. 真实对白；
2. 叫声或喘息；
3. 碰撞声触发的 ASR 幻觉；
4. 模型数字幻觉；
5. 组装碎片。

所以 L3/L4 被迫做它们不该做的语义判断。

---

## 六、问题清单

### P268：L1 承载段错误

**问题**：L1 应固定使用 `SenseVoice × Qwen 时间戳`，但当前仍遍历 Whisper 段。

**影响**：Whisper tiny 只有 3 段时，SenseVoice 和 L1 几乎失效。

### P269：Refiner 缺少单稿疏通模式

**问题**：Refiner 当前只做 Qwen vs Whisper 冲突裁判。Whisper 参考稿缺失时，它不会对 Qwen 单稿做句意疏通。

**影响**：碎句、不完整句、明显语义不通的 Qwen 单稿直接进入翻译。

### 组装覆盖率问题

**问题**：Qwen 原始词很多，但组装后部分时间段字幕密度大幅下降，空白间隙变多。

**影响**：用户看到角色在说话，但字幕长时间不出现。

### L2 降级问题

**问题**：L2 需要两个健康文本稿才能比较。Whisper 全是数字或只有 3 段时，L2 还继续比较，导致大量 `uncertain`。

**影响**：用户看到大量不确定标记，系统自己也无法区分“真的可疑”和“参考稿坏了”。

### L3/L4 成人片声音策略缺失

**问题**：L3/L4 不知道“叫声、喘息、重复短音”是否应该保留。

**影响**：可能误删真实短声，也可能保留大量无意义字幕。

---

## 七、总解决方案

新的责任分工：

```text
Qwen 转录 + Qwen 时间戳
  ↓
L1：SenseVoice × Qwen 时间戳，判断是不是人声区域
  ↓
组装：把 Qwen 词拼成字幕句子，并做覆盖率检查
  ↓
L2：只在 Whisper 健康时做双稿文本对比
  ↓
P269 Refiner：有双稿就双稿裁判，没双稿就 Qwen 单稿疏通
  ↓
新增对白价值判断：判断这段是否值得作为字幕
  ↓
L4：重复模式过滤，但要懂叫声和真实重复对白
  ↓
L3：语速/时长过滤，只处理物理上不可能的文本
  ↓
翻译
```

下面逐项说明。

---

## 八、方案一：P268，L1 脱离 Whisper

### 目标

L1 不再因为 Whisper 段数少而失效。无论 Whisper 用 large、tiny，甚至跳过，L1 都要覆盖 Qwen 的时间线。

### 当前错误代码

```python
_sv_target = _whisper_segments if _whisper_segments else results

if filter_keywords and _which_primary == "qwen" and _whisper_segments:
    _l1_align = _qwen_segments_as_alignment(qwen_chunks)
    for _ws in _whisper_segments:
        _layer1_dual_signal(_ws, _l1_align, detected_lang)
```

问题有两个：

1. SenseVoice 输入目标是 Whisper 段；
2. L1 遍历对象也是 Whisper 段。

### 建议实现

新增一个函数，把 Qwen 原始词级结果变成 L1 可处理的 Qwen 时间段：

```python
def _qwen_words_to_l1_segments(qwen_chunks, lang: str) -> list[Segment]:
    words = _flatten_qwen_words(qwen_chunks)
    if not words:
        return []
    return _pause_segment(words, start_id=0, lang=lang, pause_threshold=0.5)
```

SenseVoice 不再吃 Whisper 段，而是按固定窗口扫全片：

```python
def _fixed_audio_windows(total_end: float, window_sec: float = 30.0) -> list[Segment]:
    windows = []
    t = 0.0
    idx = 0
    while t < total_end:
        end = min(t + window_sec, total_end)
        windows.append(Segment(id=idx, text="", start=t, end=end))
        idx += 1
        t = end
    return windows
```

替换逻辑：

```python
qwen_l1_segments = _qwen_words_to_l1_segments(qwen_chunks, detected_lang)
total_end = qwen_l1_segments[-1].end if qwen_l1_segments else results[-1].end
sv_target = _fixed_audio_windows(total_end, window_sec=30.0)

sv_events = run_sensevoice(audio_path, device="cpu", segments=sv_target)
_apply_sv_tags_by_time(qwen_l1_segments, sv_events)

qwen_align = _qwen_segments_as_alignment(qwen_chunks)
for seg in qwen_l1_segments:
    _layer1_dual_signal(seg, qwen_align, detected_lang)
```

需要新增按时间贴 SenseVoice 标签：

```python
def _apply_sv_tags_by_time(segments: list[Segment], sv_events: list[dict]) -> None:
    for seg in segments:
        best_tag = None
        best_overlap = 0.0
        for ev in sv_events or []:
            ov = min(seg.end, ev["end"]) - max(seg.start, ev["start"])
            if ov > best_overlap:
                best_overlap = ov
                best_tag = ev.get("tag")
        if best_tag and best_overlap > 0:
            seg.sensevoice_tag = best_tag
```

### 如何解决问题

第二次测试里 Whisper 只有 3 段。旧代码 L1 只处理 3 个对象。  
新代码 L1 处理的是 Qwen 自己的时间线。Qwen 有 5132 个词，L1 就能覆盖全片。

这样碰撞声、音乐、非人声区域不会因为 Whisper 崩掉而失去过滤。

### 测试要求

新增回归测试：

```python
def test_l1_uses_qwen_segments_when_whisper_has_only_three_segments():
    qwen_chunks = fake_qwen_chunks(word_count=5000, duration=7400)
    whisper_segments = fake_whisper_segments(count=3, duration=7400)

    l1_segments = _qwen_words_to_l1_segments(qwen_chunks, "ja")

    assert len(l1_segments) > 100
```

---

## 九、方案二：组装覆盖率护栏

### 目标

防止 Qwen 原始词很多，但组装后字幕大面积消失。

### 当前问题

第二次测试中，25 分钟后多个区间字幕密度降到第一次的 14%-33%。但 Qwen 原始输出在这些区间并不少。

这说明问题不是 Qwen 没识别，而是组装后丢了覆盖。

### 建议实现

组装后做每 5 分钟窗口的覆盖检查：

```python
def _coverage_report(qwen_words, assembled_segments, window_sec=300.0):
    total_end = max(
        qwen_words[-1]["end"] if qwen_words else 0.0,
        assembled_segments[-1].end if assembled_segments else 0.0,
    )
    reports = []
    t = 0.0
    while t < total_end:
        end = t + window_sec
        qw = [w for w in qwen_words if t <= w["start"] < end]
        segs = [s for s in assembled_segments if s.end > t and s.start < end]
        covered = sum(max(0.0, min(s.end, end) - max(s.start, t)) for s in segs)
        reports.append({
            "start": t,
            "end": end,
            "qwen_words": len(qw),
            "segments": len(segs),
            "covered_seconds": covered,
        })
        t = end
    return reports
```

发现异常时触发保底组装：

```python
def _needs_assembly_fallback(report):
    return (
        report["qwen_words"] >= 80
        and report["segments"] < 10
    )
```

保底组装规则：

```python
def _fallback_assemble_by_budget(words, start_id, lang):
    joiner = "" if lang in ("ja", "zh", "ko") else " "
    segments = []
    buf = []
    for w in words:
        buf.append(w)
        text = joiner.join(x["text"] for x in buf)
        span = buf[-1]["end"] - buf[0]["start"]
        if span >= 5.0 or len(text) >= 30:
            segments.append(_words_to_segment(buf, start_id + len(segments), joiner))
            buf = []
    if buf:
        segments.append(_words_to_segment(buf, start_id + len(segments), joiner))
    return segments
```

### 如何解决问题

这个方案不追求句子最漂亮，先保证字幕不大面积消失。

如果某个 5 分钟窗口里 Qwen 原始词很多，但组装段极少，说明停顿切句或时间戳质量不可靠。此时用保底规则按时间和字符数切段，至少让用户看到内容。

### 测试要求

```python
def test_assembly_fallback_when_qwen_words_many_but_segments_few():
    report = {"qwen_words": 150, "segments": 4}
    assert _needs_assembly_fallback(report)
```

---

## 十、方案三：P269，Refiner 加 dual/single 模式

### 目标

Whisper 健康时，Refiner 做双稿裁判。  
Whisper 崩坏或缺失时，Refiner 对 Qwen 单稿做有限语义疏通。

### 当前错误逻辑

```python
if not _wh or _normalize_text(_qw) == _normalize_text(_wh):
    _prompt_lines.append(f"段落 {seg.id}: {_qw}")
else:
    _prompt_lines.append(f"\n[CONFLICT seg_id: {seg.id}]")
```

没有 `whisper_text` 时不会进入校准，只当普通上下文。

### 建议实现

先判断 Refiner 模式：

```python
def _refiner_mode(segments: list[Segment]) -> str:
    if not segments:
        return "skip"
    with_whisper = sum(1 for s in segments if getattr(s, "whisper_text", ""))
    coverage = with_whisper / len(segments)
    if coverage >= 0.30:
        return "dual"
    return "single"
```

dual 模式保留现有逻辑。

single 模式只挑高风险段：

```python
def _single_refiner_candidates(segments: list[Segment]) -> list[Segment]:
    candidates = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        if getattr(seg, "uncertain", False):
            candidates.append(seg)
            continue
        if len(text) <= 4:
            candidates.append(seg)
            continue
        if getattr(seg, "l3_flag", False) or getattr(seg, "l4_flag", False):
            candidates.append(seg)
            continue
    return candidates
```

single 模式 prompt：

```text
你是字幕原文校对员。下面只有 Qwen 单稿，没有第二份参考稿。
只处理候选段，不要改没有列出的段。

任务：
1. 如果句子明显碎裂、语序不通，尽量在同一段内疏通。
2. 可以补标点。
3. 不要跨段合并，不要删除 ID，不要改时间戳。
4. 如果只是叫声、喘息、短促语气声，不要扩写成完整对白。
5. 无法确定就不返回 corrections。

输出 JSON：
{"corrections":[{"seg_id":12,"fixed_text":"修正后的原文","reason":"broken_sentence"}]}
```

注意：single 模式未返回的段，不要自动清掉 `uncertain`。

```python
if mode == "dual":
    clear_uncertain_for_uncorrected = True
else:
    clear_uncertain_for_uncorrected = False
```

### 如何解决问题

第二次 FJIN-141 里 944 段全部没有 `whisper_text`。旧 Refiner 只能返回 `corrections=0`。  
新 Refiner 会进入 single 模式，挑出 `uncertain=True` 和明显碎片段，让 LLM 做有限疏通。

例如：

```text
原文：父さんとお母さん今
可能修成：お父さんとお母さんは今
```

如果只是：

```text
ああ
うん
はぁ
```

则不扩写，交给“对白价值判断”决定是否保留。

---

## 十一、方案四：Prompt 分层修改，区分“原文校准”和“中文翻译”

### 目标

这次讨论确认了另一个问题：Refiner 的失败不只是代码模式问题，也是 prompt 任务写窄了。

当前 Refiner prompt 只告诉 LLM：

```text
对比 Qwen 和 Whisper。
默认信任 Qwen。
只有 Qwen 明显错、Whisper 听对时才修。
没有冲突的段不要输出。
```

这不是“句意疏通 prompt”，而是“冲突裁判 prompt”。

它能处理：

```text
Qwen: I have a claw
Whisper: I have a clock
上下文在聊时间 → 修成 clock
```

但它不能处理：

```text
只有 Qwen 单稿，没有 Whisper 参考
Qwen 句子本身碎了
Qwen 某个词听错，但 Whisper 没给正确版本
一句话有多义词，翻译时选错中文意思
日语成人片里的短句、叫声、隐晦表达需要按语境处理
```

### 必须拆成两类 prompt

这里不能让一个 prompt 同时负责“校准原文”和“翻译成中文”。这两件事要分开。

| 层级 | 处理对象 | 解决什么问题 | 不该做什么 |
|------|----------|--------------|------------|
| Refiner 原文校准 prompt | 原语言字幕，比如日语或英语 | 听错词、漏词、碎句、边界错位 | 不翻译成中文，不改剧情 |
| 翻译 prompt | 已校准的原语言字幕 | 一词多义、语气、称呼、成人片场景表达 | 不反向改原文 |

简单说：

```text
Refiner 负责让原文可信。
翻译 prompt 负责让中文自然。
```

### Refiner dual prompt：双稿裁判

Whisper 健康时使用。

建议 prompt 含义如下：

```text
你是字幕原文校对员。你会看到两份同一音频的转录：
主稿是 Qwen，参考稿是 Whisper。

你的任务只是在原语言层面校准字幕，不要翻译成中文。

请只修这些问题：
1. 两份稿子冲突时，根据上下文判断哪个词更合理。
2. 修明显听错的词，比如发音接近但句意不通的词。
3. 修漏词、错词、边界处词跑到上一句或下一句的问题。
4. 可以补必要标点。

禁止：
1. 不要改写剧情。
2. 不要扩写叫声或喘息。
3. 不要把短促声音脑补成完整对白。
4. 不确定就保留主稿。

只输出需要修正的段落。
```

这个 prompt 解决的是 `claw/clock` 这类问题。

### Refiner single prompt：单稿句意疏通

Whisper 崩坏、缺失、全是数字或覆盖率太低时使用。

建议 prompt 含义如下：

```text
你是字幕原文校对员。现在只有 Qwen 单稿，没有第二份参考稿。

你的任务不是翻译，而是在原语言层面做有限校准。

请只处理候选段：
1. 句子明显碎裂；
2. 语序明显不通；
3. 前后文能看出缺了少量连接词；
4. 标点缺失导致断句不清；
5. 明显不符合上下文的误听词。

禁止：
1. 不要凭空增加剧情。
2. 不要跨段合并。
3. 不要删除时间戳或编号。
4. 不要把“ああ、んん、はぁ”这类叫声扩写成完整对白。
5. 不确定就不修。

只输出需要修正的段落。
```

它解决的是：

```text
Today, clock, I
父さんとお母さん今
少しですかうんお
```

但它不解决中文翻译选词。中文翻译选词放到翻译 prompt。

### 翻译 prompt：增加日语成人片场景规则

当前翻译 prompt 已经有“多义词根据场景选最自然表达”的通用规则，但太泛，没有针对这次测试的场景。

建议在翻译 prompt 里增加一个场景规则块：

```text
如果内容是日语成人剧情、亲密场景或强语气对话：

1. 翻译要口语化、自然，符合人物关系和语气。
2. 短句不要机械直译，要按中文自然说法处理。
3. 暧昧表达、命令、拒绝、害羞、喘息，要按上下文翻译。
4. “だめ”这类词不要固定翻成一个中文，要根据语境选“不行 / 不要 / 不可以”。
5. “いや”根据语境可译为“不要 / 不是 / 讨厌 / 不行”，不要死译。
6. 叫声、喘息、短促声音不要过度翻译。
7. 重复叫声可以弱化或省略，真实对白重复要保留。
8. 同一人物称呼要统一。
9. 不要把无意义叫声翻成夸张中文句子。
```

### 如何解决这次翻译不理想

这次翻译不理想可能来自三层：

1. **原文没校准**：Qwen 单稿碎了，Refiner 没有 single prompt，所以碎句直接进翻译。
2. **翻译 prompt 太泛**：没有日语成人片语境规则，导致多义词、语气词、称呼、短句翻得生硬。
3. **叫声没分类**：`ああ / んん / はぁ` 这类直接进入翻译，LLM 会硬翻，画面上就会出现大量不自然中文。

拆分后：

```text
Refiner dual/single → 先把原文校准到可信
对白价值判断 → 决定叫声是否限量显示
翻译 prompt 场景化 → 再把可信原文翻成自然中文
```

### 验收标准

新增检查项：

1. Whisper 覆盖率为 0 时，日志显示 Refiner 使用 single prompt。
2. Refiner single prompt 不会把 `ああ`、`んん` 扩写成完整对白。
3. Refiner single prompt 可以修正明显碎句，但不能跨段合并。
4. 翻译 prompt 对 `だめ`、`いや`、`もっと` 这类多义短词能按上下文选择自然中文。
5. 重复叫声不会每条都硬翻成中文。

---

## 十二、方案五：L2 健康降级

### 目标

L2 只在两个健康稿子都存在时工作。Whisper 崩坏时，L2 不再制造大量假 `uncertain`。

### 建议实现

新增 Whisper 健康判断：

```python
def _whisper_is_usable(segments: list[Segment], total_duration: float) -> bool:
    if not segments or len(segments) < 20:
        return False
    digit_only = sum(1 for s in segments if (s.text or "").strip().isdigit())
    if digit_only / len(segments) > 0.20:
        return False
    covered = sum(max(0.0, s.end - s.start) for s in segments)
    if total_duration > 0 and covered / total_duration < 0.20:
        return False
    return True
```

调用 L2 前：

```python
if not _whisper_is_usable(_whisper_segments, total_duration):
    skip_l2 = True
    _log.warning("L2 跳过：Whisper 不健康，不能作为文本参考")
else:
    skip_l2 = False
```

`filter_hallucinations()` 增加参数：

```python
filter_hallucinations(..., skip_l2=skip_l2)
```

### 如何解决问题

第一次 large-v3 数字幻觉，L2 不再拿数字和 Qwen 日文硬比。  
第二次 tiny 只有 3 段，L2 不再把几乎全片标成 `uncertain`。

这不会提升文本质量，但会避免错误信号污染后续流程。

---

## 十三、方案六：新增“对白价值判断”，解决 L3/L4 成人片策略

### 为什么必须新增这一层

L3 只知道时长和语速。  
L4 只知道重复和黑名单。  
它们都不知道“这句声音是否值得作为字幕”。

成人片里的声音有四类：

| 类型 | 例子 | 应该怎么处理 |
|------|------|--------------|
| 真实对白 | “やめて”、“気持ちいい”、“何してるの” | 保留并翻译 |
| 叫声/喘息 | “ああ”、“んん”、“はぁ” | 默认弱化或限量保留 |
| 碰撞/摩擦声幻觉 | Qwen 从非人声里听出短词 | 删除或标疑 |
| 模型坏掉 | 数字串、长重复、乱码 | 删除 |

如果没有“对白价值判断”，L3/L4 只能粗暴处理，必然误杀或漏放。

### 建议新增字段

给 `Segment` 动态加字段即可，不需要改数据类定义：

```python
seg.dialogue_kind = "dialogue"          # 正常对白
seg.dialogue_kind = "vocalization"      # 叫声/喘息/短促语气声
seg.dialogue_kind = "noise_hallucination" # 非人声触发幻觉
seg.dialogue_kind = "broken_text"       # 碎句，交给 Refiner
```

### 建议实现函数

```python
VOCALIZATION_RE = re.compile(r"^(あ+|ぁ+|ん+|うん+|はぁ+|はあ+|いや+|だめ+|ah+|uh+|mm+)$", re.I)

def _classify_dialogue_value(seg: Segment, lang: str) -> str:
    text = (seg.text or "").strip()
    tag = getattr(seg, "sensevoice_tag", None)

    if not text:
        return "noise_hallucination"

    # 纯数字或长数字串，成人片日语场景下大概率是 Whisper/ASR 幻觉
    if text.isdigit() and len(text) >= 3:
        return "noise_hallucination"

    # 叫声、喘息、短促语气声
    compact = re.sub(r"\s+", "", text)
    if VOCALIZATION_RE.match(compact):
        return "vocalization"

    # SenseVoice 明确非人声，且文本很短，倾向认为是噪声幻觉
    if tag in ("<|BGM|>", "<|Music|>", "<|Noise|>") and len(compact) <= 4:
        return "noise_hallucination"

    # 很短但不是常见叫声，交给 Refiner 或保留
    if len(compact) <= 4:
        return "broken_text"

    return "dialogue"
```

### 如何接入

在 L1 之后、L4/L3 之前运行：

```python
for seg in results:
    seg.dialogue_kind = _classify_dialogue_value(seg, detected_lang)
```

---

## 十四、方案七：L4 重复策略改造

### 当前问题

L4 看到重复就容易删。但成人片里有三种重复：

1. 模型坏掉的重复；
2. 叫声重复；
3. 真实对白重复。

它们不能同一个规则处理。

### 建议逻辑

```python
def _layer4_regex(seg: Segment, lang: str) -> bool:
    kind = getattr(seg, "dialogue_kind", "dialogue")
    text = (seg.text or "").strip()

    if kind == "noise_hallucination":
        seg.filtered = True
        seg.filter_reason = "L4_noise_hallucination"
        return True

    if kind == "vocalization":
        return _handle_vocalization(seg)

    if _is_structural_garbage(text):
        seg.filtered = True
        seg.filter_reason = "L4_structural_garbage"
        return True

    if _is_real_repeated_dialogue(text, lang):
        return False

    if _is_repetition_pattern(text):
        seg.uncertain = True
        seg.filter_reason = "L4_repetition_uncertain"
        return False

    return False
```

叫声限量保留：

```python
def _handle_vocalization(seg: Segment) -> bool:
    # 默认不硬删，先打标签，最终由输出配置决定是否显示
    seg.subtitle_policy = "suppress_or_limit"
    seg.uncertain = True
    return False
```

真实重复对白判断：

```python
def _is_real_repeated_dialogue(text: str, lang: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    keep_phrases = (
        "やめて", "だめ", "もっと", "気持ちいい", "お願い",
        "不要", "不行", "继续", "停下",
    )
    return any(p in compact for p in keep_phrases)
```

结构性垃圾判断：

```python
def _is_structural_garbage(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 8 and len(set(compact)) <= 2:
        return True
    if compact.isdigit() and len(compact) >= 3:
        return True
    return False
```

### 如何解决问题

`ああ`、`んん` 不再被当作“模型坏掉”直接删。  
`やめてやめて` 不再因为重复被删。  
长数字串、长乱码、单字符超长重复仍会被删。

也就是说，L4 从“看到重复就打”变成“先分类，再处理”。

---

## 十五、方案八：L3 只处理物理不可能

### 当前问题

L3 看到 0.2 秒的短声、很快的短词，容易打异常。但成人片里短促声音很多，不都应该删。

### 新原则

L3 只处理物理上不可能的文本，不负责判断有没有翻译价值。

### 建议逻辑

```python
def _layer3_duration_speed(seg: Segment, lang: str) -> bool:
    kind = getattr(seg, "dialogue_kind", "dialogue")
    text = (seg.text or "").strip()
    duration = max(0.0, seg.end - seg.start)
    units = _count_units(text, lang)

    # 叫声/喘息不由 L3 删除，交给对白价值策略限量显示
    if kind == "vocalization":
        return False

    # 0 秒却有多个字符，物理不可能
    if duration <= 0.05 and units >= 2:
        seg.filtered = True
        seg.filter_reason = "L3_impossible_zero_duration"
        return True

    # 极短时间内有大量字符，物理不可能
    if duration < 0.30 and units >= 8:
        seg.filtered = True
        seg.filter_reason = "L3_impossible_burst"
        return True

    # 正常长句才计算语速
    if units >= 8:
        rate = units / max(duration, 0.01)
        threshold = _speed_threshold(lang)
        if rate > threshold:
            seg.uncertain = True
            seg.filter_reason = "L3_speed_uncertain"

    return False
```

### 如何解决问题

短促叫声不再被 L3 大量误杀。  
真正离谱的 ASR 幻觉仍会被删，例如 0 秒里出现一长串日文。

L3 的职责变窄：只管“物理上能不能说完”。

---

## 十六、建议改动文件

| 文件 | 改什么 |
|------|--------|
| `v2/backend/transcriber_v2.py` | P268：Qwen L1 segments、固定窗口 SenseVoice、组装覆盖率护栏 |
| `v2/backend/core/hallucination_filter.py` | L2 skip 参数、对白价值判断、L3/L4 改造 |
| `v2/backend/worker.py` | P269：Refiner dual/single 模式、日志、dual/single prompt |
| `v2/backend/translation/orchestrator.py` | 翻译 prompt 增加日语成人片场景规则、多义词和叫声处理规则 |
| `v2/test/...` | 新增 P268/P269/L3/L4 回归测试 |

---

## 十七、建议实施顺序

### 第 1 步：P268

先修 L1 工作对象。因为如果 L1 仍被 Whisper 拖住，后面 L3/L4 会继续收到脏数据。

### 第 2 步：组装覆盖率护栏

保证 Qwen 原始词不会在组装时大面积消失。

### 第 3 步：L2 健康降级

Whisper 不健康时，不再做错误对比。

### 第 4 步：P269 Refiner dual/single + prompt 分层

让没有 Whisper 参考稿时也能校准 Qwen 单稿。同时把 Refiner prompt 分成双稿裁判和单稿疏通两类。

### 第 5 步：翻译 prompt 场景化

给日语成人片、亲密场景、强语气短句增加翻译规则，解决多义词和语气词翻译生硬的问题。

### 第 6 步：对白价值判断 + L3/L4 改造

专门解决成人片叫声、喘息、碰撞声、重复短音问题。

---

## 十八、验收标准

### 单元测试

至少新增：

1. Whisper 只有 3 段时，L1 仍生成大量 Qwen-side L1 segments。
2. Whisper 数字幻觉时，L2 自动跳过。
3. `whisper_text` 覆盖率为 0 时，Refiner 进入 single 模式。
4. `ああ`、`んん` 被分类为 `vocalization`。
5. `やめてやめて` 被保留为真实对白。
6. 0 秒长句被 L3 删除。
7. 短促叫声不被 L3 删除。
8. Refiner single prompt 不扩写叫声，不跨段合并。
9. 翻译 prompt 能按上下文处理 `だめ`、`いや`、`もっと` 等多义短词。

### 端到端测试

重跑 FJIN-141：

| 指标 | 期望 |
|------|------|
| Qwen 原始输出 | 仍为 25 chunks / 5132 segments 左右 |
| SenseVoice 覆盖 | 不再随 Whisper tiny 降到 2 chunks / 3 segments |
| L1 日志 | 不再只处理 3 个 Whisper 段 |
| L2 | Whisper 不健康时明确日志 `L2 skipped` |
| Refiner | 明确日志 `mode=single` 或 `mode=dual` |
| 大于 30 秒空白 | 明显少于第二次的 32 个 |
| 字幕段数 | 不应从 2249 直接塌到 944，具体值以覆盖率为准 |
| 成人片短声 | 不再满屏翻译“啊啊啊”，也不误删真实对白 |

---

## 十九、审查人重点看什么

请重点审查：

1. P268 是否真的让 L1 脱离 Whisper，而不是换一种方式继续依赖 Whisper。
2. 组装覆盖率护栏是否会引入过多碎字幕。
3. Refiner single 模式是否会让 LLM 过度改写。
4. Refiner prompt 是否明确禁止翻译、扩写叫声和跨段合并。
5. 翻译 prompt 场景化是否足够，但不会让翻译过度露骨或乱改意思。
6. L3/L4 新策略是否会误删真实对白。
7. “对白价值判断”是否应该放在 L3/L4 前，还是应并入 L4。
8. 这些改动是否应该分成多个小 PR，而不是一次大改。

---

## 二十、最终判断

这次 FJIN-141 的问题不是单点 bug，而是一次真实视频把架构里的旧依赖暴露出来了。

最重要的架构调整是：

```text
Whisper 从“隐性骨架”降级为“健康时可用的参考稿”。
Qwen 时间戳成为主时间线。
SenseVoice 全片扫声音，不再跟 Whisper 段数走。
Refiner 有双稿就裁判，没双稿就单稿疏通。
翻译 prompt 按日语成人片/亲密场景做自然中文表达。
L3/L4 不做人类语义判断；成人片声音交给“对白价值判断”。
```

如果只修 P268，不修 P269 和 L3/L4，字幕仍可能出现碎句和大量叫声误判。  
如果只修 L3/L4，不修 P268，L1 仍会被 Whisper tiny 拖垮。  
所以建议按第十六节的顺序分步实现。
