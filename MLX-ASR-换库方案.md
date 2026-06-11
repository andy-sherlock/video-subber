# MLX ASR 库更换方案 — 完整讨论记录

**日期**：2026-06-11
**状态**：⏸ 方案定稿，代码尚未执行
**关联**：P251（P251 换库后可进一步简化归窗逻辑）

---

## 一、换库动机

当前 Mac 端使用的千问 ASR 库是 `qwen3-asr-mlx==0.1.0`（作者 gabrimatic，个人项目），存在以下核心缺陷：

- **没有时间戳功能**：`model.transcribe()` 只返回 `{text, language, duration}`，不返回词级时间戳。
- **没有量化支持**：只支持 fp16，无法通过 8-bit/4-bit 加速。
- **没有对齐器**：FA（强制对齐）必须另外通过 PyTorch 的 `qwen-asr` 包加载 → 引入版本冲突风险（FA 和 Windows 端 ASR 共用 `qwen-asr`，对版本要求不同）。
- **项目成熟度低**：0.1.0 版本，个人维护，无性能基准数据。

候选替换库：`mlx-qwen3-asr`（作者 moona3k，工程级项目），核心优势：

- **词级时间戳**：`return_timestamps=True` → `result.segments = [{text, start, end}, ...]`
- **4-bit / 8-bit 量化**：最高 4.68× 加速，8-bit 字错率仅上升 0.04pp
- **自带纯 MLX 版 FA**：Qwen3-ForcedAligner-0.6B，比 PyTorch 版快 2.64 倍
- **462 项测试 + 基准门控**，多语言质量报告、与 PyTorch 官方逐项对比
- **WAV 快速路径**：原生二进制解析器，短片段提速最高 25%
- **依赖更干净**：核心只需 mlx + numpy + regex + huggingface-hub（4 个包）

---

## 二、两个库的逐项对比

| | **gabrimatic（当前）** | **moona3k（候选）** |
|---|---|---|
| **版本** | 0.1.0（beta 级） | 成熟项目（462 测试 + 基准门控） |
| **模型规格** | 只支持 1.7B | 0.6B（快、默认）+ 1.7B（高精度） |
| **词级时间戳** | ❌ 没有 | ✅ `return_timestamps=True`，segments 字段 |
| **量化** | 无 | 4-bit / 8-bit，最高 4.68× 加速 |
| **对齐器（FA）** | 无，需外部 PyTorch FA | ✅ 自带纯 MLX ForcedAligner，快 2.64× |
| **WAV 快速路径** | 无 | ✅ 原生二进制解析器，绕过 ffmpeg |
| **流式传输** | 无 | ✅ KV-cache 流式解码 + 麦克风实时转写 |
| **说话人分离** | 无 | ✅ pyannote 集成（可选，需 PyTorch） |
| **输出格式** | 纯文本 | txt / json / srt / vtt / tsv |
| **HTTP 服务器** | 无 | ✅ 内置异步服务器 + OpenAI API 兼容 |
| **推测解码** | 无 | ✅ 0.6B → 1.7B 加速 |
| **领域词汇** | 无 | ✅ system prompt 注入 |
| **依赖数** | 5 个 | 4 个 |
| **PyTorch 依赖** | 无 | 无（核心路径）；说话人分离可选 extra 需要 |
| **加载方式** | `Qwen3ASR.from_pretrained(...)` | `Session(...)` / `load_model(...)` / `transcribe(...)` |
| **模型 ID** | `mlx-community/Qwen3-ASR-1.7B-bf16` | `Qwen/Qwen3-ASR-1.7B` |
| **代码量** | 小（几个模块） | 7,602 行源码，19 模块 |
| **许可证** | MIT | Apache 2.0 |
| **作者** | Soroush Yousefpour（个人） | moona3k（有社区贡献） |

---

## 三、术语解释

### 词级时间戳（Word-level Timestamps）
千问 ASR 重听音频时，不止告诉你"说了什么"，还告诉你**每个词是第几秒到第几秒说的**。

输出格式：`[{text: "hello", start: 0.5, end: 0.8}, {text: "world", start: 1.0, end: 1.5}]`

moona3k 的实现方式是：千问 ASR 先做语音识别出文字 → 内部自动调一次 FA（Qwen3-ForcedAligner-0.6B）→ 把文字逐词掐到音频时间点上 → 返回 `segments` 字段。

### 对齐器（Forced Aligner / FA）
已知文字 + 已知音频 → 输出每个字的起止时间。"把文字掐到音频时间点上"。

现在你的 L1 用的是 PyTorch 版 FA（`from qwen_asr import Qwen3ForcedAligner`），对 Whisper 碎片文字做对齐，供 L1 判断"音频里这几秒到底有没有人在说这些字"。

moona3k 自带相同模型的 MLX 版（更快、不需要 PyTorch），换库后可以替换。

### 量化（Quantization）
fp16（16 位）→ 8-bit（压缩一半）→ 4-bit（压缩四分之一）。体积变小、跑得更快，精度微降。

moona3k 数据：
- 8-bit：快 3.11 倍，字错率升 0.04%（≈ 听不出）
- 4-bit：快 4.68 倍，字错率升 0.43%（极个别词可能听错）

### WAV 快速路径
WAV 是未压缩音频。moona3k 自带二进制 WAV 解析器，遇 WAV 直接读，跳过 ffmpeg 进程启动开销。短片段提速最高 25%。

### 流式传输（Streaming）
边听边出字，像实时字幕。用 KV-cache 跨块复用上下文，O(n) 复杂度。你的项目是离线处理，不需要。

### 说话人分离（Diarization）
识别"谁在几秒到几秒之间说话" → 输出 `[{speaker: "SPEAKER_00", start: 0, end: 5, text: "..."}]`。

moona3k 通过 pyannote.audio 实现（可选）。你的项目当前用的是 Resemblyzer（比较相邻两句话声纹相似度，打横杠标记说话人变化）。

### 推测解码（Speculative Decoding）
小模型（0.6B）先"猜"大模型（1.7B）会输出什么 → 大模型验证 → 猜对了省大模型时间。适合大批量高频转录场景。你用不上。

### 领域词汇（Domain Vocabulary）
通过 system prompt 注入专有名词，减少罕见词误识别。比如医学视频告知模型"会出现阿司匹林、布洛芬"。

---

## 四、词级时间戳 vs 对齐器（关键区分）

**这是两个不同的东西，但底层用的是同一个 FA 模型。**

| | 词级时间戳（Qwen ASR segments） | 对齐器（FA） |
|---|---|---|
| **谁生成的** | Qwen ASR 内部调 FA | 外部单独调 FA |
| **输入** | Qwen ASR 自己听出来的文字 + 原始音频 | Whisper 碎片文字 + 音频片段 |
| **输出** | `[{text, start, end}]` — Qwen 说的话在哪几秒 | `[{word, start, end}]` — Whisper 的每个字在哪几秒 |
| **用途** | L2 归窗（"Qwen 的这句话该归第几个 30s 窗口"） | L1 验证（"Whisper 说的这段文字，音频里有没有"） |

**你的项目里，当前从没用过词级时间戳。** 因为旧 ASR 库（gabrimatic）没这个功能。L1 交叉验证一直用的是外部 FA（对齐器）。

---

## 五、换库后的新架构流水线（详细）

```
        音频文件（WAV）
           │
    ┌──────┴──────┐
    ▼             ▼
[Whisper]    [Qwen-ASR + 内部FA]          ← ★ 同时并行，谁都不等谁
 转录出         ★ moona3k 新库
 碎片文字       ★ transcribe(audio, return_timestamps=True)
                ★ Qwen 自己检测语言，不等 Whisper
                ★ Qwen 自己按固定时间分块，不等 Whisper 给边界
  产出：         内部流程：
  [{id,text,      ① Qwen ASR 听音频 → 出文字
    start,end}]   ② 内部调 MLX FA → 逐词对齐出时间戳
                  ③ 返回 {text, segments:[{text,start,end}]}
    │             │
    │             ★ segments 每句话自带精确起止时间
    │             ★ 这份时间戳供 L1（交叉验证）和 L2（归窗）共用
    │             │
    └──────┬──────┘
           ▼
    [SenseVoice 单独跑]                  ← ★ 以前是 SV+FA 并行，现在只需 SV
     给每个碎片贴声音标签                 ★ FA 不再单独跑——千问的 segments
     Speech / Music / BGM / ...           已经能回答 L1 要问的"这几秒有人说话吗"
           │
           ▼
    ┌─────────────────────────────────────────────┐
    │           五层过滤                            │
    │  filter_hallucinations(segments,              │
    │    qwen_chunks=[{text,segments,t_start,t_end}])│
    └─────────────────────────────────────────────┘
           │
    ┌──────┼──────────────────────────────────────┐
    │      │                                      │
    ▼      ▼                                      ▼
  坍缩     L1 双信号交叉验证                    L2 30s窗口比对
  检测     ★ 不再需要外部 FA                   ★ Whisper 碎片：用自己的时间归窗
  Qwen     SenseVoice 标签                       （不变）
  文字     × Qwen segments 时间戳               ★ Qwen 文字：用 segments 的
  切句     "18-21s SV说音乐"                      start 时间精确算窗口
  数重复   → 查 segments：18-21s有字？            不再"猜"相似度
           → 有 → SV搞错，保留+标bgm_override     win = int(seg["start"] // 30)
           → 没有 → SV对，删碎片                 归窗后比对词根重叠率
                                                 高→GREEN 低→YELLOW
    └──────┼──────────────────────────────────────┘
           ▼
    L4 正则过滤 → L5 去重 → L3 语速检测
           │
           ▼
        翻译（P253 疲劳系数控制分块大小）
           │
           ▼
        合成字幕文件（SRT / VTT）
```

---

## 六、旧架构 vs 新架构

### 旧架构（当前 gabrimatic）

```
        音频文件
           │
           ▼
      Whisper 转录
           │  每 5 分钟一块
           ├──────────────────────┐
           ▼                      ▼
      Qwen-ASR（旧库）        Whisper 继续转
      ★ 等 Whisper 给语言
      ★ 等 Whisper 给分块边界
      ★ 没有时间戳
      产出: {text, t_start, t_end}
           │
           ▼  Whisper 全转完
      SV + FA 并行
      ├─ SenseVoice 贴标签
      └─ FA 对 Whisper 文字掐时间（PyTorch）
           │
           ▼
      五层过滤
      L1: SV标签 × FA时间 → 交叉验证
      L2: Whisper碎片(按时归窗) + Qwen文字(★ 相似度猜窗口, ~80行)
           │
           ▼  以下不变
```

### 新架构（换库后 moona3k）

```
        音频文件
           │
    ┌──────┴──────┐
    ▼             ▼
Whisper       Qwen-ASR（新库）
转录           ★ 自己分块、自己检测语言
               ★ 内部 FA 出时间戳
               ★ segments 供 L1+L2 共用
    │             │
    └──────┬──────┘
           ▼
    SenseVoice 单独跑  ← FA 不跑了！
           │
           ▼
      五层过滤
      L1: SV标签 × Qwen时间戳（★ 不用外部FA）
      L2: Whisper碎片(按时归窗) + Qwen文字(★ 精确时间归窗, ~15行)
           │
           ▼  以下不变
```

---

## 七、换库后的删减清单

| 删除项 | 原因 |
|--------|------|
| **qwen3-asr-mlx==0.1.0** | 旧 ASR 库，被 moona3k 替换 |
| **外部 FA 调用**（`QwenAligner` + `align_segments`） | 千问内部 FA 已出时间戳；L1 直接用 Qwen segments 验证 |
| **Qwen 等 Whisper 的队列 + 消费者线程** | 两边独立并行，不再需要"Qwen 等 Whisper 分块" |
| **qwen-asr==0.0.6**（PyTorch FA 包） | moona3k 自带 MLX 版 FA，PyTorch 不再需要 |
| **tokenizers 依赖** | moona3k 自带分词器 |
| **transformers 依赖**（如只被 FA 用） | FA 已迁到 MLX |
| **_assign_qwen_to_windows 的"猜"逻辑** | 换精确时间归窗（~80 行 → ~15 行） |

**保留（不受影响）：**
- Whisper 转录全部逻辑
- SenseVoice 全部逻辑
- L4/L5/L3 全部逻辑
- 翻译模块全部逻辑
- 说话人检测（Resemblyzer）
- Windows 端全部逻辑（`from qwen_asr import Qwen3ASRModel` 路径不变）

---

## 八、对 Windows 用户的影响：零

Windows 用户走的是 PyTorch 路径：
```python
from qwen_asr import Qwen3ASRModel
return Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")
```

Mac 用户走的是 MLX 路径：
```python
# 旧
from qwen3_asr_mlx import Qwen3ASR
return Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")

# 新
from mlx_qwen3_asr import load_model, Session
model, config = load_model("Qwen/Qwen3-ASR-1.7B")
```

两条路径从 `import` 开始就是不同的包、不同的类、不同的加载方式。换 Mac 端的 MLX 库，**不碰 Windows 端一个字节。** Windows 用户的依赖列表里根本没有 `mlx` 相关包（这些包是 Apple Silicon 专属，装不上 Windows）。

---

## 九、与新功能的关系

### 对 P251 的改进
P251 当前（旧库）：`_assign_qwen_to_windows` 用词根相似度"猜"每句话该归哪个 30s 窗口（~80 行）。

换库后：直接用千问 segments 的 `start` 时间精确算窗口索引，一行代码归窗。`_assign_qwen_to_windows` 从 ~80 行简化到 ~15 行。

### FA 迁移（可选升级）
moNA3k 自带纯 MLX 版 ForcedAligner。换库后可以将 L1 的 FA 从 PyTorch 迁到 MLX：

- 删 `qwen-asr==0.0.6`（PyTorch 千问包）
- 删 `transformers`、`tokenizers`（FA 用不到的依赖）
- FA/ASR 版本冲突**从源头消失**——不再有"两个东西抢同一个包"

但这一步**不是换库必须做的**——可以分批：先换 ASR 库（核心收益），再迁 FA（连带简化）。

### 说话人分离（暂不做）
moNA3k 通过 pyannote.audio 支持说话人分离。但你当前用的是 Resemblyzer，且说话人检测功能默认关闭（`speakerDetect=false`）。pyannote 需要 PyTorch（额外 ~200MB），且对你并非刚需。**记入未来想法，当前批次不碰。**

---

## 十、实施计划

| 步骤 | 内容 |
|------|------|
| 1 | 新建分支 `mlx-asr-upgrade` |
| 2 | `pip uninstall qwen3-asr-mlx && pip install mlx-qwen3-asr` |
| 3 | 改 `pyproject.toml` 依赖：删 `qwen3-asr-mlx==0.1.0`，加 `mlx-qwen3-asr` |
| 4 | 改 `qwen_asr_runner.py`：MLX 加载从 `Qwen3ASR.from_pretrained` → `load_model` |
| 5 | 改 `_transcribe_chunk` + `run_qwen_asr`：MLX 分支开 `return_timestamps=True`，取 `result.segments` |
| 6 | 改 `transcriber_v2.py`：Whisper 和 Qwen 并行启动（Qwen 不等 Whisper 分块） |
| 7 | 删 `_assign_qwen_to_windows` 中的"猜"逻辑 → 精确时间归窗 |
| 8 | 改 L1：用 Qwen segments 替代 FA（可选，可以分批做） |
| 9 | TDD：写测试、验证 Mac 端功能、全量回归 |
| 10 | 用户 Windows 端实测 |
| 11 | merge |

---

## 十一、待决策事项

| 决策 | 状态 |
|------|------|
| 换 ASR 库（gabrimatic → moona3k） | ✅ 用户已定，待开分支执行 |
| FA 同步迁移到 MLX（踢掉 qwen-asr 依赖） | ⏸ 待用户决定（可以分批做） |
| 说话人分离替换为 pyannote | ⏸ 记入未来想法，当前不做 |
| 量化（8-bit / 4-bit） | ⏸ 换库后再评估 |
