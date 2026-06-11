# 依赖版本冲突问题 — tokenizers 版本夹逼

## 项目背景

一个桌面端视频字幕生成工具（Python 3.13 + FastAPI + PyTorch/MLX）。核心流程：

1. 下载视频（yt-dlp）
2. Whisper 转录（faster-whisper）
3. Qwen ASR 交叉验证（转录第二份结果，与 Whisper 比对过滤幻觉）
4. FA 词级对齐（拿 Whisper 文本去音频里找到每个词的精确时间戳）
5. SenseVoice 声音分类（判断每段是人声/音乐/噪音）
6. 三层信号交叉验证过滤（L1 音频信号 + L2 文本比对 + L4 关键词）
7. LLM 翻译 + 字幕烧录

其中步骤 3（Qwen ASR）和步骤 4（FA 对齐器）是两个独立的模型，互不调用对方，但共用同一个 Python 虚拟环境和依赖树。

## 涉及的两个模型及其依赖

### Qwen ASR（Qwen3-ASR-1.7B）
- 功能：把音频转成文字，给 Whisper 做交叉验证
- Mac 路径：通过 `qwen3-asr-mlx` 包加载，使用苹果芯片的 MLX 框架加速
- Windows 路径：通过 `qwen-asr` 包加载，使用 PyTorch + CUDA
- 代码在 `qwen_asr_runner.py`，通过检测 `qwen3-asr-mlx` 是否可导入来决定走哪条路

### FA 对齐器（Qwen3-ForcedAligner-0.6B）
- 功能：接收 Whisper 转录的文字 + 音频，输出每个词的精确起止时间（如"hello"在 0.32s-1.44s）
- 通过 `qwen-asr` 包中的 `Qwen3ForcedAligner` 类加载
- 代码在 `qwen_aligner.py`，启动前通过 `is_qwen_aligner_available()` 检测可用性
- 该检测函数调用 `transformers.AutoConfig.from_pretrained()` 来验证模型文件是否已缓存

## 问题时间线

### 最初状态（项目搭建时）
- `tokenizers==0.23.1`（系统自动安装的最新版）
- Qwen ASR 走 MLX 路径，正常工作
- FA 对齐器为旧版手写实现（用 AutoModel 裸加载，forward pass 失败后静默降级到假数据）

### P207 修复（6月9日上午）
- 将 FA 对齐器从旧版手写实现替换为官方 `Qwen3ForcedAligner` API
- 为此安装了 `qwen-asr==0.0.6`（官方包，包含 ForcedAligner 类）
- 安装过程中，依赖解析将 `tokenizers` 从 0.23.1 降级到 0.22.2
- 副作用：`qwen3-asr-mlx` 被清除（它从未被写入依赖清单，作为孤儿包被清理）

### P226 修复（6月9日下午，几个小时前）
- 发现问题：Qwen ASR 的 MLX 路径丢失，Qwen 在备用路径上产生了多个连锁 bug
- 修复方案：重新安装 `qwen3-asr-mlx==0.1.1` 并写入 `pyproject.toml`
- 副作用：`qwen3-asr-mlx==0.1.1` 要求 `tokenizers>=0.23.0`，pip 自动将 tokenizers 从 0.22.2 升级到 0.23.1

### 当前问题（P227）
- FA 对齐器的 `is_qwen_aligner_available()` 调用 `AutoConfig.from_pretrained()`，内部触发版本检查
- transformers 库要求 `tokenizers>=0.22.0,<=0.23.0`
- 当前 tokenizers==0.23.1 超出了 ≤0.23.0 上限
- 版本检查抛出 `ImportError`，被 `except (ImportError, OSError)` 捕获
- `is_qwen_aligner_available()` 返回 False，FA 对齐器静默跳过，不报错

## 核心矛盾

```
qwen3-asr-mlx==0.1.1 要求 tokenizers >= 0.23.0
transformers (FA 对齐器) 要求 tokenizers >= 0.22.0, <= 0.23.0
```

两个区间唯一的交集版本是 **tokenizers==0.23.0**。但该版本在 PyPI 上从未发布——版本号从 0.22.2 直接跳到了 0.23.1。

## 已验证的替代方案

### 方案 A：降级 qwen3-asr-mlx（推荐）
- 使用 `qwen3-asr-mlx==0.1.0` 替代 0.1.1
- 0.1.0 的 tokenizers 要求仅为 `>=0.15.0`（远低于 0.1.1 的 `>=0.23.0`）
- 配合 `tokenizers==0.22.2` 锁死，同时满足 MLX 和 FA 两边
- 零代码修改，仅调整依赖版本号
- 0.1.0 和 0.1.1 的功能差异：0.1.1 新增了对 tokenizers 0.23.x 的特定优化，但对本项目功能无实际影响

### 方案 B：修改 FA 可用性检测逻辑
- 修改 `is_qwen_aligner_available()`，不通过 `AutoConfig.from_pretrained()` 校验版本
- 改为直接检查模型文件是否存在于本地缓存（`os.path.exists` 或 `huggingface_hub` 的本地文件检查）
- 可彻底避免 tokenizers 版本耦合，但需要改动代码
- 风险：FA 模型实际加载时可能遇到相同的版本检查问题

### 方案 C：升级 transformers
- `transformers==5.x`（当前为 4.57.6）可能放宽了 tokenizers 上限
- 大版本跳跃（4.x→5.x），其他依赖可能不兼容
- 未验证

## 深层问题

这个案例暴露了一个系统性问题：项目依赖清单中的版本号没有全面锁死。每次 `pip install` 的副作用都可能触发连锁反应——今天修模型 A 的 bug，安装了新包，模型 B 的版本检查被新包的随从依赖顶穿。

项目的 `pyproject.toml` 中大部分依赖使用了 `>=`（下限约束）而非 `==`（精确锁定），导致 pip 在解析依赖树时有自由度选择"最新但可能不兼容"的版本。对于涉及多个独立 AI 模型、共享同一个 Python 环境的项目，这种自由度是有害的。

## 环境信息
- Python 3.13.13
- 操作系统：macOS (Apple Silicon M-series)
- 包管理器：uv pip
- 相关包当前版本：`qwen-asr==0.0.6`, `qwen3-asr-mlx==0.1.1`, `transformers==4.57.6`, `tokenizers==0.23.1`
