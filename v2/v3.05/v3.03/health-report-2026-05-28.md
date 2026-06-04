# Code Health Report — V2.11

审查日期：2026-05-28
上一版 V2.10 报告对比基准：health-report-2026-05-28.md（V2.10 上线前审查）

---

## Health Score: 5.5/10

| 类别 | 权重 | V2.11 评分 | V2.10 评分 | 变化 | 说明 |
|------|------|------------|------------|------|------|
| Python 语法正确性 | 20% | 10/10 | 10/10 | -- | 全部 28 文件编译通过 |
| JS 语法正确性 | 10% | 10/10 | 9/10 | +1 | console.* 残留全部清理 |
| 类型注解覆盖 | 15% | 8/10 | 8/10 | -- | ~90% 函数有类型注解 |
| 代码规范 (lint) | 10% | 3/10 | 3/10 | -- | 无 lint 工具配置，发现 4 处未使用 import |
| 死代码 | 10% | 6/10 | 7/10 | -1 | V2.10 的 3 处已修复，新发现 4 处 |
| 测试覆盖 | 20% | 0/10 | 0/10 | -- | test/ 目录不存在，零测试文件 |
| 依赖健康 | 10% | 7/10 | 7/10 | -- | 依赖精简，但未做漏洞扫描 |
| 文档 | 5% | 7/10 | 7/10 | -- | 有操作手册，有完整架构文档 |
| **综合** | **100%** | **5.5/10** | **5.5/10** | **持平** | **最大扣分：零测试覆盖** |

---

## 1. Python 语法正确性 — 10/10

```
backend/main.py               OK
backend/worker.py             OK
backend/worker_manager.py     OK
backend/key_manager.py        OK
backend/config_manager.py     OK
backend/env_check.py          OK
backend/subtitle_style.py     OK
backend/transcriber_v2.py     OK
backend/ws_manager.py         OK
backend/usage_reporter.py     OK
backend/core/__init__.py      OK
backend/core/checkpoint.py    OK
backend/core/extractor.py     OK
backend/core/subtitler.py     OK
backend/core/vad_filter.py    OK
backend/translation/__init__.py  OK
backend/translation/base.py      OK
backend/translation/google.py    OK
backend/translation/baidu.py     OK
backend/translation/claude.py    OK
backend/translation/gpt.py       OK
backend/translation/gemini.py    OK
backend/translation/deepseek.py  OK
backend/translation/doubao.py    OK
backend/translation/glm.py       OK
backend/translation/custom.py    OK
backend/translation/orchestrator.py OK
desktop.py                    OK
```

全部 28 个 Python 文件 + desktop.py 通过 `py_compile` 编译检查。✅

---

## 2. JavaScript 语法 — 10/10 (+1 vs V2.10)

- 350 组花括号 { } 完全匹配 ✅
- 910 组圆括号 ( ) 完全匹配 ✅
- **零 `console.*` 残留** ✅（V2.10 有 13 处，已全部清理）
- 无重复变量声明冲突 ✅

V2.10 报告中的 6 处 `console.error`、4 处 `console.warn`、3 处 `console.log` 已全部移除。

---

## 3. 类型注解覆盖 — 8/10

~90% 函数有返回类型注解，覆盖良好。主要缺口：

- 多处使用裸 `list` / `dict` 而非参数化类型（如 `list[Segment]`、`dict[str, Any]`）
- 少数函数缺少返回类型：
  - `worker.py:193`: `def main():` 无返回类型
  - `worker.py:538`: `def stderr_progress(...)` 无返回类型
  - `worker_manager.py:71,122`: `_read_stdout`, `_read_stderr` 无参数类型

总体：对个人项目来说注解质量高。

---

## 4. 代码规范 (Lint) — 3/10

| 工具 | 状态 |
|------|------|
| ruff | 未安装 |
| mypy | 未安装 |
| pytest | 未安装 |
| CI/CD pipeline | 无 |

### 发现的未使用 import：

| # | 文件 | 行 | 未使用导入 | 
|----|------|----|-----------|
| L1 | backend/key_manager.py | 6 | `import os` — 模块中从未引用 `os.anything` |
| L2 | backend/translation/gemini.py | 2 | `import time` — 文件内未使用 |
| L3 | backend/translation/gemini.py | 3 | `from .base import ..., TranslationResult` — TranslationResult 未直接使用 |
| L4 | backend/translation/base.py | 2 | `from dataclasses import ..., field` — `field` 未使用 |

**建议：** 添加 `pyproject.toml` 的 ruff + mypy 配置段，并在 `.github/workflows/` 加最小 CI。

---

## 5. 死代码 — 6/10 (-1 vs V2.10)

### 5a. V2.10 问题修复确认

| # | V2.10 问题 | 状态 |
|----|-----------|------|
| H1 | `main.py:6` 死导入 `UploadFile, File, Form` | **已修复** ✅ — 行 6 现为 `Request, WebSocket, WebSocketDisconnect` |
| H2 | `orchestrator.py` 死导入 `concurrent.futures` | **已修复** ✅ — 现导入 `threading, time, re` |
| H3 | `orchestrator.py:383-384` 不可达代码 | **已修复** ✅ — 现在为 context_aware 异常回退路径，可达且逻辑正确 |

### 5b. 新发现的死代码

#### 从未被调用的函数：

| # | 文件 | 行 | 函数 | 说明 |
|----|------|----|------|------|
| D1 | backend/core/extractor.py | 23 | `get_video_duration()` | 定义但全项目无调用。疑似 V1.3 遗留。 |
| D2 | backend/key_manager.py | 39 | `encrypt_api_key()` | 定义但从不调用。API Key 以明文存储到 config.json（仅在展示时 mask），加密链断了。 |

#### 7 个 LLM 后端的 `understand_context()` 方法：

| # | 文件 | 行 | 说明 |
|----|------|----|------|
| D3a | claude.py | 36-46 | 从未被调用。上下文感知翻译走 `translate_raw()` 路径 |
| D3b | gpt.py | 27-37 | 同上 |
| D3c | gemini.py | 42-52 | 同上 |
| D3d | deepseek.py | 27-37 | 同上 |
| D3e | doubao.py | 27-37 | 同上 |
| D3f | glm.py | 27-37 | 同上 |
| D3g | custom.py | 30-40 | 同上 |

这 7 个方法共 ~70 行代码，是设计阶段的占位方法。`LLMBackend` 基类中未声明为抽象方法，实际上下文感知功能由 `orchestrator.py:_translate_whole_with_markers()` 通过 `translate_raw()` 实现。

#### 整个模块未使用：

| # | 文件 | 说明 |
|----|------|------|
| D4 | backend/usage_reporter.py (90 行) | `record_usage()`, `flush_usage()`, `replay_fallback()` 三个函数全项目无外部调用。V3 占位模块，`SERVER_URL = None` 时所有函数为 no-op。 |

### 5c. 注释掉的代码块

未发现。代码干净，无被注释掉的功能代码块。✅

---

## 6. 测试覆盖 — 0/10 (红色)

**项目零测试文件。** `test/` 目录不存在，`test/test_pure_functions.py` 不存在。

| 优先级 | 测试对象 | 类型 | 原因 |
|--------|---------|------|------|
| P0 | `key_manager.encrypt/decrypt` | 单元 | 加密错误 = 用户 Key 丢失 |
| P0 | `_config_hash` | 单元 | 设置去重逻辑核心 |
| P1 | `_friendly_error` | 单元 | 用户看到的错误信息 |
| P1 | `_split_zh_into_sentences` | 单元 | 字幕断句核心 |
| P2 | `scrub_logs` | 单元 | 安全关键 |
| P2 | `extract_audio` + mock ffmpeg | 集成 | 流水线第一步 |
| P2 | `translate_with_orchestrator` | 集成 | 核心翻译逻辑 |

---

## 7. 依赖健康 — 7/10

`pyproject.toml` 声明 11 个直接依赖，版本约束合理：

```
fastapi>=0.115, uvicorn[standard]>=0.34, python-multipart>=0.0.20,
faster-whisper>=1.0, deep-translator>=1.11, resemblyzer>=0.1,
librosa>=0.10, cryptography>=44.0, websockets>=14.0
```

**打包体积预估（PyInstaller macOS）：**

| 依赖 | 估重大小 |
|------|----------|
| faster-whisper + ctranslate2 | ~25 MB |
| librosa + numpy + scipy + numba | ~180 MB |
| torch（silero-vad 依赖）| ~800 MB |
| cryptography | ~5 MB |
| fastapi + uvicorn + websockets | ~15 MB |
| resemblyzer | ~5 MB |
| deep-translator | ~2 MB |
| 其他 + Python runtime | ~60 MB |
| **预估总计** | **~350-500 MB** |

torch 是体积大户。若 silero-vad 改为可选依赖（仅用于语音占比健康检查，非核心路径），可节省 ~800MB。

- 未运行 `pip-audit` 做最终确认
- `uv.lock` 519KB 需检查锁定版本是否有已知 CVE

---

## 8. 代码复杂度

### 超 200 行函数

| # | 文件 | 行 | 函数 | 行数 | 说明 |
|----|------|----|------|------|------|
| C1 | backend/worker.py | 193-518 | `main()` | **325 行** | 流水线编排函数，含 5 个步骤 + 配置变更检测 + LLM 降级弹窗 |

其他函数均远低于 200 行上限。`main.py:create_job()` 139 行，`startup()` 80 行。

**建议：** 将 `main()` 中的 5 个流水线步骤各抽取为独立函数（`_step_extract`, `_step_transcribe`, `_step_translate`, `_step_subtitle`, `_step_burn`）。

### 循环依赖

未检测到硬循环依赖。存在一处惰性导入避免环形依赖：
- `orchestrator.py` → `backend.main.get_platform_keys`（函数内惰性 import，运行时才解析）

架构层级清晰：`main.py` → `worker_manager` → `worker`(子进程) → `core/` + `translation/` + `transcriber_v2`

---

## 9. 代码统计

| 指标 | V2.11 | V2.10 | 变化 |
|------|-------|-------|------|
| Python 文件 | 28 | 28 | -- |
| Python 总行数 | 3,602 | 3,551 | +51 |
| 前端 HTML/JS/CSS | 994 | 959 | +35 |
| Desktop 启动器 | 171 | 171 | -- |
| **项目总行数** | **4,767** | 4,681 | +86 |
| 函数/方法定义 | ~105 | ~105 | -- |
| 前后端接口 | 14 HTTP + 1 WS | 14 HTTP + 1 WS | -- |
| LLM 翻译后端 | 7 | 7 | -- |

---

## 10. V2.10 → V2.11 改进清单

| 改进项 | 状态 |
|--------|------|
| 删除 `UploadFile, File, Form` 死导入 (main.py) | ✅ 已修复 |
| 删除 `concurrent.futures` 死导入 (orchestrator.py) | ✅ 已修复 |
| 删除不可达代码 (orchestrator.py 383-384) | ✅ 已修复（现为正常异常回退路径）|
| 清理 13 处 `console.*` 残留 (frontend) | ✅ 已修复 |
| V2.10 报告中其他建议 | 未处理（无 lint 工具、零测试） |

---

## 11. Quick Wins (优先级排序)

1. **删除 4 个未使用 import**（5 分钟）：`key_manager.py:os`, `gemini.py:time`, `gemini.py:TranslationResult`, `base.py:field`
2. **删除 `extractor.py:get_video_duration()`**（1 分钟）：V1.3 遗留，零调用
3. **删除或注释 `encrypt_api_key()`**（1 分钟）：Key 以明文存储，加密函数从不调用，容易误导
4. **删除 `usage_reporter.py` 或加 TODO 注释**（1 分钟）：V3 占位模块，90 行全部未激活
5. **7 个 `understand_context()` 标记为未来使用**（5 分钟）：加 `# TODO(V3)` 或移到基类 `LLMBackend` 作为可选方法
6. **拆分 `worker.py:main()`**（30 分钟）：325 行拆为 5 个步骤函数

---

## 12. 上线就绪度

代码语法正确，架构清晰，类型注解良好。V2.10 报告的 3 个关键问题（死导入 + 不可达代码 + console 残留）均已修复。

**但零测试覆盖仍是致命问题** —— 任何代码修改都可能引入回归而无人知晓。对于首批受控内测可接受，公网发布前必须补齐 P0/P1 测试。
