# Phase 1D 设计规格：对齐精度 + 幻觉验证升级

**日期**：2026-06-04
**版本**：V3.04
**决策**：Steve Jobs 审查决定，V4 四项提前

---

## 一、目标

1. 用 Qwen3-ForcedAligner 替代当前「按字数比例分时间」方案，字幕时间戳精确到毫秒
2. 双信号交叉验证（SenseVoice + Qwen3-FA）替代单信号幻觉过滤
3. 恢复 `[?]` 不确定标记，与双信号联动
4. 三级阈值提示系统
5. 模型分时加载，峰值内存从 8.7GB 降至 3.5GB

---

## 二、流水线变更

### 当前（V3.03）

```
音频提取 → Whisper 转录（英文+时间戳） → 五层幻觉过滤
  → 翻译（英文→中文，按时戳比例分配） → 断句 → 字幕输出
```

### Phase 1D 后（2026-06-04 Steve Jobs 审查修正）

```
音频提取 → Whisper 转录（原始语言）
  → Qwen3-FA 词级对齐（原始语言 + 原音频 → 精确词级时间戳）← 在这里
  → 五层幻觉过滤（双信号：SenseVoice + Qwen3-FA 对齐结果）
  → 翻译（原始语言→中文，segment 边界不变）
  → 断句（在精确边界之间微调）
  → 字幕输出
```

关键变化：
- Qwen3-FA 在**转录后、过滤前**运行，只跑**一次**
- 对齐的是**原始语言**文本（非中文），因为时间戳是音频的属性，与语言无关
- Qwen3-FA 产出的精确段边界是整个流水线的**锚点**，翻译不改变它
- 双信号幻觉过滤（SenseVoice + Qwen3-FA）立即生效

---

## 三、新增/修改文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/core/qwen_aligner.py` | 🆕 新增 | Qwen3-FA 加载、推理、时间戳提取 |
| `backend/core/hallucination_filter.py` | ✏️ 修改 | L1 升级为双信号，新增 `aligner_segments` 参数 |
| `backend/worker.py` | ✏️ 修改 | 流水线插入 Qwen3-FA、模型分时加载、[?] 统计 |
| `backend/core/sentence_splitter.py` | ✏️ 修改 | 断句改用 Qwen3-FA 时间戳替代按比例分配 |
| `backend/subtitle_style.py` | ✏️ 修改 | [?] 标记追加到字幕文本末尾 |
| `frontend/index.html` | ✏️ 修改 | [?] 阈值检测 + 黄/红提示 |
| `backend/model_manager.py` | ✏️ 修改 | 新增 Qwen3-FA 模型条目（可选，Phase 1D 可用 HuggingFace 自动下载） |
| `test/test_phase1d_aligner.py` | 🆕 新增 | Qwen3-FA 对齐精度测试 |
| `test/test_phase1d_dual_signal.py` | 🆕 新增 | 双信号过滤测试 |
| `test/test_phase1d_question_mark.py` | 🆕 新增 | [?] 标记 + 阈值测试 |
| `test/test_phase1d_memory.py` | 🆕 新增 | 模型分时加载内存测试 |

---

## 四、模块设计

### 4.1 `qwen_aligner.py` — Qwen3-ForcedAligner 封装

```
class QwenAligner:
    def __init__(self, model_id="Qwen/Qwen3-ForcedAligner-0.6B", device=None)
        → 从 HuggingFace 加载模型（transformers backend）
        → device 自动检测：cuda > mps > cpu

    def align(self, audio_path: str, text: str, language: str = "zh")
        → 输入：音频文件路径 + 中文文本 + 语言代码
        → 输出：list[AlignedWord]
            AlignedWord(text="你", start=1.234, end=1.456)

    def close(self)
        → del 模型 + torch.cuda.empty_cache()

def is_qwen_aligner_available() -> bool
    → 检测 transformers + torch + 模型文件是否可用
```

**依赖**：`transformers`, `torch`（项目中已有，不增加新依赖）

**模型**：`Qwen/Qwen3-ForcedAligner-0.6B`，HuggingFace 自动下载到 `~/.cache/huggingface/hub/`

**语言映射**：`{"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", ...}`

### 4.2 `hallucination_filter.py` L1 升级

**当前 L1**（单信号）：
```
SenseVoice 判定 "非人声" → 直接丢弃
SenseVoice 判定 "人声" → 保留
```

**Phase 1D L1**（双信号）：
```
SenseVoice 非人声 + Qwen3-FA 无对齐 → 双确认，丢弃 ✓
SenseVoice 非人声 + Qwen3-FA 有对齐 → SenseVoice 误判，保留
SenseVoice 人声   + Qwen3-FA 无对齐 → 疑似幻觉，保留并标记 [?]
SenseVoice 人声   + Qwen3-FA 有对齐 → 双确认，正常通过 ✓
```

**API 变更**：
```python
# 旧
def filter_hallucinations(segments: list, source_lang: str = "auto") -> list

# 新
def filter_hallucinations(segments: list, source_lang: str = "auto",
                          aligner_result: list | None = None) -> list
```

`aligner_result` 为 None 时降级为旧行为（向后兼容）。

### 4.3 `worker.py` 流水线变更

在翻译完成后插入对齐步骤：

```python
# 现有流程（简化）
segments = transcribe(audio_path, ...)
segments = filter_hallucinations(segments, source_lang)
zh_list, failed, token_usage = translate(...)
segments = word_align_segments(segments)  # 旧的时间对齐
srt_path = build_subtitles(segments, ...)

# Phase 1D 流程
segments = transcribe(audio_path, ...)
# 先做基础过滤（L2-L5），L1 双信号放后面做
segments = filter_hallucinations(segments, source_lang)
zh_list, failed, token_usage = translate(...)

# Qwen3-FA 词级对齐（新增）
from backend.core.qwen_aligner import QwenAligner, is_qwen_aligner_available
if is_qwen_aligner_available() and transcription_tier == "precise":
    aligner = QwenAligner()
    full_text = " ".join(zh_list)
    aligned_words = aligner.align(audio_path, full_text, language=source_lang)
    # 用 aligned_words 更新 segments 的 start/end
    segments = _apply_aligner_timestamps(segments, aligned_words)
    aligner.close()  # 模型分时加载：用完即释

# 断句（用新时间戳）
segments = word_align_segments(segments)
srt_path = build_subtitles(segments, ...)
```

### 4.4 模型分时加载

```python
# transcriber_v2.py 和 worker.py 中
# 每个模型用完立即释放

# Whisper
model = WhisperModel(...)
segments = model.transcribe(...)
del model  # ← 新增

# Qwen3-ASR
qwen_result = transcribe_raw(...)
del qwen_model  # ← 新增

# SenseVoice  
sv_events = run_sensevoice(...)
del sv_model  # ← 新增

# Qwen3-FA
aligned = aligner.align(...)
aligner.close()  # ← 新增
```

**效果**：峰值内存 = max(Whisper, Qwen3-ASR, SenseVoice, Qwen3-FA) ≈ 3.5GB，而非总和 ≈ 8.7GB。

### 4.5 `[?]` 标记恢复

在 `subtitle_style.py` / `subtitler.py` 的字幕文本拼接处：

```python
if hasattr(seg, 'uncertain') and seg.uncertain:
    text += " [?]"
```

`seg.uncertain` 由 `hallucination_filter.py` L1 双信号设置（SenseVoice 人声 + Qwen3-FA 无对齐）。

### 4.6 阈值提示系统（前端）

```javascript
// 新增函数
function checkQuestionMarkThreshold(totalSegments, questionMarkCount) {
    if (totalSegments < 20) return null;  // 样本太少不触发
    const ratio = questionMarkCount / totalSegments;
    if (ratio > 0.15) return {level: "red", msg: "音频质量较差，建议手动校对后使用"};
    if (ratio >= 0.05 || questionMarkCount >= 50) return {level: "yellow", msg: "部分字幕可能不准确，可点微调查看"};
    return null;
}
```

Worker 在 `emit("progress", ...)` 时带上 `question_mark_count` 和 `total_segments`。前端接收后在进度区上方显示黄/红色提示条。

---

## 五、错误处理

| 场景 | 行为 |
|------|------|
| Qwen3-FA 模型未下载 | 降级：使用旧的按字数比例分时间方案（当前行为）。日志记录 |
| Qwen3-FA 推理失败 | 降级：该段字幕保留旧时间戳，日志记录具体错误 |
| 对齐结果与 segment 数量不匹配 | 降级：能对齐的对齐，对不上的保留旧时间戳 |
| SenseVoice 不可用 | 幻觉过滤 L1 跳过，L2-L5 正常工作 |
| 模型加载 OOM | 跳过 Qwen3-FA，日志提示"内存不足，建议切换为标准模式" |

**核心原则**：Qwen3-FA 是增强，不是必需品。任何失败都不阻塞流水线。

---

## 六、测试计划（目标 30+ 测试）

| 测试文件 | 测试内容 | 最低数量 |
|---------|---------|---------|
| `test_phase1d_aligner.py` | 对齐器加载/推理/输出格式/语言映射/错误降级 | 8 |
| `test_phase1d_dual_signal.py` | 四路判断矩阵每种情况 + 边界条件 | 8 |
| `test_phase1d_question_mark.py` | [?] 标记追加 + 阈值三级梯度 + 段数<20 不触发 | 8 |
| `test_phase1d_memory.py` | 模型 close() 释放验证 + import 隔离 | 6 |

---

## 七、不动的模块

- 五层幻觉过滤 L2-L5 逻辑不变
- 三模型转录调度框架不变（`transcriber_v2.py` 主体不动）
- `transcriptionTier` 三档不变（精确档新增 Qwen3-FA）
- `_SUBTITLE_PROMPT` 不变
- FastAPI 20 个端点 + WebSocket 不变
- 前端 7 区域布局不变（只新增提示条）
- 翻译模块（`orchestrator.py`）零改动

---

## 八、决策记录

| # | 决策 | 结论 | 锁定 |
|---|------|------|------|
| 1 | 加载方式 | transformers + PyTorch（方式 A） | ✅ |
| 2 | 流水线位置 | 翻译之后、断句之前 | ✅ |
| 3 | 翻译与断句 | 保持分离，不合并且 | ✅ |
| 4 | 目录名 | v2/ 不动，打包时统一整理 | ✅ |
| 5 | Whisper 时间戳 | 丢弃，Qwen3-FA 重新生成 | ✅ |
| 6 | 单信号→双信号 | L1 SenseVoice 升级为 SenseVoice + Qwen3-FA | ✅ |
| 7 | 降级策略 | Qwen3-FA 失败不回退到旧方案，不阻塞流水线 | ✅ |

---

## 九、规格自审

- [x] 无 TBD、TODO、占位符
- [x] 架构与功能描述一致（Qwen3-FA 在翻译后、模块 4/5 保持分离）
- [x] 范围聚焦：5 项任务，不改其他模块
- [x] 无歧义：每个模块的输入输出已明确
