# Video Subber V1.2 — 改动说明

**版本基础**：从 V1.1 复制。V1.2 在 V1.1 基础上实现 RMS 三特征双信号交叉验证（P025）。

**改动主线**：修复 P025 / P026。

---

## V1.1 中遇到的问题

### 问题 1：no_speech_prob 单信号不可靠（P025）

V1.1 的 L1 过滤完全依赖 Whisper 自己的 `no_speech_prob`。核心矛盾：Whisper 出题（转录）、答卷（输出文字）、打分（给置信度），三个环节都是它自己。同一个实体同时担任被告和法官。

**具体表现**：
- **片头 5 秒音乐**：不管怎么调阈值，要么把音乐当人声（字幕从 0s 覆盖片头），要么连带着后面真正的对话全部过滤掉（片头空白几十秒）
- **不同模型的 nsp 标度不统一**：tiny 的 0.5 和 large-v3 的 0.5 不代表同一件事
- **nsp 分布在视频内部漂移**：片头有音乐所以 nsp 偏高，中间纯对话 nsp 偏低，单阈值无法覆盖

### 问题 2：per-model 校准在完整视频上失效（P026）

用 3 分钟校准片段跑 5 个模型，算出 per-model nsp 阈值（tiny=0.52, base=0.62, small=0.79, medium=0.95, large-v3=0.18）。但应用到 25 分钟完整视频后全部失效：

| 模型 | 校准阈值 | 25 分钟视频表现 |
|------|---------|---------------|
| tiny | 0.52 | 过滤 74 段，1:50-3:40 空白 |
| base | 0.62 | 过滤 6 段，最接近默认 0.6，效果最好 |
| small | 0.79 | 过滤 0 段，前 40 秒无字幕 |
| medium | 0.95 | 过滤 0 段，前 20 秒无字幕 |
| large-v3 | 0.18 | 过滤 52 段，片头出字幕+间歇空白 |

**根因**：3 分钟校准片段没有片头音乐（第一条就是对话），校准出来的阈值无法预判片头音乐段的 nsp 行为。全用 0.6 反而最稳。

### 问题 3：校准样本不足以代表完整视频

`calibration_raw.json` 显示：3 分钟片段中所有模型的 nsp 值都很低（tiny=0.13, base=0.24, small=0.16, medium=0.54, large-v3=0.04）——因为片段里根本没有片头音乐。用简单样本校准的阈值套到复杂样本上，必然出错。

---

## 解决方案探索过程

### 方向 1：继续调 per-model 阈值 → 放弃

尝试了 tiny(0.52)→base(0.62)→small(0.79)→medium(0.95)→large-v3(0.18)，结论：单信号 + per-model 阈值是死胡同。nsp 的标度在不同模型间不可比，在不同视频段落间漂移。

### 方向 2：全部用 0.6 → 部分改善但不够

P026 结论：全用 0.6 最稳。但片头问题仍未解决——0.6 对 base/large-v3 太松（片头音乐当人声），对 small/medium 又不够（片头全空白）。

### 方向 3：固定时间窗口（Jobs 建议）→ 有启发但不完整

前 30 秒不过滤——保证用户看到字幕。但固定 30 秒对音乐只有 5 秒的视频是浪费，对音乐有 60 秒的视频不够。

### 方向 4：物理信号检测（Musk 建议）→ 核心方向

用 RMS 能量方差找音乐→语音的切换点。纯物理测量，不依赖任何 AI 模型。

### 方向 5：三特征多数投票（最终方案）→ 采纳

综合三个物理特征（RMS 能量方差 + 频谱质心均值 + 零交叉率均值），多数投票判"像音乐还是像说话"。与 Whisper nsp 交叉验证。

**核心原则**：
- **法官不应该是被告**：物理信号不认识 Whisper，不关心 Whisper，Whisper 不存在于它们的物理世界里
- **失败模式从"静默丢弃"变成"用户可见的 [?]"**：阈值错了不会丢字幕，只是多一个标记

---

## V1.2 新增功能详细列表

### 1. RMS 三特征双信号交叉验证（L1 替换）

**修改文件**：`video_subber/transcriber.py`

**A. `_is_music_like(y, sr)` 函数**（transcriber.py 第 34-70 行）

纯信号处理，无 AI，< 1ms/次。三个独立物理测量：

| 特征 | 测什么 | 说话 | 音乐 | 阈值 |
|------|--------|------|------|------|
| RMS 能量方差 | 响度稳不稳 | 跳来跳去（高方差）| 平稳（低方差）| < 0.005 |
| 频谱质心均值 | 缺不缺高频辅音 | 辅音高，质心高 | 缺辅音，质心低 | < 1200 Hz |
| 零交叉率均值 | 有没有辅音摩擦 | 辅音多，ZCR 高 | 无摩擦，ZCR 低 | < 0.04 |

≥ 2 票判"像音乐" → 返回 True。3 票中允许 1 票出错，有冗余。

**B. L1 双信号交叉逻辑**（替换旧版 `nsp > threshold → drop`）

```
nsp > 0.6 AND _is_music_like() = True   → 丢弃（双方一致）
nsp > 0.6 AND _is_music_like() = False  → 保留，标记 [?]（Whisper 说非人声，物理说像说话）
nsp ≤ 0.6 AND _is_music_like() = True   → 保留，标记 [?]（Whisper 说是人声，物理说像音乐）
nsp ≤ 0.6 AND _is_music_like() = False  → 保留（双方一致说是人声）
```

四个分支全部可达（初始版本曾有一个性能优化 bug，只在 nsp_high 时才调 `_is_music_like`，导致 not nsp_high and music_like 分支不可达——已修复）。

**C. 完整音频加载**

使用 `wave` 标准库加载 ffmpeg 输出的 16kHz mono 16-bit PCM WAV 到内存（25 分钟视频约 48MB），供 `_is_music_like` 按段切片。零新依赖。加载失败时降级为纯 nsp 单信号过滤。

**D. disputed 计数**

新增 `disputed` 计数器，统计双信号意见冲突的段数。终端输出和 debug 日志均包含。

### 2. `[?]` 不确定标记（字幕渲染）

**修改文件**：`video_subber/subtitler.py`

**A. `_annotate_speaker_changes()` 扩展**

函数从仅处理 `speaker_change` 扩展为同时处理 `speaker_change` 和 `uncertain`：

```python
if changed:
    t = f"- {t}"        # 说话人切换 → 行首加 " - "
if unc:
    t = f"{t} [ ? ]"    # 不确定 → 行尾加 " [ ? ]"
```

**B. `_collect_entries()` 扩展**

每个 sub-segment 继承父 segment 的 `speaker_change` 和 `uncertain` 标记。

### 3. nsp 阈值策略回退

**修改文件**：`video_subber/transcriber.py`

删除 per-model 校准字典 `_calibrated_nsp`。所有模型统一 `nsp=0.6`（P026 结论）。用户显式传 `--no-speech-threshold` 可覆盖。

### 4. `Segment.uncertain` 字段

**修改文件**：`video_subber/transcriber.py`

Segment 类新增 `uncertain: bool = False` 字段。用于在过滤→翻译→字幕渲染流水线中传递不确定性标记。

---

## 代码改动清单

| 文件 | 改动内容 | 行数变化 |
|------|---------|---------|
| `video_subber/transcriber.py` | `_is_music_like()` 函数（已存在，V1.2 开始调用） | 已存在 |
| `video_subber/transcriber.py` | `import wave` + 音频加载 | +14 行 |
| `video_subber/transcriber.py` | L1 双信号交叉逻辑（替换旧 L1） | +20 行 |
| `video_subber/transcriber.py` | `disputed` 计数器 + 统计输出 | +5 行 |
| `video_subber/transcriber.py` | 删除 per-model 校准字典 | -3 行 |
| `video_subber/subtitler.py` | `_annotate_speaker_changes()` 处理 `uncertain` | +3 行 |
| `video_subber/subtitler.py` | `_collect_entries()` 解包 `uncertain` | +1 行 |

**总改动量**：约 40 行净增代码。不动 L2/L4/L5、不动翻译、不动说话人检测。

---

## 尚未完成

| 任务 | 说明 |
|------|------|
| RMS 特征阈值校准（P029） | 用六段素材（3 段纯音乐 + 3 段纯对话）校准三个物理特征的阈值到最坏情况分界线 |
| 频谱质心第三信号扩展（P027） | 武打场景兜底——区分打斗音效和人声 |
| 实际视频验证 | 用 25 分钟俄语视频跑 V1.2，验证 `[?]` 标记的准确率和覆盖率 |

---

## V1.1 → V1.2 核心决策

1. **不修参数，改架构**：V1.1 在 nsp 单信号上反复调参（per-model 阈值、校准片段、avg_logprob），到达了单信号的极限。V1.2 引入第二个独立信号从根本上改变了过滤的可靠性。
2. **物理信号优于模型自评**：RMS/频谱质心/ZCR 是客观测量，不随模型版本变化，不需要 per-model 校准。
3. **故障模式升级**：从"静默丢弃→用户不知道"变为"不确定→用户看到 [?]"。宁可多出带标记的字幕，不可空白。
4. **零新依赖**：wave 标准库 + librosa（已在用，resemblyzer 依赖）+ numpy（已在用）。
