# 设计文档：P275 人声时间轴 + 标准/精确模式重构

日期：2026-06-16
分支：`l1-voice-timeline-and-standard-mode`
状态：已实现（2026-06-16 第二轮修订）

## 最终决策（2026-06-16 确认）

1. **标准模式永远只用千问做 ASR 引擎**，不管什么语言。千问支持 52 种语言，标准模式够用。
2. **L1 交叉验证永远是 SenseVoice × FA**，平台无关。Mac 上 FA 是千问内置的，Windows 上是外部 FA 模型。L1 的代码逻辑完全一样。
3. **精确模式加载 Whisper 做双引擎交叉验证**，标准模式不加载 Whisper。
4. **不需要提前做语言检测来选引擎**——标准模式永远用千问，千问自带语言自动检测。
5. **P275 人声地图**：利用 SenseVoice 已有的全片扫描结果整理成 voice_regions/non_voice_regions，无额外开销。

## 一、要解决什么问题

### P275：L1 不知道哪里有人声、哪里没有

现在 L1 只能检查"已有字幕段"上有没有问题。比如一段音乐区间没有字幕，L1 不会报告"这里漏了字幕"——因为它根本不知道哪些区间有人声、哪些没有。

SenseVoice 模型已经会告诉我们每个时间段是"Speech"（人声）还是"Music"（音乐）还是"Applause"（掌声）。但目前这个信息只用来给已有字幕段打标签（`_apply_sv_tags`），没有汇总成全片人声地图。

**目标**：把 SenseVoice 的输出转换成两个列表：
- `voice_regions`：人声区间列表，如 `[(0.0, 15.3), (20.1, 45.6), ...]`
- `non_voice_regions`：非人声区间列表，如 `[(15.3, 20.1), (45.6, 60.0), ...]`

本次只生成这两个列表并记录到日志。后续 P275 Phase 2 才会用它来判断"人声区内无字幕 = 真漏洞"。

### 标准/精确模式重构：砍掉"快速"，标准模式用 Qwen 做主引擎

现在有三个模式：
- 快速（fast）：只跑 Whisper，不做任何过滤——基本没人用
- 标准（standard）：Whisper + SenseVoice 声音分类
- 精确（precise）：Whisper + Qwen + SenseVoice + 强制对齐，三模型交叉验证

问题：标准模式还是以 Whisper 为主引擎。但 P254 已经证明，对中日韩英四种语言，Qwen 的识别质量明显优于 Whisper。

**重构后**：
- **标准**：Qwen ASR + SenseVoice（不加载 Whisper，省内存省时间）
- **精确**：Qwen + Whisper + SenseVoice（双引擎交叉验证，跟现在的 precise 行为一致）

## 二、怎么改

### P275 改动

**新函数** `_build_voice_timeline(sv_events, audio_duration) -> dict`
- 位置：`transcriber_v2.py`（跟 `_apply_sv_tags` 放在一起）
- 输入：SenseVoice 事件列表 `[{start_s, end_s, event}, ...]` + 音频总时长
- 逻辑：
  1. 筛出 event=="Speech" 的事件
  2. 按 start_s 排序
  3. 合并重叠/相邻（间隔 ≤1s）的语音区间 → `voice_regions`
  4. 用总时长减去 voice_regions → `non_voice_regions`
- 输出：`{"voice_regions": [...], "non_voice_regions": [...], "voice_ratio": 0.xx}`

**调用位置**：`transcribe()` 中 SenseVoice 跑完之后、L1 之前
- 生成 timeline 并记日志：`_log.info("人声地图: %.1f%% 人声, %d 个语音区间", ...)`
- 本阶段不传给 L1，不影响过滤逻辑

### 标准/精确模式改动

**1. `should_qwen_pipeline(tier, ram_gb)` — L28-30**
- 改为：`tier in ("standard", "precise") and ram_gb >= 16`
- 标准和精确都启用 Qwen

**2. `transcribe()` — Whisper 加载条件化**
- 新增判断：`use_whisper = (transcription_tier == "precise")`
- `use_whisper=True` 时加载 Whisper、迭代段、产出 `results`（现有逻辑不动）
- `use_whisper=False` 时跳过 Whisper，`results` 从 Qwen 组装得到

**3. `transcribe()` — 标准模式 Qwen 主线程执行**
- 标准模式不需要并行线程（没有 Whisper 可以并行）
- Qwen 在主线程同步执行，拿到 `qwen_chunks` 后直接走 SenseVoice → L1 → 组装

**4. `transcribe()` — 标准模式跳过 L2 交叉验证**
- L2 的作用是比对 Whisper 和 Qwen 的文本差异。标准模式没有 Whisper，L2 无意义
- `filter_hallucinations` 已支持 `qwen_chunks=None` 时跳过 L2（`_qwen_is_reliable` 返回 False 就不跑 L2）
- 标准模式传 `qwen_chunks=None` 给 `filter_hallucinations`，让 L2 自然跳过
- 注意：标准模式 L1 仍然跑（SenseVoice 单信号），L4/L5/L3 仍然跑

**5. `worker.py` L353 — 去掉 "fast" 选项**
- 合法值改为 `("standard", "precise", None)`
- 收到 "fast" 时当作 "standard" 处理（向后兼容旧前端）

**6. `frontend/index.html` — 三按钮变两按钮**
- 删掉"快速"按钮
- "标准（推荐）"：Qwen + SenseVoice，速度快精度高
- "精确"：三模型交叉验证，占用更多内存
- 更新说明文字

## 三、数据流对比

### 标准模式（新）
```
Qwen ASR（主线程）
  → SenseVoice（声音分类）
    → _build_voice_timeline（P275 人声地图）
      → _apply_sv_tags
        → L1（SenseVoice 单信号）
          → _smart_assemble（Qwen only, whisper_segments=None）
            → L4 → L5 → L3
```

### 精确模式（基本不变）
```
Whisper ∥ Qwen ASR（并行）
  → SenseVoice（声音分类）
    → _build_voice_timeline（P275 人声地图）
      → _apply_sv_tags
        → L1（双信号：SenseVoice × Qwen 时间戳）
          → _smart_assemble（Qwen 主 + Whisper 参考）
            → L2（Whisper/Qwen 交叉验证）→ L4 → L5 → L3
```

## 四、不改什么

- L1 `_layer1_dual_signal` 内部逻辑不变
- L2 `_layer2_cross_validate` 内部逻辑不变（只是标准模式不调用它）
- `_smart_assemble` 内部逻辑不变（已支持 `whisper_segments=None`）
- SenseVoice runner 不变
- Qwen ASR runner 不变
- 说话人检测不变

## 五、测试计划

1. `_build_voice_timeline` 单元测试：空输入、全语音、全音乐、交叠区间、相邻区间合并
2. `should_qwen_pipeline` 单元测试：standard+16GB→True, standard+8GB→False, precise+16GB→True
3. `transcribe` 集成测试 mock：标准模式不加载 Whisper、精确模式加载 Whisper
4. 向后兼容：worker 收到 "fast" 降级为 "standard"
5. 前端：只有两个按钮、默认选中"标准"
