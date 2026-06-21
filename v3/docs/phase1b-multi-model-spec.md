# Phase 1B 技术规格：三模型转录 + 硬件调度

## 1. 文件结构

```
v2/backend/
├── core/
│   ├── hallucination_filter.py   # ✅ 已完成（TDD，35 测试）
│   ├── sensevoice_runner.py      # 新增：SenseVoice 声音分类
│   ├── qwen_asr_runner.py        # 新增：Qwen3-ASR 交叉验证
│   └── hardware_detector.py      # 新增：硬件检测 + 三档调度
├── transcriber_v2.py             # 改动：集成多模型流水线
├── worker.py                     # 改动：新增 transcriptionTier 配置
├── env_check.py                  # 改动：新增 SenseVoice/Qwen3-ASR 检测
└── model_manager.py              # 改动：新增两个模型的下载管理
```

## 2. 三档硬件调度

### 2.1 硬件自动检测 (`hardware_detector.py`)

```python
def detect_hardware() -> dict:
    """返回 {platform, has_gpu, gpu_type, ram_gb, recommended_tier}"""
```

| 检测项 | 方法 | 用途 |
|--------|------|------|
| 平台 | `sys.platform` + `platform.machine()` | 区分 Mac/Win/Linux |
| Apple Silicon | `platform.machine() == "arm64"` + `sys.platform == "darwin"` | MLX 可用性 |
| CUDA GPU | `torch.cuda.is_available()` | GPU 加速 |
| RAM | `psutil.virtual_memory().total` (无 psutil 则读 `/proc/meminfo` 或 `sysctl hw.memsize`) | 内存够不够跑多模型 |

### 2.2 三档定义

| 档位 | 模型 | 内存需求 | 推荐条件 | 前端标签 |
|------|------|---------|---------|---------|
| `fast` | Whisper only | ~3GB | RAM < 8GB 或用户选择 | 快速 |
| `standard` | Whisper → SenseVoice | ~4GB | 8GB ≤ RAM < 16GB | 标准（推荐） |
| `precise` | Whisper → SenseVoice → Qwen3-ASR | ~7GB | RAM ≥ 16GB | 精确 |

### 2.3 推荐逻辑

```python
def recommend_tier(hw: dict) -> str:
    ram = hw["ram_gb"]
    if ram < 8:
        return "fast"
    if ram >= 16:
        return "precise"
    return "standard"
```

用户可在前端覆盖推荐，选择任意档位。选了比推荐高的档位时，前端显示黄色提示："内存可能不足，处理速度会变慢"。

### 2.4 降级策略

模型加载失败时自动降级，不中断流水线：

```
precise → standard → fast（始终可用，Whisper 是必装项）
```

降级时 `_log.warning` 记录原因，前端进度条更新提示文字。

## 3. 模型管理

### 3.1 模型下载时机

| 模型 | 下载时机 | 存储位置 |
|------|---------|---------|
| Whisper | 用户在前端选择模型点时触发（已有逻辑） | `~/.cache/huggingface/hub/` |
| SenseVoice | 首次使用 standard/precise 档位时自动下载 | funasr 自管理（`~/.cache/modelscope/`） |
| Qwen3-ASR | 首次使用 precise 档位时自动下载 | Mac: `~/.cache/huggingface/hub/` (MLX) / Windows: `~/.cache/huggingface/hub/` (PyTorch) |

### 3.1.1 跨平台打包策略

模型权重文件**不打包进安装包**（太大：Whisper 3GB + SenseVoice 0.8GB + Qwen3-ASR 3.4GB = 7.2GB）。用户首次使用时自动下载到 `~/.cache/`。

| 平台 | Qwen3-ASR 包 | 说明 |
|------|-------------|------|
| Mac Apple Silicon | `qwen3-asr-mlx` (MLX) | Metal 加速，无需 CUDA |
| Windows NVIDIA | `qwen-asr` (PyTorch + CUDA) | GPU 加速 |
| Windows CPU / Linux | `qwen-asr` (PyTorch + CPU) | 慢但能跑 |

SenseVoice 的 `funasr` 全平台通用包，无需区分。安装包只需预置下载逻辑 + 网络检测，首次使用前引导用户联网下载模型。

### 3.1.2 Qwen3-ForcedAligner（V4 才用，Phase 1B 不上）

Qwen3-ASR 和 Qwen3-ForcedAligner 是**两个独立模型**，同属千问家族但功能不同：

| 模型 | 用途 | 大小 | 计划版本 |
|------|------|------|---------|
| Qwen3-ASR 1.7B | 语音 → 文字（转录） | ~3.4GB | Phase 1B |
| Qwen3-ForcedAligner 0.6B | 音频 + 文字 → 词级时间戳 | ~1.2GB | V4 |

Phase 1B 只用 Qwen3-ASR 做交叉验证（对比 Whisper 和 Qwen3-ASR 的转录结果）。词级时间戳对齐留给 V4——V4 规划的前置条件是"V3 LLM 断句上线后实测，若断句未解决 90% 字幕错位则提前到 V3 Phase 1C"。当前时间戳仍以 Whisper 的词级时间戳为准。

下载进度通过 worker → WebSocket → 前端推送。

### 3.2 内存管理（用完即释放）

```python
# 每个 runner 内部：
model = load_model()
result = model.process(audio)
del model
gc.collect()
# 如果有 torch: torch.cuda.empty_cache()
```

峰值内存 = 最大的单个模型（~3.4GB Qwen3-ASR），不是三个叠加。

### 3.3 模型加载失败降级

```python
try:
    sensevoice_results = run_sensevoice(audio_path)
except (ImportError, Exception) as e:
    _log.warning("SenseVoice 不可用: %s，跳过声音分类", e)
    sensevoice_results = None  # 后续层跳过
```

## 4. 多模型转录流水线

### 4.1 执行顺序（串行，用完释放）

```
Step 1: Whisper 转录 → 得到 segments（含词级时间戳）→ del whisper_model
Step 2: SenseVoice 分类 → 对每个 segment 的音频片段分类 → 写入 segment.sensevoice_tag → del sensevoice_model
Step 3: Qwen3-ASR 验证 → 对整段音频转录 → 按时间戳对齐写入 segment.qwen_text → del qwen_model
Step 4: 五层幻觉过滤 → filter_hallucinations(segments, source_lang)
Step 5: 说话人检测（如果开启）
```

### 4.2 SenseVoice Runner (`sensevoice_runner.py`)

```python
def classify_segments(audio_path: str, segments: list) -> list:
    """对每个 segment 的音频区间运行 SenseVoice，写入 sensevoice_tag。
    
    实现策略：用整段音频一次 generate()，SenseVoice 内置 VAD 自动分段，
    然后按时间戳匹配到 Whisper segments。
    """
```

**输出映射**：
- SenseVoice 输出 `"<|zh|><|NEUTRAL|><|Speech|>内容"`
- 用正则 `r"<\|(\w+)\|>"` 提取第三个标签（事件类型）
- 写入 `segment.sensevoice_tag = "Speech"` / `"BGM"` / `"Laughter"` 等
- 匹配策略：SenseVoice 段的时间范围与 Whisper segment 有 ≥50% 重叠 → 匹配

### 4.3 Qwen3-ASR Runner (`qwen_asr_runner.py`)

```python
def cross_validate(audio_path: str, segments: list) -> list:
    """用 Qwen3-ASR 转录整段音频，按时间对齐写入 segment.qwen_text。"""
```

**实现策略**：
1. Qwen3-ASR 转录整段音频，得到完整文本
2. 因为 MLX 版本不返回时间戳，用 Whisper 的时间戳做切分参考
3. 将 Qwen3-ASR 的整段文本按 Whisper 的段数等比例切分
4. 写入 `segment.qwen_text`

**备选方案（如果等比切分效果差）**：按句号/逗号等标点切分 Qwen3-ASR 文本，再用文本相似度匹配到最近的 Whisper segment。

### 4.4 时间戳对齐策略

**以 Whisper 时间戳为准**。原因：
- Whisper 有词级时间戳（`word_timestamps=True`），精度最高
- SenseVoice 只用于分类，不提供时间戳
- Qwen3-ASR MLX 版本不提供时间戳
- V4 的 Qwen3-ForcedAligner 才会重新生成时间戳

## 5. 与现有代码的集成

### 5.1 `transcriber_v2.py` 改动

```python
def transcribe(audio_path, model_size="medium", source_lang="auto",
               ...,
               transcription_tier="standard",  # 新增参数
               progress_callback=None) -> list:
```

改动点：
1. 新增 `transcription_tier` 参数（"fast" / "standard" / "precise"）
2. Whisper 转录后，根据 tier 调用 SenseVoice / Qwen3-ASR
3. 将 `_is_hallucination()` 和 `_dedup_hallucination_chains()` 替换为 `filter_hallucinations()`
4. 保留 `_detect_speaker_changes()` 不动

### 5.2 `worker.py` 改动

```python
# config 新增字段
transcription_tier = config.get("transcriptionTier", "standard")

# 传给 transcribe_v2()
segments = transcribe_v2(
    audio_path, model_size=model,
    ...,
    transcription_tier=transcription_tier,
)
```

`SETTING_STAGE` 新增：`"transcriptionTier": "transcribe"`
`_validate_config` 新增：`transcriptionTier` 只接受 `("fast", "standard", "precise")`

### 5.3 `env_check.py` 改动

```python
def check_sensevoice() -> dict:
    """检查 funasr + SenseVoice 模型是否可用。"""
    try:
        import funasr
        return {"ready": True, "version": funasr.__version__}
    except ImportError:
        return {"ready": False}

def check_qwen_asr() -> dict:
    """检查 qwen3-asr-mlx 是否可用。"""
    try:
        from qwen3_asr_mlx import Qwen3ASR
        return {"ready": True}
    except ImportError:
        return {"ready": False}
```

`check_all()` 返回值新增 `sensevoice` 和 `qwen_asr` 两项。

### 5.4 `main.py` 改动

- `/api/env` 返回新增两项检测结果
- `/api/hardware` 新增端点，返回硬件检测 + 推荐档位
- `_config_hash` 新增 `transcriptionTier`

### 5.5 前端改动

模型选择区域下方新增「转录模式」三选一：

```
● 快速（仅 Whisper）
● 标准（Whisper + 声音分类）← 推荐
● 精确（三模型交叉验证）
```

环境栏新增两项：
- SenseVoice 声音分类：绿/红
- Qwen3-ASR 交叉验证：绿/红

## 6. 接受标准

1. `transcription_tier="fast"` 行为与当前完全一致（向后兼容）
2. `transcription_tier="standard"` 运行 Whisper + SenseVoice，BGM/Laughter 段被 L1 硬过滤丢弃
3. `transcription_tier="precise"` 运行全部三模型，Whisper 和 Qwen3-ASR 文本差异大的段标记 `uncertain=True`
4. funasr 未安装时，standard/precise 自动降级为 fast，进度条提示"SenseVoice 不可用"
5. qwen3-asr-mlx 未安装时，precise 自动降级为 standard
6. 每个模型用完 `del model + gc.collect()`，峰值内存不超过单个最大模型
7. 全部 35 个幻觉过滤测试仍然通过
8. 前端三档选择可用，环境栏显示新模型状态
