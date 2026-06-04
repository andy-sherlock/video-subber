# Phase 1D 进度日志

**开始**：2026-06-04
**版本**：V3.04

---

## 2026-06-04 会话 #1

### 完成事项
- [x] Steve Jobs 审查：V4 四项提前到 V3.04 Phase 1D
- [x] 项目文档更新：版本规划表、功能清单、Phase 1D 详细计划
- [x] Obsidian 同步：用户视角 DIFF 格式
- [x] V3.04 只读快照创建（`v2/v3.04/`）
- [x] 确认当前版本 V3.03，159 测试全过
- [x] brainstorming：4 项设计决策锁定
- [x] 设计规格文档：`docs/superpowers/specs/2026-06-04-phase1d-alignment-design.md`
- [x] 任务计划文件：`task_plan.md`

### 当前状态
- 活跃代码：V3.03（`v2/`），pyproject.toml 版本号 `"3.03"`
- 只读快照：V3.01 / V3.02 / V3.03 / V3.04（空壳）
- Phase 1D 代码：**0 行，尚未开始**
- Phase 1D 规格：**已锁定，7 项决策全部确认**

### 2026-06-04 会话 #2：Phase 1D 编码实施

#### 阶段 1：模型分时加载 ✅
- 发现 Whisper/SenseVoice/Qwen3-ASR 已有模型释放
- 新增 `QwenAligner.close()` 方法
- 8 个测试

#### 阶段 2：Qwen3-ForcedAligner 集成 ✅
- 新增 `backend/core/qwen_aligner.py`：QwenAligner 类 + apply_aligner_timestamps + extract_segment_time
- 集成到 `transcriber_v2.py`：转录后、过滤前运行（Steve Jobs 审查修正）
- Steve Jobs 审查发现流水线位置矛盾，修正 Qwen3-FA 从"翻译后"移到"转录后"
- 12 个测试

#### 阶段 3：双信号幻觉过滤 ✅
- `hallucination_filter.py` L1 升级：`_layer1_dual_signal()` 替代 `_layer1_sensevoice()`
- 四路判断矩阵实现
- 向后兼容：`aligner_result=None` 时降级单信号
- 7 个测试

#### 阶段 4：[?] 标记 + 阈值提示 ✅
- 发现 `subtitler.py` 已有 [?] 标记实现（`_annotate_speaker_changes`）
- 修复格式：[ ? ] → [?]（去掉空格）
- Worker：`emit("done", ...)` 新增 `question_mark_count`
- 前端：阈值三级梯度 + 黄/红提示条
- 10 个测试

#### 阶段 5：审查收尾 ✅
- 196 测试全过（159 旧 + 37 新）
- pyproject.toml：3.03 → 3.04
- 规格文档更新：修正 Qwen3-FA 流水线位置

### 改动文件汇总

| 文件 | 操作 | 行数变化 |
|------|------|---------|
| `backend/core/qwen_aligner.py` | 🆕 | ~195 行 |
| `backend/core/hallucination_filter.py` | ✏️ | L1 升级为双信号 |
| `backend/transcriber_v2.py` | ✏️ | 集成 Qwen3-FA（~25 行） |
| `backend/worker.py` | ✏️ | 新增 qm_count（~3 行） |
| `backend/core/subtitler.py` | ✏️ | 修复 [?] 格式（1 行） |
| `frontend/index.html` | ✏️ | 阈值警告条+JS |
| `test/test_phase1d_memory.py` | 🆕 | 8 tests |
| `test/test_phase1d_aligner.py` | 🆕 | 12 tests |
| `test/test_phase1d_dual_signal.py` | 🆕 | 7 tests |
| `test/test_phase1d_question_mark.py` | 🆕 | 10 tests |
| `pyproject.toml` | ✏️ | 3.03→3.04 |
| `docs/.../2026-06-04-phase1d-alignment-design.md` | ✏️ | 修正流水线 |

### 下一步
进入 Phase 2：PySide6 原生桌面应用
