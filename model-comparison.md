# 模型能力对比：Whisper vs SenseVoice vs Qwen3-ASR

> 创建日期：2026-06-10
> 最后更新：2026-06-11（加入 Qwen3-ASR）
> 用途：为 video-subtitle 项目的转录引擎选型提供数据支撑
> 更新：有新 benchmark 或新模型时更新此文件

---

## 一、模型概览

| 维度 | SenseVoice Small | Qwen3-ASR 1.7B | Whisper Large V3 |
|------|:---:|:---:|:---:|
| **开发者** | 阿里达摩院 FunAudioLLM | 阿里云 Qwen 团队 | OpenAI |
| **发布时间** | 2024-07 | 2026-01 | 2023-05 |
| **参数量** | 234M | ~2B（1.7B decoder + 300M encoder） | 1,550M |
| **架构** | 非自回归 Encoder-Only | AuT Encoder + LLM Decoder | 自回归 Encoder-Decoder |
| **下载大小** | ~827 MB | ~4.7 GB (BF16) | ~3,000 MB |
| **运行时内存** | ~700 MB | 4–14 GB（视音频长度） | ~2,000 MB |
| **核心语言数** | 5（中/粤/英/日/韩） | 52（30 语种 + 22 方言） | 99+ |
| **中文方言** | 粤语一种 | **22 种**（粤/吴/闽南/四川/东北…） | 粤语（弱） |
| **训练数据** | 40 万+ 小时 | 未公开 | 68 万小时 |
| **开源协议** | Apache 2.0 | Apache 2.0 | MIT |
| **推理方式** | 一次前向并行出结果 | 自回归逐 token | 自回归逐 token |
| **流式转录** | ❌ | ✅ 流式/离线一体 | ❌ |

---

## 二、中文准确率对比（核心）

### 2.1 Qwen3-ASR vs Whisper 直接对比（FireRedASR2S 统一基准平台）

**这是最可信的对比——两模型在同一平台、同一测试条件下跑分。SenseVoice 不在这个统一基准中（见下方说明）。**

| 测试集 | Qwen3-ASR 1.7B | Whisper Large V3 | Qwen3 优势 |
|--------|:---:|:---:|:---:|
| **AISHELL-1**（朗读） | **1.48%** | 5.14% | **3.5×** |
| **AISHELL-2**（朗读） | **2.71%** | — | — |
| **WenetSpeech net**（网络） | **4.97%** | 9.86% | **2.0×** |
| **WenetSpeech meeting**（会议） | **5.88%** | **19.11%**¹ | **3.2×** |
| **普通话平均**（4 集合计） | **3.76%** | — | — |

> ¹ Whisper 在会议场景 CER 高达 19.11%——几乎不可用

**关键发现：Qwen3-ASR 的中文 CER（1.48%）是 Whisper（5.14%）的 3.5 分之一。在会议场景差距更大——Qwen3 5.88% vs Whisper 19.11%，差了 3.2 倍。**

> 来源：腾讯云开发者社区「中文语音识别该用谁？6 个开源模型一文理清」（2026-03 更新），FireRedASR2S 仓库统一基准

### 2.1b SenseVoice 的中文准确率（独立来源，非统一平台）

**⚠️ SenseVoice 不在 FireRedASR2S 统一基准中。以下数据来自其自身仓库，不能与 2.1 节直接比大小。**

| 测试集 | SenseVoice Small | 来源 |
|--------|:---:|------|
| AISHELL-1 | **~3.0%**¹ | SenseVoice HuggingFace README 柱状图估算 |
| CommonVoice 中文（zh-CN） | **10.78% CER** | SenseVoice 论文（见 2.2） |

> ¹ 这个 ~3.0% 是在 SenseVoice 自己的测试条件下跑出来的。FireRedASR2S 平台上 Whisper 跑了 5.14%——但同一模型在不同测试条件下的分数不完全可比（音频预处理、解码参数、评估脚本都可能不同）。

**能确定的结论：**

1. **SenseVoice 和 Qwen3-ASR 都远优于 Whisper 中文**——各来源独立验证，方向一致
2. **Qwen3-ASR vs SenseVoice 谁更准——没有直接对比数据**。各自在自己的测试条件下跑分，数字不能直接比
3. 要真正比出高下——需要在项目自己的测试视频上，用相同条件各跑一遍

### 2.2 CommonVoice 多语言基准（SenseVoice 论文）

| 语言 | SenseVoice Small | Whisper Large V3 | 胜者 | 优势幅度 |
|------|:---:|:---:|:---:|:---:|
| **中文（zh-CN）** | **10.78% CER** | 12.55% CER | SenseVoice | 14% ↓ |
| **粤语（yue）** | **7.09% CER** | 10.41% CER | SenseVoice | 32% ↓ |
| 日语（ja） | 11.96% CER | **10.34% CER** | Whisper | 14% ↓ |
| 韩语（ko） | 8.28% CER | **5.59% CER** | Whisper | 32% ↓ |
| 英语（en） | 14.71% WER | **9.39% WER** | Whisper | 36% ↓ |

> ⚠️ Qwen3-ASR 不在这个测试集上。只能用 AISHELL-1 交叉推算：Qwen3 1.48% vs SenseVoice ~3.0% vs Whisper 5.14%，推测 Qwen3 在 CommonVoice 中文上约 5-6% CER。

> 来源：FunAudioLLM/SenseVoice 论文 (arXiv:2407.04051)

### 2.3 中文真实场景独立测试（CSDN，RTX 4090D）

**SenseVoice vs Whisper 直接对比。Qwen3-ASR 不在本测试中。**

| 场景 | SenseVoice Small | Whisper Large V3 | SenseVoice 优势 |
|------|:---:|:---:|:---:|
| 新闻播音（干净） | **2.8% WER** | 3.2% | 12.5% ↓ |
| BGM + 旁白 | **5.1% WER** | 8.7% | 41% ↓ |
| 客服电话（带口音） | **6.4% WER** | 9.3% | 31% ↓ |
| 直播（笑声/掌声） | **7.9% WER** | 12.5% | 37% ↓ |
| 情感朗读 | **5.6% WER** | 10.1% | 45% ↓ |

> ⚠️ Qwen3-ASR 不在这个测试里。从 2.1 的 FireRedASR2S 数据推测，Qwen3 在这些场景上的表现大概率更好——但这是推测，不是实测。

### 2.4 极端噪音鲁棒性（超低信噪比）

**各自的独立测试——不是同一平台。**

| 测试 | Qwen3-ASR 1.7B | SenseVoice Small | Whisper Large V3 |
|------|:---:|:---:|:---:|
| 超低信噪比 WER（Qwen3 官方） | **16.17%** | — | 63.17% |
| 高噪直播 CER（CSDN 独立测试） | — | **6.1%** | 18.5%（出幻觉） |
| WenetSpeech 会议 CER（FireRedASR2S 统一平台） | **5.88%** | — | 19.11% |

> ⚠️ 各行来自不同测试，横向不可比。但纵向每个独立测试都指向同一结论：**国产模型（Qwen3 / SenseVoice）在噪音下的鲁棒性远超 Whisper。** Whisper 在极端噪音下 CER 飙到 63%——基本上在乱猜。

### 2.5 粤语六领域独立评测（AlienKevin/cantonese_asr_eval）

| 领域 | SenseVoice Small | Whisper Large V2 | 胜者 |
|------|:---:|:---:|:---:|
| 日常用语 | **5.64% CER** | 12.38% | SenseVoice |
| 指令 | **7.45% CER** | 14.22% | SenseVoice |
| **粤英混合** | **9.05% CER** | 20.85% | SenseVoice（2.3×） |
| 故事讲述 | **14.67% CER** | 18.54% | SenseVoice |
| 合成语音 | **10.58% CER** | 14.20% | SenseVoice |
| 混合领域 | 7.70% CER | **5.55% CER** | Whisper |

> ⚠️ Qwen3-ASR 不在这个测试集上。但 Qwen3 官方报告的粤语 Fleurs-yue CER 为 3.98%（[qwen-ai.com](https://qwen-ai.com/qwen-asr/#benchmarks)），如果属实，比 SenseVoice 的 5.64% 还低 29%。

### 2.6 方言覆盖

| 模型 | 支持方言数 | 代表性方言 |
|------|:---:|------|
| **Qwen3-ASR** | **22 种** | 粤、吴、闽南、四川、东北、河南、湖南、陕西… |
| SenseVoice Small | 1 种（粤语） | 仅粤语 |
| Whisper Large V3 | 1 种（粤语，弱） | 仅粤语，且效果差（CER 10-38%） |

**如果用户视频含方言——Qwen3-ASR 是目前唯一选择。**

---

## 三、英文准确率对比

| 测试集 | Qwen3-ASR 1.7B | Whisper Large V3 | 胜者 |
|--------|:---:|:---:|:---:|
| LibriSpeech clean | **1.63% WER** | 1.51% | Whisper（微弱） |
| LibriSpeech other | **3.38% WER** | 3.97% | **Qwen3** |
| GigaSpeech | **8.45% WER** | 9.76% | **Qwen3** |
| CommonVoice en | — | **9.39% WER**¹ | — |

> ¹ SenseVoice 英文 CommonVoice WER 为 14.71%，明显弱于 Whisper（9.39%）和 Qwen3（估计 9% 左右）

**英文上三者接近，Qwen3 略优于 Whisper。SenseVoice 英文明显较弱。**

> 来源：qwen-ai.com/qwen-asr/#benchmarks（Qwen3 官方）

---

## 四、速度对比

### 4.1 推理延迟（统一口径）

| 模型 | 延迟（10 秒音频） | RTF（单并发） | 相对速度 |
|------|:---:|:---:|:---:|
| **SenseVoice Small** | **70ms**（A800） | ~0.007 | **基准（最快）** |
| Qwen3-ASR 0.6B | — | 0.00923 | 接近 SenseVoice |
| Qwen3-ASR 1.7B | — | 0.01482 | 比 SenseVoice 慢 ~2× |
| Whisper Large V3 | 1,281ms（A800） | — | 比 SenseVoice 慢 18× |

### 4.2 Apple M4 Pro Mac 实测（Whisper Notes，2026）

| 测试音频 | SenseVoice Small | Whisper Large V3 Turbo |
|------|:---:|:---:|
| 27 分钟中文播客 | **13.8 秒**（118× 实时） | 124 秒（13.1× 实时） |
| 5 分钟英文播客 | **5.8 秒**（52× 实时） | 20.9 秒（14.3× 实时） |

> ⚠️ Qwen3-ASR 没在这台 Mac 上测。但已知其 RTF 0.01482，在 Mac 上估计比 SenseVoice 慢但远快于 Whisper。

### 4.3 消费级 GPU 实测（Qwen3-ASR）

| 配置 | Qwen3-ASR 1.7B RTF | 吞吐量 |
|------|:---:|:---:|
| RTX 3050 6GB（FP16） | 0.12–0.32 | 每秒 3.2–8.3 倍实时 |
| vLLM 后端（0.6B） | — | 128 并发 2000× 加速 |

**速度排名：SenseVoice > Qwen3-ASR 0.6B > Qwen3-ASR 1.7B >> Whisper**

---

## 五、独有能力对比

| 能力 | SenseVoice Small | Qwen3-ASR 1.7B | Whisper Large V3 |
|------|:---:|:---:|:---:|
| 语音转文字（ASR） | ✅ | ✅ | ✅ |
| 语种识别（LID） | ✅ 内置 | ✅ 自动 | ✅ 内置 |
| **情感识别（SER）** | ✅ 7 种情绪 | ❌ | ❌ |
| **音频事件检测（AED）** | ✅ BGM/掌声/笑声/咳嗽等 | ❌ | ❌ |
| **中文方言识别** | 仅粤语 | ✅ **22 种** | 仅粤语（弱） |
| **流式转录** | ❌ | ✅ 流式/离线一体 | ❌ |
| **歌声识别** | ❌ | ✅ 带 BGM 歌声转写 | ❌ |
| **Code-switching** | ✅ 强（粤英） | ✅ 强 | 弱 |
| 语音翻译（ST→TT） | ❌ | ❌ | ✅ |
| 时间戳输出 | ✅ CTC 对齐 | ✅ 强制对齐 | ✅ Cross-attention |
| 标点恢复 + ITN | ✅ 内置 | ✅ 内置 | ✅ 内置 |

**三模型的独门绝技各不相同：**

- **SenseVoice**：唯一能同时输出情感 + 音频事件的模型。这是你的项目 L1 双信号交叉验证的基础。Whisper 和 Qwen3-ASR 都做不到。
- **Qwen3-ASR**：22 种中文方言 + 流式转录 + 歌声识别。中文能力最全面。
- **Whisper**：99+ 语言 + 语音翻译 + 最大生态。多语言覆盖无可替代。

---

## 六、模型大小与部署成本

| 模型 | 参数量 | 下载 | 最小显存 | 推荐显存 |
|------|:---:|:---:|:---:|:---:|
| SenseVoice Small | 234M | 827 MB | —（CPU 可跑） | 无要求 |
| Qwen3-ASR 0.6B | 600M | ~1.2 GB | 4 GB | 6 GB |
| Qwen3-ASR 1.7B | ~2B | 4.7 GB | 4 GB | 16 GB |
| Whisper Medium | 769M | 1.5 GB | 2 GB | 4 GB |
| Whisper Large V3 | 1.55B | 3 GB | 4 GB | 8 GB |

**SenseVoice 是最轻量的——234M 参数，CPU 就能跑，700MB 内存。Qwen3-ASR 1.7B 是最重的——需要 4-14GB 显存。**

---

## 七、社区生态

| 维度 | SenseVoice | Qwen3-ASR | Whisper |
|------|:---:|:---:|:---:|
| GitHub Stars | ~7k | ~3k（新发布） | ~75k |
| C++ 推理 | SenseVoice.cpp | QwenASR (Rust) | whisper.cpp（47.5k★） |
| Python 推理 | FunASR | qwen-asr（PyPI） | faster-whisper, WhisperX |
| GUI 工具 | ❌ | ❌ | ✅ 多个 |
| 社区规模 | 中文社区为主 | 中文社区，增长快 | 全球最大 |
| 文档质量 | 中 | 好（官方博客 + Model Card） | 好 |

---

## 八、交叉比对总结

### 哪些对比是直接的、哪些是推算的

| 对比对 | 数据来源 | 可比性 |
|--------|------|:---:|
| Qwen3-ASR vs Whisper | FireRedASR2S 统一基准 | ✅ 直接对比 |
| SenseVoice vs Whisper | CommonVoice（同一论文）/ 各自 AISHELL-1 | ✅ 直接对比 |
| **Qwen3-ASR vs SenseVoice** | **无同一平台测试** | **❌ 不可直接比** |

**Qwen3-ASR 和 SenseVoice 之间——我们没有同一基准平台上的直接对比数据。** 各自在自己的测试条件下跑分（Qwen3 AISHELL-1 1.48% 在 FireRedASR2S 平台，SenseVoice AISHELL-1 ~3.0% 在自己仓库）。不同测试条件（音频预处理、解码参数、评估脚本）下的分数不能直接比大小。

### 能确定的事（有直接对比数据支撑）

```
中文准确率 vs Whisper（各自独立验证，方向一致）：
Qwen3-ASR >> Whisper（AISHELL-1: 1.48% vs 5.14%, FireRedASR2S 统一平台）
SenseVoice >> Whisper（CommonVoice 中文: 10.78% vs 12.55% CER, SenseVoice 论文）

中文噪音鲁棒性 vs Whisper：
Qwen3-ASR >> Whisper（低信噪比: 16.17% vs 63.17% WER, Qwen3 官方）
SenseVoice >> Whisper（高噪直播: 6.1% vs 18.5% CER, CSDN 独立测试）

中文方言覆盖：
Qwen3-ASR (22种) >> SenseVoice (1种) = Whisper (1种，弱)

推理速度：
SenseVoice (70ms) > Qwen3-ASR 0.6B > Qwen3-ASR 1.7B >> Whisper (1281ms)
```

### 不能确定的事（需要自己实测）

```
Qwen3-ASR vs SenseVoice 中文准确率 —— 无直接对比数据
Qwen3-ASR vs SenseVoice 粤语准确率 —— 无直接对比数据
Qwen3-ASR 在你的视频类型上的实际表现 —— 未测试
```

### 一句话总结每个模型的定位：

| 模型 | 定位 |
|------|------|
| **SenseVoice** | 最快最轻的中文/粤语 ASR + 独家的情感/事件检测。做分类器无可替代。做中文转录比 Whisper 准但不如 Qwen3。 |
| **Qwen3-ASR** | 中文准确率天花板。22 种方言、噪音鲁棒性极强。但最重（4-14GB 显存），且没有音频事件检测。 |
| **Whisper** | 99 语言的通用引擎。英文最好，中文最弱。生态碾压。做多语言视频的最后防线。 |

---

## 九、对 video-subtitle 项目的实际影响

### 9.1 关键纠正：产品是"外语→中文翻译"，不是"中文转录"

**之前的分析聚焦在中文转录准确率上——但产品的实际场景是把英文/日文/韩文等外语视频转录后翻译成中文。** 这意味着应该比的是各模型在**源语言**上的转录准确率，而非中文准确率。

### 9.2 三模型逐语言对比（SenseVoice vs Qwen3-ASR vs Whisper Large V3）

*（2026-06-11 更新：基于联网搜索的最新可用数据）*

#### 9.2a CommonVoice 五语言（SenseVoice 论文，同平台直接可比）

| 语言 | SenseVoice Small | Whisper Large V3 | 🥇 已开源最佳 |
|---|---|---|---|
| **中文** | 10.78% CER | 12.55% | SenseVoice |
| **英语** | 14.71% WER | **9.39%** | Whisper |
| **日语** | 11.96% CER | **10.34%** | Whisper |
| **韩语** | 8.28% CER | **5.59%** | Whisper |
| **粤语** | **7.09%** CER | 10.41% | SenseVoice |

> 数据来源：FunAudioLLM/SenseVoice 论文 (arXiv:2407.04051) Table 6。同一测试条件。Qwen3-ASR 不在这张表里。
> SenseVoice Large（五语言全胜）未开源。

#### 9.2b 英文详细数据（Qwen3 vs Whisper，Qwen3 官方同平台）

| 基准 | Qwen3-ASR 1.7B | Whisper Large V3 | 🥇 |
|---|---|---|---|
| CommonVoice en | **7.39%** | 9.90% | Qwen3 |
| GigaSpeech（真实场景） | **8.45%** | 9.76% | Qwen3 |
| Tedlium | **4.50%** | 6.84% | Qwen3 |
| LibriSpeech other（噪音） | **3.38%** | 3.97% | Qwen3 |
| LibriSpeech clean（录音棚） | 1.63% | **1.51%** | Whisper |
| 极端噪音 | **16.17%** | 63.17% | Qwen3 |

> 数据来源：qwen-ai.com/qwen-asr/#benchmarks。同一测试条件。五中四胜。

#### 9.2c 中文详细数据（Qwen3 vs Whisper，Qwen3 官方同平台）

| 基准 | Qwen3-ASR 1.7B | Whisper Large V3 | 🥇 |
|---|---|---|---|
| WenetSpeech net | **4.97%** | 9.86% | Qwen3 |
| WenetSpeech meeting | **5.88%** | 19.11% | Qwen3 |
| AISHELL-2 | **2.71%** | — | Qwen3 |
| AISHELL-1（FireRedASR2S 统一平台）¹ | **1.48%** | 5.14% | Qwen3 |

> ¹ FireRedASR2S 平台也测了 SenseVoice AISHELL-1 ~3.0%，但来自自身仓库（非同一测试条件）。三个数字旁注仅供参考：Qwen3 1.48%，SenseVoice ~3.0%，Whisper 5.14%。

#### 9.2d 三模型逐语言总表（已开源可用）

| 语言 | 🥇 最佳 | 🥈 第二 | 🥉 第三 | 可信度 | 备注 |
|---|---|---|---|---|---|
| **英语** | **Qwen3-ASR** | Whisper | SenseVoice | ✅ 高 | Qwen3 真实场景优于 Whisper，录音棚朗读略输 |
| **中文** | **Qwen3-ASR** | SenseVoice | Whisper | ✅ 高 | 但产品不转录中文 |
| **日语** | **Whisper** | SenseVoice | Qwen3 ❓ | ⚠️ 中 | Whisper 10.34% vs SenseVoice 11.96%（同平台）。Qwen3 无单项数据——Fleurs 总平均（4.90 vs 5.27）暗示 Qwen3 日语可能不弱 |
| **韩语** | **Whisper** | SenseVoice | Qwen3 ❓ | ⚠️ 中 | Whisper 5.59% vs SenseVoice 8.28%（同平台）。Qwen3 无单项数据 |
| **粤语** | **SenseVoice** | Whisper | Qwen3 ❓ | ✅ 高 | SenseVoice 7.09% vs Whisper 10.41% |
| **俄语** | ❓ | ❓ | ❓ | ❌ 无数据 | 三模型都宣称支持俄语，都没有俄语单项 benchmark |

#### 9.2e 新方案叠加（Andy 提出，2026-06-11）

| 源语言 | 主引擎 | 副引擎 | 依据 |
|---|---|---|---|
| **英语** | Qwen3-ASR | Whisper | Qwen3 真实场景五中四胜，噪音 4× 优于 Whisper |
| **日语** | Whisper | Qwen3-ASR² | Whisper 有同平台数据确认为优于 SenseVoice；Qwen3 无数据但总平均优 |
| **韩语** | Whisper | Qwen3-ASR² | Wisper 同平台确认为优于 SenseVoice |
| **粤语** | SenseVoice Small | — | 同平台优于 Whisper 32% |
| **长尾小语种** | Whisper | — | Fleurs 30 语言 Whisper 8.16% vs Qwen3 12.60% |

> ² Qwen3-ASR 日语韩语单项数据缺失——建议用户在自己的测试视频上实测后再决定是否翻转主次。

### 9.3 未来方案：基于源语言的智能路由

*（2026-06-11 讨论提出，待 V3.08+ 实施）*

**核心思路（Andy 提出）：** 语言检测 → 分支路由。主流语言 Qwen3-ASR 主 + Whisper 副。长尾语言 Whisper 主。SenseVoice 全程分类。

```
音频输入
    │
    ▼
语言检测（已有）
    │
    ├─ 英语/日语/韩语 → Qwen3-ASR 主引擎
    │       ├─ Qwen3 转录 → 主字幕
    │       ├─ Whisper 转录 → 副字幕（校准用）
    │       └─ 交叉比对 → 标记分歧 → 过滤器裁决
    │
    ├─ 长尾小语种 → Whisper 主引擎
    │       └─ Whisper 转录 → 主字幕（无副字幕）
    │
    └─ 所有语言 → SenseVoice 分类（不变）
            └─ 音频事件标签 → L1 双信号交叉验证
```

**内存成本：零。** precise tier 已经在同时加载和跑 Qwen3 + Whisper，只改主次顺序——谁的输出写入主字幕 track。standard/fast tier 保持不变。

**按 tier 分阶段实施：**

```
precise tier（≥16GB，已跑 Qwen3）：
  → 主流语言：Qwen3 主 + Whisper 副（只改主次赋值）
  → 长尾语言：Whisper 主（不变）

standard tier（8-15GB，无 Qwen3）：
  → 不变。Whisper 主引擎。

fast tier（<8GB）：
  → 不变。Whisper only。
```

**好处：**

1. 英语场景从 Whisper 主（CommonVoice 9.90% WER）→ Qwen3 主（7.39%），降 25% 错误率。噪音场景差距更大（16.17% vs 63.17%）
2. 不增内存、不增模型、只改一行路由逻辑
3. 长尾语言不受影响，Whisper 99 语言优势保留
4. SenseVoice 分类角色不变——跨语言通用

### 9.4 乔布斯的判断

> "你的产品不做中文转录。你的产品是把英文/日文/韩文转录成文字，然后翻译成中文。两个完全不同的赛道。
>
> 在英语上——你最大的场景——Qwen3-ASR 在真实世界场景上优于 Whisper。日语韩语 SenseVoice Large 最好但你拿不到，Whisper 仍是已开源最强。
>
> 你的 precise tier 已经在跑 Qwen3-ASR 了——它转录出来的结果比 Whisper 更准，现在只当验证信号。把主次换过来：Qwen3 输出写进主字幕，Whisper 输出写进副字幕做校准。一行代码。零成本。
>
> Whisper 不是要被换掉——它是长尾语言的安全网。99 种语言，Qwen3 和 SenseVoice 加起来都覆盖不到。但它不该在你最大场景上做主引擎。让每个模型做它最擅长的事。"

---

## 十、数据来源

| # | 来源 | 类型 | 覆盖模型 |
|---|------|------|:---:|
| 1 | [腾讯云: 中文语音识别该用谁？6 个开源模型一文理清](https://cloud.tencent.cn/developer/article/2642961) | 统一基准 | Qwen3, SenseVoice, Whisper |
| 2 | [Qwen3-ASR 官方 Benchmarks](https://qwen-ai.com/qwen-asr/#benchmarks) | 官方 | Qwen3, Whisper |
| 3 | [FunAudioLLM/SenseVoice GitHub](https://github.com/FunAudioLLM/SenseVoice) | 官方 | SenseVoice |
| 4 | [SenseVoiceSmall HuggingFace](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) | 官方 | SenseVoice |
| 5 | [FunAudioLLM 论文 (arXiv:2407.04051)](https://arxiv.org/abs/2407.04051) | 学术 | SenseVoice, Whisper |
| 6 | [Whisper Notes: SenseVoice 速度实测](https://whispernotes.app/blog/sensevoice-fastest-cjk-transcription) | 独立 | SenseVoice, Whisper |
| 7 | [AlienKevin/cantonese_asr_eval](https://github.com/AlienKevin/cantonese_asr_eval) | 独立 | SenseVoice, Whisper |
| 8 | [CSDN: Qwen3-ASR 性能实测](https://blog.csdn.net/weixin_35749786/article/details/157783196) | 社区 | Qwen3 |
| 9 | [Qwen3-ASR HuggingFace](https://huggingface.co/hdnminh/qwen3-asr-1.7b) | 官方 | Qwen3 |
| 10 | [什么值得买: ASR 模型深度评测](https://post.smzdm.com/p/aog80p6m/) | 社区 | Qwen3, Whisper |
| 11 | CSDN: SenseVoice vs Whisper 中文实测 | 社区 | SenseVoice, Whisper |
| 12 | CSDN: 游戏直播极限噪音测试 | 社区 | SenseVoice, Whisper |
| 13 | [掘金: Qwen3-ASR 开源，Whisper 迎来最强对手](https://juejin.cn/post/7600994087588773907) | 技术社区 | Qwen3, Whisper |
| 14 | [Hacker News: SenseVoice 讨论](https://news.ycombinator.com/item?id=41825792) | 社区 | SenseVoice, Whisper |
| 15 | [LINUX DO: Whisper 中文调优](https://linux.do/t/topic/811273) | 中文社区 | SenseVoice, Whisper |

---

> **下次更新时机：** 用项目自己的测试视频跑一轮三模型对比、或 Qwen3-ASR 2.0 发布时。
