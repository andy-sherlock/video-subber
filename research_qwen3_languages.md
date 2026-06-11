# Qwen3-ASR 模型调研报告

> 调研日期: 2026-06-05
> 目的: 评估 Qwen3-ASR 是否适合替换或补充 video-subtitle 项目中的 Whisper

---

## 1. 模型概述

Qwen3-ASR 是阿里云通义千问团队于 2026 年 1 月 29 日发布的开源语音识别模型系列，基于 Qwen3-Omni 多模态基础模型构建。Apache 2.0 许可证。

**模型系列包含三个模型：**

| 模型 | 参数量 | 类型 | 用途 |
|------|--------|------|------|
| Qwen3-ASR-1.7B | ~2B (1.7B LLM + 300M 编码器) | 自回归 ASR | 最高精度 |
| Qwen3-ASR-0.6B | ~0.9B (0.6B LLM + 180M 编码器) | 自回归 ASR | 高效推理，128 并发下 2000x 吞吐 |
| Qwen3-ForcedAligner-0.6B | ~0.9B | 非自回归对齐 | 词/字级时间戳预测 |

**另有一个云 API 专有模型：**
| Qwen3-ASR-Flash | ~7B (未公开) | 云 API (DashScope) | 专有，非开源，支持情感识别和 Prompt 增强 |

---

## 2. 完整支持语言列表（52 种）

### 30 种国际语言

| # | 语言 | 代码 | FLEURS WER/CER (1.7B) | FLEURS WER/CER (0.6B) |
|---|------|------|------------------------|------------------------|
| 1 | 中文 (Chinese) | zh | 2.41 (CER) | 2.88 |
| 2 | 英语 (English) | en | 3.35 | 4.39 |
| 3 | 韩语 (Korean) | ko | 2.57 (CER) | 3.72 |
| 4 | 日语 (Japanese) | ja | 5.20 | 8.33 |
| 5 | 粤语 (Cantonese) | yue | 3.98 | 5.79 |
| 6 | 德语 (German) | de | 3.92 | 6.48 |
| 7 | 西班牙语 (Spanish) | es | 3.36 | 4.94 |
| 8 | 法语 (French) | fr | 4.75 | 7.72 |
| 9 | 葡萄牙语 (Portuguese) | pt | 3.92 | 6.21 |
| 10 | 意大利语 (Italian) | it | 2.41 | 4.99 |
| 11 | 印尼语 (Indonesian) | id | 5.16 | 7.92 |
| 12 | 俄语 (Russian) | ru | 5.99 | 9.91 |
| 13 | 泰语 (Thai) | th | 6.32 | 8.34 |
| 14 | 越南语 (Vietnamese) | vi | 5.55 | 8.52 |
| 15 | 土耳其语 (Turkish) | tr | 9.47 | 16.18 |
| 16 | 印地语 (Hindi) | hi | 17.15 | 19.12 |
| 17 | 马来语 (Malay) | ms | 10.39 | 17.66 |
| 18 | 荷兰语 (Dutch) | nl | 7.04 | 14.02 |
| 19 | 瑞典语 (Swedish) | sv | 19.36 | 35.87 |
| 20 | 丹麦语 (Danish) | da | 21.00 | 36.36 |
| 21 | 芬兰语 (Finnish) | fi | 25.23 | 46.59 |
| 22 | 波兰语 (Polish) | pl | 12.54 | 24.71 |
| 23 | 捷克语 (Czech) | cs | 22.42 | 47.67 |
| 24 | 菲律宾语 (Filipino) | fil | 24.29 | 36.10 |
| 25 | 波斯语 (Persian) | fa | 29.90 | 53.76 |
| 26 | 希腊语 (Greek) | el | 28.08 | 49.67 |
| 27 | 匈牙利语 (Hungarian) | hu | 34.22 | 59.47 |
| 28 | 马其顿语 (Macedonian) | mk | 19.05 | 37.26 |
| 29 | 罗马尼亚语 (Romanian) | ro | 20.70 | 44.26 |
| 30 | 阿拉伯语 (Arabic) | ar | 16.98 | 25.51 |

### 22 种中文方言

安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语(香港口音)、粤语(广东口音)、吴语、闽南语

### 额外支持
- **英语口音**: 16 个国家/地区（印度、英国、澳大利亚等）
- **语码切换**: 同句内多语言混合识别
- **歌唱识别**: 支持清唱和带背景音乐的歌曲

---

## 3. 翻译能力——不支持

**Qwen3-ASR 是纯转录(ASR)模型，不支持跨语言翻译。**

- 输入: 语音 → 输出: 同语言文本
- 不支持: 日语音频 → 英文文本（这是 speech translation，需要级联翻译引擎）

**如果你需要 speech translation(如日语语音→英文字幕):**
需要级联方案: Qwen3-ASR 转录为日文 → 再用 LLM/MT 模型翻译为英文

**注意:** HuggingFace 上有一个第三方项目 `voiceping-ai/qwen3-asr-ja-en-speech-translation`，它基于 Qwen3-ASR 微调了日→英 speech translation，但这不是官方功能。

---

## 4. 模型架构与训练

### 架构（三组件设计）

```
音频 → AuT 编码器 → 投影层 → Qwen3 LLM 解码器 → 文本
```

1. **AuT 编码器 (Audio Transformer)**
   - 输入: 128 维 Fbank 特征 (100Hz)
   - 3x Conv2d 下采样 + 18~32x 自注意力层
   - 8x 总下采样 → 12.5Hz token rate
   - 动态 Flash 注意力窗口 (1s~8s)，支持流式和离线统一推理

2. **投影层 (Projector)**
   - 桥接音频编码器到 LLM 解码器嵌入空间

3. **Qwen3 LLM 解码器**
   - 28 层解码器，GQA 注意力 (16 Q heads / 8 KV heads)
   - 交错式 MRoPE (Multi-dimensional RoPE)，分段 [24, 20, 20]
   - RoPE theta = 1,000,000
   - 词表大小: 151,936 (Qwen Tokenizer)
   - 8x 交叉注意力 + 自注意力块

### 四阶段训练流程

| 阶段 | 数据规模 | 内容 |
|------|----------|------|
| (1) AuT 预训练 | ~4000 万小时伪标注 ASR 数据 | 中英文为主，学习通用音频表征 |
| (2) Omni 预训练 | 3 万亿 token (多模态: 音频+视觉+文本) | 基于 Qwen3-Omni，获取多模态理解能力 |
| (3) ASR 监督微调 (SFT) | 多语言 ASR 数据 + 非语音数据 + 流式增强 | 将 Omni 模型转为 ASR 专用格式 |
| (4) ASR 强化学习 (RL) | ~5 万条 (35% 中英, 35% 多语言, 30% 功能) | 使用 GSPO 算法提升噪声鲁棒性和稳定性 |

---

## 5. 各语言精度排名

### 最佳精度 (FLEURS, 1.7B 模型)

**第一梯队 (WER/CER < 5):**
- 中文 (zh): 2.41 — 最强语言
- 意大利语 (it): 2.41
- 韩语 (ko): 2.57
- 英语 (en): 3.35
- 西班牙语 (es): 3.36
- 德语 (de): 3.92
- 葡萄牙语 (pt): 3.92
- 粤语 (yue): 3.98

**第二梯队 (WER 5~10):**
- 法语 (fr): 4.75
- 印尼语 (id): 5.16
- 日语 (ja): 5.20
- 越南语 (vi): 5.55
- 俄语 (ru): 5.99
- 泰语 (th): 6.32
- 荷兰语 (nl): 7.04
- 土耳其语 (tr): 9.47

**第三梯队 (WER 10~20):**
- 马来语 (ms): 10.39
- 波兰语 (pl): 12.54
- 阿拉伯语 (ar): 16.98
- 印地语 (hi): 17.15
- 马其顿语 (mk): 19.05
- 瑞典语 (sv): 19.36

**第四梯队 (WER > 20, 较弱):**
- 罗马尼亚语 (ro): 20.70
- 丹麦语 (da): 21.00
- 捷克语 (cs): 22.42
- 菲律宾语 (fil): 24.29
- 芬兰语 (fi): 25.23
- 希腊语 (el): 28.08
- 波斯语 (fa): 29.90
- 匈牙利语 (hu): 34.22

---

## 6. 模型规格对比

| 规格 | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B | ForcedAligner-0.6B |
|------|---------------|---------------|-------------------|
| 总参数量 | ~0.9B | ~2B | ~0.9B |
| 编码器参数 | 180M | 300M | 180M |
| 编码器隐藏维度 | 896 | 1024 | 896 |
| 编码器层数 | 18 | 32 | 18 |
| 解码器层数 | 28 | 28 | 28 |
| 解码器隐藏维度 | 1024 | 2048 | 1024 |
| GPU 内存 (bf16) | ~1.2 GB | ~3.4 GB | ~0.8 GB |
| 单并发 RTF | 0.00923 | 0.01482 | 0.00889 |
| 128 并发吞吐 | 2000x | 1219x | — |
| 支持语言数 | 52 | 52 | 11 |
| 最长音频 | 20 分钟 | 20 分钟 | 5 分钟 |

**选择建议:**
- 精度优先 → 1.7B
- 速度/资源优先 → 0.6B
- 需要词级时间戳 → 加载 1.7B(或 0.6B) + ForcedAligner-0.6B 组合
- 云 API 调用 → Qwen3-ASR-Flash (专有)

---

## 7. Qwen3-ASR 与 ForcedAligner 的关系

二者是**同一模型系列的不同模型**，共享 AuT 编码器和 Qwen3 骨干，但用途不同：

| 模型 | 用途 | 推理方式 |
|------|------|----------|
| Qwen3-ASR | 语音→文本转录 | 自回归 (逐个 token 生成) |
| Qwen3-ForcedAligner | 给定文本+音频→时间戳 | 非自回归 (并行预测) |

**集成方式 (官方 API):**
```python
from qwen_asr import Qwen3ASRModel

model = Qwen3ASRModel.from_pretrained(
    "Qwen/Qwen3-ASR-1.7B",
    forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
)
results = model.transcribe(audio="audio.wav", return_time_stamps=True)
```

- ASR 先转录，产生编码器隐藏状态
- ForcedAligner 复用这些编码器特征，预测每个词/字的起止时间
- 组合使用时显存峰值约为 ASR 模型峰值 + 对齐器附加开销 (~0.8GB)
- ForcedAligner 仅支持 11 种语言: 中、英、粤、法、德、意、日、韩、葡、俄、西
- ForcedAligner 仅开源 0.6B 尺寸，没有 1.7B 版本

---

## 8. 与 Whisper large-v3 的对比

### 总体结论
Qwen3-ASR-1.7B 在**绝大多数**基准测试上超越 Whisper large-v3，在中文和噪声鲁棒性上有**压倒性优势**。但在完整 30 语言 FLEURS 平均上 Whisper 略优（8.16 vs 12.60），说明 Whisper 在低资源语言上更均衡。

### 关键基准对比 (WER/CER, 越低越好)

| 数据集 | Whisper large-v3 | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B | 优势 |
|--------|-----------------|----------------|----------------|------|
| **英文** | | | | |
| LibriSpeech clean | 1.51 | 2.11 | 1.63 | Whisper 微弱领先 |
| LibriSpeech other | 3.97 | 4.55 | **3.38** | Qwen3 |
| GigaSpeech | 9.76 | 8.88 | **8.45** | Qwen3 |
| FLEURS-en | 4.08 | 4.39 | **3.35** | Qwen3 |
| **中文** | | | | |
| WenetSpeech test-net | 9.86 | 5.97 | **4.97** | Qwen3 大幅领先 (49.6%) |
| AISHELL-2 test | 5.06 | 3.15 | **2.71** | Qwen3 大幅领先 (46.4%) |
| SpeechIO | 7.56 | 3.44 | **2.88** | Qwen3 大幅领先 (61.9%) |
| KeSpeech (方言) | 28.79 | 7.08 | **5.10** | Qwen3 压倒性 (82.3%) |
| **多语言** | | | | |
| FLEURS 12 语言平均 | 5.27 | 7.57 | **4.90** | Qwen3 |
| FLEURS 20 语言平均 | 6.85 | — | **6.62** | Qwen3 |
| FLEURS 30 语言平均 | **8.16** | — | 12.60 | **Whisper 领先** (低资源语言) |
| CommonVoice 平均 | 10.77 | 12.75 | **9.18** | Qwen3 |
| **鲁棒性** | | | | |
| 带口音英语 | 21.30 | 16.62 | **16.07** | Qwen3 |
| 极端噪声 | 63.17 | 17.88 | **16.17** | Qwen3 压倒性 (74.4%) |
| 老人与儿童(普通话) | 10.61 | 4.48 | **3.81** | Qwen3 |
| **语言识别** | | | | |
| LID 平均准确率 | 94.1% | 96.8% | **97.9%** | Qwen3 |

### 日语/韩语/俄语 × Whisper 对比 (FLEURS)

| 语言 | Qwen3-1.7B | Qwen3-0.6B | 说明 |
|------|-----------|-----------|------|
| 日语 (ja) | 5.20 | 8.33 | 1.7B 表现良好，0.6B 仍可用 |
| 韩语 (ko) | 2.57 | 3.72 | 极佳，接近中文水平 |
| 俄语 (ru) | 5.99 | 9.91 | 1.7B 可用，0.6B 一般 |
| 中文 (zh) | 2.41 | 2.88 | 最强语言，远超 Whisper |

**注意:** 技术报告中 FLEURS 的 Whisper 对照只有 20 语言和 30 语言汇总平均，没有逐语言对比。以上日语/韩语/俄语的 Whisper 分数需单独实测。

### Open ASR Leaderboard 总平均
- Cohere Transcribe: 5.42%
- **Qwen3-ASR: 5.76%**
- Whisper large-v3: 7.44%

---

## 9. video-subtitle 项目的关键影响

### 优势
1. **中文精度极高**: WenetSpeech 上比 Whisper 好 ~49%，对中文视频字幕场景是重大提升
2. **噪声鲁棒性**: 极端噪声下 WER 从 63.17 降到 16.17，对现实场景视频非常有利
3. **方言支持**: 22 种中文方言，Whisper 完全无法竞争
4. **语言识别准确**: 97.9% vs Whisper 94.1%，自动检测更可靠
5. **Apache 2.0 许可证**: 商业友好，无 Whisper 的顾虑
6. **流式+离线统一**: 一个模型同时支持实时和批量

### 劣势/风险
1. **不支持翻译**: 如果需要"日语语音→英文字幕"，必须级联翻译引擎
2. **低资源语言精度差**: 匈牙利语 (34.22)、波斯语 (29.90)、希腊语 (28.08) 等非常弱
3. **日语可用但不惊艳**: 日语 FLEURS WER 5.20，比中文/韩语差不少
4. **硬件门槛**: 1.7B 模型需要 ~3.4GB 显存 (bf16)，Mac 上需要 MLX 移植版本
5. **成熟度**: 2026 年 1 月才发布，生态不如 Whisper 成熟
6. **MPS 支持**: 需要通过 MLX 移植 (`mlx-qwen3-asr`) 才能在 Apple Silicon 上运行

### 建议
- **中文视频字幕**: 强烈推荐替换 Whisper，精度提升巨大
- **日语/韩语/俄语**: 可以用，精度优于 Whisper 但不是压倒性
- **小语种**: 保留 Whisper 作为后备
- **需要翻译**: 不能用 Qwen3-ASR 直接替代，需要级联方案

---

## 10. 信息来源

- 官方 GitHub: https://github.com/QwenLM/Qwen3-ASR
- 官方工具包: https://github.com/QwenLM/Qwen3-ASR-Toolkit
- HuggingFace 模型页: https://huggingface.co/Qwen/Qwen3-ASR-1.7B
- 技术报告 (arXiv): https://arxiv.org/abs/2601.21337
- ForcedAligner 模型页: https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B
- MLX 移植版 (Apple Silicon): https://github.com/moona3k/mlx-qwen3-asr
- PyPI 包: https://pypi.org/project/qwen-asr/
- Northflank 2026 STT 对比: https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks
- Brown University CCV 对比: https://docs.ccv.brown.edu/ai-tools/services/transcribe/comparing-speech-to-text-models
