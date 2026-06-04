# Code Health Report — V2.10 上线前审查

审查日期：2026-05-28

---

## Health Score: 5.5/10

| 类别 | 权重 | 评分 | 说明 |
|------|------|------|------|
| Python 语法正确性 | 20% | 10/10 | 全部 28 文件编译通过 |
| JS 语法正确性 | 10% | 9/10 | 无语法错误，13 处 console 残留 |
| 类型注解覆盖 | 15% | 8/10 | ~100% 函数有类型注解 |
| 代码规范 (lint) | 10% | 3/10 | 无 lint 工具配置，无 CI pipeline |
| 死代码 | 10% | 7/10 | 发现 2 处死导入，1 处不可达代码 |
| 测试覆盖 | 20% | 0/10 | 零测试文件 |
| 依赖健康 | 10% | 7/10 | 依赖精简，但未做漏洞扫描 |
| 文档 | 5% | 7/10 | 有操作手册，有完整架构文档 |
| **综合** | **100%** | **5.5/10** | **最大扣分：零测试覆盖** |

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
backend/translation/*.py (12) OK
backend/core/*.py (5)         OK
```

全部 28 个 Python 文件通过 `py_compile` 编译检查。✅

---

## 2. JavaScript 语法 — 9/10

- `new Function(script)` 解析通过，无语法错误 ✅
- 154 个声明变量，无重复声明冲突 ✅
- **13 处 `console.*` 残留** 🟡——生产版本应移除或通过 build step 剥离：
  - `console.error`: 6 处（fetch 失败日志）
  - `console.warn`: 4 处（配置保存/syncUI 防御捕获）
  - `console.log`: 3 处（dev 模式环境检测 + 其他）

---

## 3. 类型注解覆盖 — 8/10

~89 个非 init 函数中，约 80 个有返回类型注解 (~90%)。覆盖良好。

缺失示例：
- `worker.py:193`: `def main():` 无返回类型
- `worker.py:500`: `def stderr_progress(...)` 无返回类型
- `worker_manager.py:78-79`: `_read_stdout`, `_read_stderr` 无参数类型

总体：对个人项目来说注解质量很高。

---

## 4. 代码规范 — 3/10

| 工具 | 状态 |
|------|------|
| ruff | 未安装 |
| mypy | 未安装 |
| pytest | 未安装 |
| CI/CD pipeline | 无 |
| .gitignore | 需要确认 |

**建议：** 添加 `pyproject.toml` 的 ruff + mypy 配置段，并在 `.github/workflows/` 加一个最小 CI。

---

## 5. 死代码 — 7/10

### 已确认的死导入：

| # | 文件 | 行 | 死导入 | 状态 |
|----|------|----|--------|------|
| H1 | main.py | 6 | `UploadFile, File, Form` | 代码已改为原始 `Request` 解析，这三个 FastAPI 类型不再使用 |
| H2 | orchestrator.py | 2 | `concurrent.futures` | 速度模式改用 `threading.Thread` 直接管理，未使用 ThreadPoolExecutor |
| H3 | orchestrator.py | 383-384 | 两行不可达代码 | `return` 在 381 行，383-384 永远不执行（已在 code review C4 中记录） |

---

## 6. 测试覆盖 — 0/10 🔴

**项目零测试文件。** 没有任何单元测试、集成测试或端到端测试。

对一个即将打包 EXE 上线的项目，这是最大的质量风险。建议至少覆盖：

| 优先级 | 测试对象 | 类型 | 原因 |
|--------|---------|------|------|
| P0 | `key_manager.encrypt/decrypt` | 单元 | 加密错误 = 用户 Key 丢失 |
| P0 | `_config_hash` | 单元 | 去重逻辑核心 |
| P1 | `_friendly_error` | 单元 | 用户看到的错误信息 |
| P1 | `_split_zh_into_sentences` | 单元 | 字幕断句核心 |
| P2 | `scrub_logs` | 单元 | 安全关键 |
| P2 | `extract_audio` + mock ffmpeg | 集成 | 流水线第一步 |
| P2 | orchestrator `translate_with_orchestrator` | 集成 | 核心翻译逻辑 |

---

## 7. 依赖健康 — 7/10

`pyproject.toml` 声明的依赖精简（11 个），版本约束合理。没有已知的严重漏洞依赖。

但：
- 未运行 `pip-audit` 做最终确认
- `uv.lock` 519KB 需要检查锁定的版本是否有已知 CVE

---

## 8. 代码统计

| 指标 | 数值 |
|------|------|
| Python 文件 | 28 |
| Python 总行数 | 3,551 |
| 前端 HTML/JS/CSS | 959 |
| Desktop 启动器 | 171 |
| **项目总行数** | **4,681** |
| 函数/类定义 | 105 |
| 前后端接口 | 14 个 HTTP + 1 个 WebSocket |
| LLM 翻译后端 | 7 个 |

---

## Quick Wins

1. **删除 3 个死导入**（5 分钟）：`UploadFile, File, Form` from main.py + `concurrent.futures` from orchestrator.py
2. **删除不可达代码**（1 分钟）：orchestrator.py 383-384 行
3. **生产 build 剥离 console.***（10 分钟）：添加条件判断或使用 esbuild/terser

---

## 上线就绪度

代码语法正确，架构清晰，类型注解良好。**但零测试覆盖是严重问题**——任何代码修改都可能引入回归而无人知晓。对于首批受控内测可接受，公网发布前必须补齐 P0/P1 测试。
