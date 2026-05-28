# Code Review Report — V2.10 上线前审查

审查范围：`v2/v2.10/` 全部后端 + 前端 + desktop.py
审查日期：2026-05-28

---

## 🔴 Critical（必须在上线前修复）

### C1. `os.getuid()` Windows 兼容性崩溃
**文件:** [key_manager.py:24](backend/key_manager.py#L24)
```python
material = f"{platform.node()}-{os.getuid()}".encode()
```
`os.getuid()` 在 Windows 上不存在，打包 EXE 后 `import key_manager` 即崩溃，整个应用无法启动。
**修复:** 改为跨平台方案：
```python
import getpass
material = f"{platform.node()}-{getpass.getuser()}-{uuid.getnode()}".encode()
```

### C2. health 端点版本号过时
**文件:** [main.py:469](backend/main.py#L469)
```python
return {"ok": True, "version": "2.08"}
```
前端 HTML 标题显示 `v2.10`，但后端 `/api/health` 返回 `2.08`。版本号不一致会给用户支持和 bug 报告带来混乱。
**修复:** 改为 `"2.10"`。

### C3. 启动时强制写入旧版本标记
**文件:** [main.py:372-378](backend/main.py#L372-L378)
```python
if current.get("version") != "2.08":
    version_data = {"version": "2.08", ...}
```
每次启动如果 `.version` 不是 `"2.08"` 就强制写回 `"2.08"`。这意味着 V2.10 的 `.version` 文件永远写着 `"2.08"`，V3 升级迁移逻辑无法正确判断来源版本。
**修复:** 改为 `"2.10"`。

### C4. orchestrator 中存在不可达死代码
**文件:** [orchestrator.py:383-384](backend/translation/orchestrator.py#L383-L384)
```python
        # ── Fallback: LLM failed, use free backends ──
        all_backends = ordered_backends
    else:
        all_backends = ordered_backends + llm_backends
```
`translate_with_orchestrator` 的 paid 分支（`context_aware` 和逐段翻译）都以 `return` 结束，383-384 行的 "Fallback" 逻辑永远不可达。注释声称"LLM 失败后用免费后端兜底"，但实际没有实现这个降级路径。
**修复:** 如果需要兜底逻辑，应在 try/except 或失败检查后执行，而非放在 return 之后。如果不需要，删除不可达代码并更新注释。

---

## 🟡 Important（建议修复）

### M1. 硬编码的开发者激活码
**文件:** [main.py:151](backend/main.py#L151)
```python
_BUILTIN_CODES: set[str] = {"ANDY", "MENG"}
```
"ANDY" 和 "MENG" 硬编码在源码中。发布到公网后，任何人反编译 EXE 都能看到这两个码。
**修复:** 
- 删除内置码，改用环境变量 `VSUB_DEV_CODES` + 远程 `activation_codes.json` 双重渠道
- 或者保留少量内置码但通过环境变量注入（不写死）

### M2. 机器指纹过于简单
**文件:** [main.py:277-283](backend/main.py#L277-L283)
```python
parts = [str(_uuid.getnode()), _os.name]
return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
```
只用 MAC 地址 + 操作系统名做指纹。Windows 上 MAC 地址可轻易修改，激活码绑定不够强。
**修复:** 增加更多硬件因子：硬盘序列号、用户名、机器名、Windows Product ID 等，提高换机破解的门槛。

### M3. 上下文感知翻译缺少"先理解再翻译"的 Step 1
**文件:** [orchestrator.py:331-337](backend/translation/orchestrator.py#L331-L337)
`context_aware=True` 时直接调 `_translate_whole_with_markers()`，用 `[N]` 标记整篇翻译。但 V2 架构设计（video-subtitle.md 第 488-493 行）定义了两步流程：
1. LLM 先读全文理解语境（理解主题/人物/术语，不需要翻译）
2. 基于语境理解逐段翻译

当前实现跳过了 Step 1，只是把所有 segment 拼在一起做一次 LLM 调用。虽然 `[N]` marker 方案有效，但缺少独立的"语境理解"步骤，与设计文档不符。
**修复:** 要么在 `context_aware` 路径前加一步独立的语境理解调用，要么更新架构文档承认 marker 方案是最终实现。

### M4. `_config_hash` 未包含 `apiKeys`
**文件:** [main.py:39-51](backend/main.py#L39-L51)
`_config_hash` 函数排除了 `apiKeys` 字段（因为 Key 是敏感数据不应进哈希）。但换 Key 后翻译结果可能完全不同（不同模型翻译质量不同），应该让换 Key 被视为"配置变更"。
**修复:** 将 `apiKeys` 的脱敏版本（如 `{platform, model}` 不含 key 值）纳入哈希：
```python
"apiKeys": [{"platform": k.get("platform",""), "model": k.get("model","")} for k in cfg.get("apiKeys", [])]
```

### M5. `reconnectJobs` 第一个恢复 job 的 config 覆盖用户当前设置
**文件:** [index.html:751](frontend/index.html#L751)
```javascript
if(firstReconnect&&j.config){State.settings={...State.settings,...j.config};syncUI();firstReconnect=false}
```
页面加载时如果 localStorage 有未完成任务，第一个 job 的 config 直接覆盖 `State.settings`。如果用户当时正在调整设置（比如换了模型），这些未保存的修改会丢失。
**修复:** 只恢复不覆盖当前设置，或者至少对比后提示用户。

### M6. `apiKeys` 中的 key 值通过 localStorage 明文持久化
**文件:** [index.html:726](frontend/index.html#L726)
```javascript
const cfg={..., apiKeys:State.settings.apiKeys, ...};
localStorage.setItem('video-subber-config',JSON.stringify(cfg));
```
用户的 API Key 明文写入浏览器 localStorage。虽然这是本地应用且后端用 Fernet 加密存储到 `config.json`，但 `localStorage` 中的副本是明文的。任何能访问该浏览器的用户/扩展都能读取。
**修复:** 前端 localStorage 不保存 key 本身，只保存 `{platform, name, model}` 元数据。key 值仅在添加时 POST 到后端加密存储，页面加载时从后端拉回脱敏后的列表。

---

## 🟢 Low（可延后）

### L1. `_machine_fingerprint` 在 `load_activation` 检查前未初始化
**文件:** [main.py:286-302](backend/main.py#L286-L302)
`_load_activation()` 调用 `_machine_fingerprint()` 没有问题（函数定义在前面），但如果未来的重构移动了函数顺序，可能引发 NameError。当前代码安全。

### L2. 导入别名不一致
**文件:** [main.py:118](backend/main.py#L118) vs [main.py:560](backend/main.py#L560)
`import os as _os` 和 `import os as _os_module` 两个别名指向同一个模块。不影响功能，但降低可读性。

### L3. `_read_stdout` reader 线程未处理 proc.stdout 为 None
**文件:** [worker_manager.py:78](backend/worker_manager.py#L78)
```python
for line in proc.stdout:
```
如果子进程启动后立即崩溃，stdout pipe 可能为 None，导致 `TypeError`。极低概率，但可加防御。

### L4. 前端 `saveConfig` 只重试 2 次
**文件:** [index.html:728](frontend/index.html#L728)
保存配置到后端失败后只重试 1 次（总共 2 次尝试）。如果后端恰好重启中，这可能不够。建议增加到 3 次 + 指数退避。

### L5. `desktop.py` webview 窗口缺少 Windows 图标设置
**文件:** [desktop.py:156-163](desktop.py#L156-L163)
`webview.create_window()` 没有设置窗口图标。打包成 EXE 后任务栏会显示默认 Python 图标。
**修复:** 添加图标文件并在打包配置中指定。

---

## 总结

| 类别 | 数量 | 必须修 |
|------|------|--------|
| 🔴 Critical | 4 | C1, C2, C3, C4 |
| 🟡 Important | 6 | M1（安全）, M6（安全） |
| 🟢 Low | 5 | 无 |

### 上线判断

C1（Windows 崩溃）是 blocker——EXE 打包后直接无法启动。修复 C1-C4 后代码质量达到上线标准。M1（激活码泄露）和 M6（localStorage 明文）属于安全债务，如果首批用户是受控内测则可不修，公网发布则必须修。

### 做得好的地方

- 前后端字段名基本一致（`burn`, `subtitles`, `model`, `translationMode` 等），参数传递链完整
- worker_manager 的双重护卫（`_kill_if_alive` + heartbeat 超时检测）设计可靠
- 配置感知去重（`task_key` = file_hash + config_hash）逻辑正确
- 错误翻译（`_friendly_error`）覆盖了内存不足、超时、限流、认证失败等主要场景
- `_word_align_segments` 利用 English word timestamps 做准确的中文时间对齐，设计精巧
- 双日志系统（user.log + debug.log + RotatingFileHandler）成熟
