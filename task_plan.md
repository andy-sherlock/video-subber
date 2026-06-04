# Phase 1D 任务计划：对齐精度 + 幻觉验证升级

**版本**：V3.04
**日期**：2026-06-04
**规格**：`v2/docs/superpowers/specs/2026-06-04-phase1d-alignment-design.md`
**状态**：🚧 进行中

---

## 目标

1. 字幕时间戳从「按字数比例估算」升级为「词级毫秒精度」
2. 幻觉过滤从「单信号」升级为「双信号交叉验证」
3. 恢复 [?] 不确定标记
4. 三级阈值提示系统
5. 模型分时加载，内存峰值减半

---

## 阶段

### 阶段 1：模型分时加载（独立，无依赖）

| 状态 | 任务 | 验证 |
|------|------|------|
| ⬜ | `transcriber_v2.py`：Whisper 转录完 `del model` | 内存峰值下降 |
| ⬜ | `transcriber_v2.py`：SenseVoice 跑完 `del model` | 内存峰值下降 |
| ⬜ | `transcriber_v2.py`：Qwen3-ASR 跑完 `del model` | 内存峰值下降 |
| ⬜ | `worker.py`：Qwen3-FA 对齐完 `aligner.close()` | 内存峰值下降 |
| ⬜ | `test/test_phase1d_memory.py`：6 个测试 | 全绿 |

**改动量**：~30 行

### 阶段 2：Qwen3-ForcedAligner 集成（核心）

| 状态 | 任务 | 验证 |
|------|------|------|
| ⬜ | `qwen_aligner.py`：新增文件 | import 通过 |
| ⬜ | `qwen_aligner.py`：`__init__` 加载模型（HuggingFace） | 加载成功 |
| ⬜ | `qwen_aligner.py`：`align()` 对齐推理 | 返回 `list[AlignedWord]` |
| ⬜ | `qwen_aligner.py`：`close()` 释放 | del 成功 |
| ⬜ | `qwen_aligner.py`：`is_qwen_aligner_available()` | 检测逻辑正确 |
| ⬜ | `worker.py`：翻译后插入 Qwen3-FA 对齐 | 流水线跑通 |
| ⬜ | `worker.py`：对齐失败降级（旧方案兜底） | 旧方案仍可用 |
| ⬜ | `sentence_splitter.py`：断句改用对齐时间戳 | 时间戳精度提升 |
| ⬜ | `test/test_phase1d_aligner.py`：8 个测试 | 全绿 |

**改动量**：~150 行新文件 + ~30 行集成修改

### 阶段 3：双信号幻觉过滤升级（依赖阶段 2）

| 状态 | 任务 | 验证 |
|------|------|------|
| ⬜ | `hallucination_filter.py` L1：新增 `aligner_result` 参数 | 向后兼容 |
| ⬜ | `hallucination_filter.py` L1：四路判断矩阵实现 | 四路逻辑正确 |
| ⬜ | `hallucination_filter.py`：`seg.uncertain` 标记 | 人声+无对齐→标记 |
| ⬜ | `worker.py`：对齐结果传入 `filter_hallucinations()` | 数据流正确 |
| ⬜ | `test/test_phase1d_dual_signal.py`：8 个测试 | 全绿 |

**改动量**：~60 行

### 阶段 4：[?] 标记 + 阈值提示（依赖阶段 3）

| 状态 | 任务 | 验证 |
|------|------|------|
| ⬜ | `subtitle_style.py`：`seg.uncertain` → 句尾追加 ` [?]` | 字幕文件验证 |
| ⬜ | `worker.py`：[?] 统计，WS 推送 `question_mark_count` | WS 消息正确 |
| ⬜ | `frontend/index.html`：三级阈值检测函数 | 计算正确 |
| ⬜ | `frontend/index.html`：黄/红提示条 UI | 视觉效果 |
| ⬜ | `test/test_phase1d_question_mark.py`：8 个测试 | 全绿 |

**改动量**：~50 行后端 + ~40 行前端

### 阶段 5：全量测试 + 审查

| 状态 | 任务 | 验证 |
|------|------|------|
| ⬜ | 运行全部 159 个旧测试 | 无退化 |
| ⬜ | 运行全部 30 个新测试 | 30 全绿 |
| ⬜ | code-review 全量 diff | 通过 |
| ⬜ | 更新 pyproject.toml 版本号 3.03→3.04 | 版本正确 |

---

## 依赖关系

```
阶段 1（独立）
    ↓
阶段 2（独立，但数据流上 2 的输出给 3）
    ↓
阶段 3（依赖 2 的 qwen_aligner 模块存在）
    ↓
阶段 4（依赖 3 的 seg.uncertain 标记存在）
    ↓
阶段 5（全部依赖）
```

## 降级原则

任何阶段失败不阻塞流水线：
- 阶段 1 失败 → 内存峰值不变，功能正常
- 阶段 2 失败 → 回退旧按比例分时方案
- 阶段 3 失败 → L1 回退单信号
- 阶段 4 失败 → [?] 不显示，不影响字幕内容
