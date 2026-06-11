# WhisperJAV 竞品研究报告

**研究日期：** 2026-06-05
**研究项目：** [meizhong986/WhisperJAV](https://github.com/meizhong986/WhisperJAV)
**研究目的：** 了解其技术方案，作为 video-subtitle 项目的竞品参考

---

## 1. 仓库概览

| 指标 | 数值 |
|------|------|
| **仓库地址** | https://github.com/meizhong986/WhisperJAV |
| **Stars** | ~1,700 |
| **最新版本** | v1.8.14（2026-05-10） |
| **License** | MIT |
| **语言构成** | Python 78.9%, Jupyter Notebook 7.2%, JavaScript 4.9%, Batch 2.8%, HTML 2.2%, CSS 2.0%, Shell 2.0% |
| **平台支持** | Windows (.exe 安装包), macOS (Apple Silicon MPS), Linux (Ubuntu/Debian/Fedora/Arch), Google Colab, Kaggle |
| **Python 版本** | 3.10–3.12 |

**定位：** 专为日本成人视频（JAV）设计的 ASR/STT 字幕生成器，宣称在 JAV 领域比标准 Whisper 模型效果好得多。

---

## 2. Qwen3-ASR 集成方式

### 2.1 Qwen3-ASR 模型本身

- **厂商：** 阿里巴巴 Qwen 团队，2026 年 1 月 30 日发布
- **License：** Apache 2.0（完全开源）
- **模型规格：** Qwen3-ASR-0.6B（92ms TTFT）和 Qwen3-ASR-1.7B（开源 ASR 中的 SOTA）
- **支持语言：52 种**（30 种语言 + 22 种汉语方言）

#### 30 种语言：
中文、英文、粤语、阿拉伯语、德语、法语、西班牙语、葡萄牙语、印尼语、意大利语、韩语、俄语、泰语、越南语、**日语**、土耳其语、印地语、马来语、荷兰语、瑞典语、丹麦语、芬兰语、波兰语、捷克语、菲律宾语、波斯语、希腊语、匈牙利语、马其顿语、罗马尼亚语

#### 22 种汉语方言：
安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语（香港/广东）、吴语、闽南语等

#### 关键能力：
- 支持单句内多语言混用（code-switching）
- 语言识别准确率 97.9%（30 种语言间），对比 Whisper-large-v3 仅 94.1%
- 比 Whisper 快 1.2–1.5 倍
- 支持流式处理（Whisper 不支持）

### 2.2 WhisperJAV 中的 Qwen 流水线

WhisperJAV 共有 **7 条处理流水线**，Qwen 是其中之一：

| 流水线 | 后端 | 场景检测 | 语音增强 | 语音分段 | 最佳用途 |
|--------|------|----------|----------|----------|----------|
| **qwen** | Qwen3-ASR | Semantic | 可配置 | Silero | Qwen ASR + 强制对齐 |

### 2.3 Qwen3-ASR 三种处理模式

由于 Qwen3-ASR 不原生输出时间戳，WhisperJAV 提供三种子模式：

| 模式 | 工作原理 | 最佳用途 |
|------|----------|----------|
| **Assembly** | 先生成文本，再对齐时间戳；批量处理场景（最大 120s） | 大多数内容 |
| **Context-Aware** | ASR + 对齐同时进行，处理完整场景（30–90s） | 需要更多上下文的语句 |
| **VAD Slicing**（默认） | 耦合 ASR + 对齐，逐步回退 | 更多细节，较少上下文 |

---

## 3. ChronosJAV —— 解决时间戳问题的专用流水线

### 3.1 核心设计理念

ChronosJAV 是一个**专为不产生自身时间戳的模型设计的流水线**，适用的模型包括：
- LLM（大语言模型做转录）
- Qwen3-ASR
- anime-whisper
- Kotoba-Whisper

### 3.2 两阶段分离架构

ChronosJAV 将**文本生成**和**时间戳对齐**分为两个独立阶段：

1. **阶段 1 —— 文本生成：** 由 ASR 模型（Qwen3-ASR / anime-whisper / LLM）生成纯文本转录
2. **阶段 2 —— 时间戳对齐：** 使用**本地强制对齐器（local forced aligner）**生成词级时间戳

### 3.3 可插拔架构

任何音频转文本模型都可以通过 YAML 配置接入 ChronosJAV，无需修改代码。这是一个关键的架构优势。

### 3.4 已知限制

- Mac 上 Qwen 流水线**仅运行在 CPU**上，因为强制对齐器尚不支持 MPS 加速
- NVIDIA GPU (CUDA) 和 Apple Silicon (MPS) 在训练/推理上有完整支持

---

## 4. 高质量翻译技术栈

### 4.1 翻译后端（按推荐度排序）

| 后端 | 类型 | 质量 | 成本 | 备注 |
|------|------|------|------|------|
| **DeepSeek**（推荐） | 云 API | **最优** | 便宜 | 开发者推荐；v1.8.14 默认模型升级为 deepseek-v4-flash |
| Ollama | 本地 | 良好 | 免费 | 完全离线，无需 API key |
| Claude | 云 API | 高 | 付费 | — |
| GPT-4 | 云 API | 高 | 付费 | — |
| Gemini | 云 API | 中高 | 免费层 | — |
| Local LLM | 本地 (llama-cpp) | 一般 | 免费 | 自动下载 ~700MB，支持断点续传 |

### 4.2 Ollama 本地方案细节

OllamaManager 自动启动服务、检测 GPU，根据 VRAM 推荐模型：

| VRAM | 推荐模型 |
|------|----------|
| CPU only | `qwen2.5:3b` |
| 8 GB | `qwen2.5:7b` |
| 12 GB | `gemma3:12b` |
| 16 GB+ | `qwen2.5:14b` |

支持参数：`--ollama-num-ctx`（上下文窗口）、`--ollama-max-tokens`（输出长度）

### 4.3 当前翻译方向限制

- **主方向：日语 → 英语**（Japanese → English）
- **不支持日语 → 中文**（至少不是一等支持）
- DeepSeek 翻译据用户反馈能**纠正转录中的错误**

### 4.4 推荐工作流

开发者推荐的两阶段集成方案：

```
第一轮：Fidelity | Balanced 敏感度 | TEN VAD | Large-V2
第二轮：Balanced | Aggressive 敏感度 | Silero V4 | Large-V2
合并策略：Pass 1 为主 + Pass 2 填补空白
翻译：DeepSeek 提供最佳英语效果
```

---

## 5. 核心技术手段汇总

### 5.1 场景检测（4 种方法）

| 方法 | 原理 | 特点 |
|------|------|------|
| **Semantic**（默认） | 基于 MFCC 特征的纹理聚类，将声学相似的片段分组 | 最智能 |
| **Auditok** | 基于能量的检测 | 快速 |
| **Silero** | 基于神经网络的 VAD | 噪音环境更好 |
| **TEN** | ChronosJAV 专用 | 紧凑字幕时序 |

### 5.2 VAD 语音活动检测

- **Silero V4：** 大多数流水线的默认分段器，防止静音时产生幻觉
- **TEN VAD：** anime 流水线专用

### 5.3 语音增强（5 种后端）

| 后端 | 说明 |
|------|------|
| `none`（默认） | 无增强 |
| `ffmpeg-dsp` | FFmpeg 音频滤镜：loudnorm, denoise, compress, highpass, lowpass, deess |
| `clearvoice` | ClearerVoice 降噪：MossFormer2_SE_48K（默认）、FRCRN_SE_16K |
| `zipenhancer` | ZipEnhancer 16kHz：torch (GPU) 或 onnx (CPU) |
| `bs-roformer` | 人声分离：vocals 或 other |

**警告：** 改变 mel 频谱的处理可能引入伪影。

### 5.4 敏感度设定（幻觉控制）

通过 log-probability 阈值化和 `no_speech_threshold` 控制：

| 级别 | 阈值 | 幻觉 | 适用场景 |
|------|------|------|----------|
| **Conservative** | 高 | 少 | 噪音内容 |
| **Balanced**（默认） | 中 | 中 | 大多数内容 |
| **Aggressive** | 低 | 多（但捕获更多对话） | 耳语/ASMR 内容 |

### 5.5 日语后处理

- 处理句尾语气词（ね、よ、わ、の）
- 保留相槌（うん、はい、ええ）
- 识别方言模式（关西腔、女性/男性用语）
- 过滤常见 Whisper 幻觉
- 标准化领域特定术语
- 保留拟声词
- 修正标准 BPE 分词器对方言导致的 tokenization 错误

### 5.6 幻觉移除

- 调优的 log-probability 阈值化和 `no_speech_threshold` 系统性丢弃低置信度输出
- 正则过滤器清除非词汇标记（如 `(moans)`）

### 5.7 双轮集成模式（Two-Pass Ensemble）

运行两轮不同流水线，合并结果。合并策略：

| 策略 | 说明 |
|------|------|
| `pass1_primary` / `pass2_primary` | 优先某轮，从另一轮补空白 |
| `smart_merge` | 智能重叠检测合并 |
| `full_merge` | 两轮全部合并 |
| `pass1_overlap` / `pass2_overlap` | 重叠感知的优先级合并 |
| `longest` | 每段保留较长的字幕 |

支持 **BYOP（自备流水线）**：可接入 PurfView's Faster Whisper XXL 作为第二轮。

---

## 6. 质量基准与对比

### 6.1 Qwen3-ASR 在标准基准上的表现

#### 英文 ASR —— WER（越低越好）

| 数据集 | Qwen3-ASR-1.7B | Whisper-large-v3 | GPT-4o-Transcribe |
|--------|---------------|------------------|-------------------|
| LibriSpeech (clean) | 1.63 | 1.51 | **1.39** |
| LibriSpeech (other) | **3.38** | 3.97 | 3.75 |
| GigaSpeech | **8.45** | 9.76 | — |
| CommonVoice-en | **7.39** | 9.90 | — |
| Tedlium | **4.50** | 6.84 | 7.69 |

#### 中文 ASR —— CER（越低越好）

| 数据集 | Qwen3-ASR-1.7B | Whisper-large-v3 | GPT-4o-Transcribe |
|--------|---------------|------------------|-------------------|
| WenetSpeech (net) | **4.97** | 9.86 | 15.30 |
| WenetSpeech (meeting) | **5.88** | 19.11 | 32.27 |
| AISHELL-2 test | **2.71** | — | 4.24 |
| SpeechIO | **2.88** | — | 12.86 |

#### 歌声识别 —— WER

| 数据集 | Qwen3-ASR-1.7B | GPT-4o | Doubao-ASR |
|--------|---------------|--------|------------|
| M4Singer | **5.98** | 16.77 | 7.88 |
| EntireSongs-en | **14.60** | 30.71 | 33.51 |
| EntireSongs-zh | **13.91** | 34.86 | 23.99 |

#### Brown University 对比

| 指标 | OpenAI Whisper | Qwen3-ASR | Cohere Transcribe |
|------|---------------|-----------|-------------------|
| WER | 7.44% | **5.76%** | **5.42%** |
| 速度 | <5 min/音频小时 | **1.2–1.5x 快于** Whisper | <5 min/音频小时 |
| 开源 | 是 | 是 | 是 |

### 6.2 WhisperJAV 内部基准

**WhisperJAV 自身没有发布跨流水线的 WER 基准数据。** README 中没有 pipeline-vs-pipeline 的质量对比，也没有与
其他字幕工具（如 pyTranscriber、VidSub、video-subtitle 等）的对比。

唯一的速度基准（每音频小时）：

| 硬件 | 耗时 |
|------|------|
| NVIDIA RTX GPU | 5–10 分钟 |
| Apple Silicon | 8–15 分钟 |
| CPU | 30–60 分钟 |

---

## 7. 关键发现与对 video-subtitle 的启示

### 7.1 WhisperJAV 的核心创新

1. **ChronosJAV 可插拔架构：** 将文本生成和时间戳对齐分离，任何 ASR 模型可通过 YAML 配置接入——这是架构层面的亮点
2. **Semantic 场景检测：** 基于 MFCC 特征的声学纹理聚类，比纯能量检测（Auditok）更智能
3. **双轮集成 + 多策略合并：** 两种流水线互补，6 种合并策略覆盖不同场景
4. **三维敏感度控制：** Conservative/Balanced/Aggressive 的 log-probability 阈值体系
5. **日语专项后处理：** 方言、相槌、语气词、拟声词、幻觉过滤

### 7.2 WhisperJAV 的弱点/缺失

1. **没有日语→中文翻译：** 这是显著的功能缺口
2. **没有内部质量基准：** 无法量化评估各流水线的实际 WER 差异
3. **Mac 上 Qwen 流水线仅 CPU：** 强制对齐器不支持 MPS
4. **翻译系统与转录系统解耦：** `whisperjav-translate` 是一个独立命令，不是流水线内嵌功能（但可以 `--translate` 内联）
5. **Qwen 流水线的三种模式间没有基准对比：** 无法知道 Assembly vs Context-Aware vs VAD Slicing 的实际差异

### 7.3 对 video-subtitle 的可借鉴点

| 技术点 | 借鉴价值 | 说明 |
|--------|----------|------|
| ChronosJAV 分离架构 | **高** | ASR 文本生成 + 强制对齐分离，便于切换后端 |
| Semantic 场景检测 | **中** | MFCC 纹理聚类比 VAD 切分更聪明 |
| 双轮集成 + 多策略合并 | **高** | 不同模型互补，smart_merge 策略值得参考 |
| 敏感度三维控制 | **高** | log-probability + no_speech_threshold 组合控制幻觉 |
| 日语后处理链 | **高** | 方言、语气词、拟声词保留——这些 video-subtitle 可以考虑 |
| Qwen3-ASR 作为后端 | **中** | 52 语言 + 歌声识别 + 1.5x 速度优势 |
| 语音增强前端 | **中** | ClearVoice / BS-RoFormer / ZipEnhancer |
| Ollama 本地翻译 | **中** | 根据 VRAM 自动选模型，完全离线 |

### 7.4 video-subtitle 的差异化方向

WhisperJAV 专注 JAV 领域且只有日语→英语翻译。video-subtitle 可以在以下方面形成差异化：

- **日语→中文翻译**（WhisperJAV 缺失的方向）
- **多语言翻译**（不仅是英译，覆盖中日韩互译）
- **说话人分离（diarization）**——WhisperJAV 提到 "Better (via pyannote)" 但未深入集成
- **更透明的基准体系**——发布自己的 WER/CER 对比
- **更好的 Mac 支持**——WhisperJAV 的 Qwen pipeline 在 Mac 上降级到 CPU

---

## 8. 信息来源

- [WhisperJAV GitHub 仓库](https://github.com/meizhong986/WhisperJAV)
- [WhisperJAV README](https://github.com/meizhong986/WhisperJAV/blob/main/README.md)
- [WhisperJAV Releases](https://github.com/meizhong986/WhisperJAV/releases)
- [Qwen3-ASR 官方页面](https://qwen-ai.com/qwen-asr/#benchmarks)
- [Brown University CCV AI Services - ASR 模型比较](https://docs.ccv.brown.edu/ai-tools/services/transcribe/comparing-speech-to-text-models)
- [VoicePing Qwen3-ASR JA-EN Speech Translation (HuggingFace)](https://huggingface.co/voiceping-ai/qwen3-asr-ja-en-speech-translation)
- [live-translate Issue #268 - Qwen3-ASR 多语言集成讨论](https://github.com/rioX432/live-translate/issues/268)
