# P275 L1 全片人声/非人声时间轴 bug 流水线 — 2026-06-15

## 元信息
- 涉及问题：P275
- 涉及文件：`v2/backend/transcriber_v2.py`、`v2/backend/core/hallucination_filter.py`、`v2/backend/core/sensevoice_runner.py`
- 当前进度：①/⑩
- 当前决策：当前 ASR 分支不继续改 P275 代码；先选择性 commit 并合并 `main`。合并后新开 `l1-voice-timeline` 分支继续本流水线。
- 接力提醒：不要把 SRT gap 直接当成漏字幕。只有“人声区内没有字幕”才叫真实漏字幕。

## ① investigate — 2026-06-15
**执行人**：Claude
**依赖类型**：📖 读指定文件
**读了什么**：
- `v2/backend/transcriber_v2.py`
- `v2/backend/core/hallucination_filter.py`
- `v2/backend/core/subtitler.py`
- `/Users/andy/.video-subber/logs/e5494b352d/user.log`
- `/Users/andy/.video-subber/jobs/e5494b352d/FJIN-141.srt`
- `/Users/andy/.video-subber/jobs/e5494b352d/cache/c2e90d73d46a/transcript.json`

### 结论（下一步只看这里）

用户人工复核后确认：此前用 SRT gap 直接称“空洞”的口径错误。

具体事实：
- `36:27-38:45` 没有对白，不是漏字幕。
- `40:10-41:35` 没有对白，不是漏字幕。
- `85:35` 附近有字幕，问题是翻译准确性，不是空洞。

当前 L1 的真实行为：
- L1 只检查已经存在的字幕段。
- `_layer1_dual_signal(seg, ...)` 的输入是一个 `seg`，它判断这条字幕段是否该保留、删除或打 `[?]`。
- 当前没有输出全片 `voice_regions/non_voice_regions`。
- 因此系统无法自动区分“正常无对白间隔”和“有人声但没字幕”。

应修目标：
1. SenseVoice 结果要沉淀成全片人声/非人声时间轴。
2. Qwen FA/词级时间戳要叠到这张时间轴上。
3. 质量报告里只有“人声区内没有字幕”才叫真实漏字幕。
4. 非人声区长时间无字幕应记为正常，不应报空洞。

### 涉及文件列表
- `v2/backend/transcriber_v2.py:876-898` — 当前 SenseVoice/L1/组装/过滤的编排位置。
- `v2/backend/core/hallucination_filter.py:170-231` — 当前 L1 只按单个字幕段判断。
- `v2/backend/core/subtitler.py:79-80` — 最终只把 `uncertain=True` 显示成 `[?]`。
- `v2/backend/core/sensevoice_runner.py` — 后续应从这里保留原始声音事件并生成 timeline。

### 关键代码
```python
def _layer1_dual_signal(seg, aligner_result=None, source_lang="auto") -> bool:
    tag = getattr(seg, "sensevoice_tag", None)
    if tag is None:
        return False
    ...
```

这段代码说明 L1 当前的工作对象是“单条字幕段”，不是“整段音频”。

### 下一步输入

进入 ② 双签审查：
- `plan-eng-review` 审 P275 技术方案是否应该先生成 timeline，还是只做日志诊断。
- `steve-jobs-perspective` 审是否会把当前系统复杂度拉爆，是否可以用更简单的质量报告先解决误报。

在 ②-③ 完成前，不改生产代码。

## 交接记录 — 2026-06-15
**执行人**：Claude
**依赖类型**：📋 看记录

### 结论（下一步只看这里）

本次用户要求只做规划和记录，不动任何代码。

当前 ASR 分支处理方式：
- 先选择性提交当前 ASR 分支。
- 再合并到 `main`。
- 提交/合并说明必须写清：P274 已修，P273 句子化改善但 `[?]` 仍多，P275 未修。

P275 的下次处理方式：
- 从 `main` 新开 `l1-voice-timeline` 分支。
- 从本管道文件的 ② 双签审查继续。
- 先审方案，再审影响面，再进入 TDD。不要直接改代码。

后续两个独立分支：
- `qwen-standard-mode`：砍掉 fast/极速；标准 = Qwen ASR + SenseVoice；精确 = Qwen + Whisper + SenseVoice。
- `mlx-asr-library-swap`：继续 Whisper/MLX 换库和模型路径调整。
