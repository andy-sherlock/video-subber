# 未来想法 — video-subtitle

## 待评估

- [ ] **智能转录路由（Andy 2026-06-11 提出）**：基于源语言自动切换主副引擎。英语/日语/韩语 → Qwen3-ASR 主 + Whisper 副；长尾小语种 → Whisper 主。precise tier 零成本（已跑两个模型，只改主次赋值）。详见 `model-comparison.md` 第九章。 

## 已采纳（已进入版本规划）
- 2026-06-04：Qwen3-ForcedAligner 词级对齐 → 已纳入 V3.04
- 2026-06-04：双信号幻觉过滤 → 已纳入 V3.04
- 2026-06-04：[?] 不确定标记恢复 → 已纳入 V3.04

## 迁移提醒（PyQt5 桌面端）

- [ ] **不要复制 Web UI 的 connectCard 重连逻辑**（2026-06-21 乔布斯副审提醒）：当前 Web 前端 `index.html:1084-1085` 的 `connectCard()` 在恢复任务时无条件重连 WebSocket，这是 P181 刷新页面重连的临时修法。写 PyQt5 桌面端时应从零设计连接管理，不要从旧前端复制粘贴。

## 暂不考虑
- 

（此文件由 planning-with-files-zh 自动维护）
