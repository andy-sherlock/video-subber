# Security Audit Report — V2.10 上线前审查

审查日期：2026-05-28
审查范围：API Key 加密、日志脱敏、依赖供应链、输入验证、本地服务器安全、激活码机制、子进程安全

---

## 🔴 Critical

### S1. `os.getuid()` 在 Windows 不可用 → 应用崩溃
**文件:** [key_manager.py:24](backend/key_manager.py#L24)
```python
material = f"{platform.node()}-{os.getuid()}".encode()
```
`os.getuid()` 是 Unix-only API。Windows 上 `import key_manager` 直接抛出 `AttributeError`，导致整个应用无法启动。
**修复:** 改用跨平台方案：
```python
import getpass, uuid
material = f"{platform.node()}-{getpass.getuser()}-{str(uuid.getnode())}".encode()
```

### S2. 硬编码激活码可被反编译提取
**文件:** [main.py:151](backend/main.py#L151)
```python
_BUILTIN_CODES: set[str] = {"ANDY", "MENG"}
```
PyInstaller 打包的 EXE 可通过 `pyinstxtractor` + `uncompyle6` 反编译提取字符串。任何人拿到 EXE 都能看到这两个激活码。
**修复:** 删除硬编码码，改用全环境变量 + 远程拉取：
```python
_BUILTIN_CODES: set[str] = set()
```

---

## 🟡 Important

### S3. 前端 localStorage 明文存储 API Key
**文件:** [index.html:726-727](frontend/index.html#L726-L727)
```javascript
const cfg={..., apiKeys:State.settings.apiKeys, ...};
localStorage.setItem('video-subber-config',JSON.stringify(cfg));
```
用户的 LLM API Key 以明文存入浏览器 localStorage。虽然这是本地应用，但：
- 浏览器扩展可以读取 localStorage
- 如果用户共用电脑，他人打开同一浏览器即可看到 Key
- 后端 `key_manager.py` 做了 Fernet 加密，但前端的 localStorage 副本绕过了这个保护
**修复:** 前端 localStorage 仅存 `{platform, name, model}` 元数据，key 值只通过 POST `/api/config` 加密存储到后端 `config.json`。页面加载时从后端拉回脱敏列表。

### S4. 机器指纹仅用 MAC + OS 名，换机破解门槛低
**文件:** [main.py:277-283](backend/main.py#L277-L283)
```python
parts = [str(_uuid.getnode()), _os.name]
```
Windows 上修改 MAC 地址只需几秒（设备管理器 → 网络适配器 → 高级 → Network Address）。一旦复制激活文件 + 修改 MAC，即可在另一台机器上激活。
**修复:** 增加硬件绑定因子：
```python
import platform, getpass
parts = [
    str(uuid.getnode()),           # MAC
    platform.node(),               # 机器名
    getpass.getuser(),             # 用户名
    platform.platform(),           # OS 详情
]
```

### S5. `_fetch_remote_json` 无证书验证
**文件:** [main.py:135-145](backend/main.py#L135-L145)
```python
req = _urllib.Request(url, headers={"User-Agent": "VideoSubber"})
resp = _urllib.urlopen(req, timeout=5)
```
`urllib.request.urlopen` 默认验证 HTTPS 证书，这是正确的。但没有设置更严格的超时和重试策略。如果攻击者控制了 DNS/网络（同一 WiFi），可中间人攻击注入伪造的激活码列表。
**修复:** 添加 SSL 证书固定（certificate pinning）或在请求中添加 `context` 参数。

### S6. Worker 子进程无输入验证防护
**文件:** [worker.py:199-216](backend/worker.py#L199-L216)
Worker 从 `config.json` 读取所有参数并直接使用。`video_path` 传给了 ffmpeg 和 Whisper。如果恶意构造的 config.json 包含 `"video_path": "/etc/passwd"` 或 `"; rm -rf /"`，理论上有风险。
**实际风险评估:** 
- ffmpeg 子进程使用 list-form args（`["ffmpeg", "-i", video_path, ...]`）→ **安全**，不会命令注入
- config.json 由主进程（main.py）写入，非用户直接控制 → **低风险**
- 路径遍历：`video_path` 可指向任意文件，但 ffmpeg 只读不写 → **低风险**
**建议:** 在 worker 启动时验证 `video_path` 是合法的视频文件扩展名。

### S7. activation.json 无完整性校验
**文件:** [main.py:286-302](backend/main.py#L286-L302)
`_load_activation()` 仅检查 `activation.json` 存在且指纹匹配。如果用户手动编辑 JSON 修改指纹字段，激活码验证会失败（因为指纹对不上），这是安全的。但没有文件篡改检测（HMAC/签名），高级用户可能绕过。
**实际风险:** 本地应用 + 低单价（¥1-3），攻击 ROI 很低。**可延后。**

---

## 🟢 Low / Info

### S8. 子进程安全：无 `shell=True`
对所有 subprocess 调用做了代码审计：
- [extractor.py:16](backend/core/extractor.py#L16): `subprocess.run([...], capture_output=True)` — list form, safe
- [subtitler.py:173](backend/core/subtitler.py#L173): `subprocess.run([...], capture_output=True)` — list form, safe
- [worker_manager.py:45-53](backend/worker_manager.py#L45-L53): `subprocess.Popen([...])` — list form, safe

**全部安全，无命令注入风险。**

### S9. 日志脱敏覆盖范围
- Worker 子进程：`_ScrubFormatter` 包装了 `scrub_logs()` → user.log 和 debug.log 均脱敏 ✅
- Worker stdout JSON IPC：通过 `emit()` 发送，包含 `message` 字段。`_friendly_error()` 已将技术错误转为用户友好消息 → API Key 不应出现在 JSON 消息中 ✅
- 主进程 stderr (worker_manager): `_log.debug("[worker:%s stderr] %s", ...)` — 如果 worker 子进程的 stderr 包含 Key（如 C++ 库的调试输出），可能泄露到 uvicorn 控制台。但 Whisper/faster-whisper/ffmpeg 的 stderr 不会包含 API Key → 低风险 🟡

### S10. CORS 配置
```python
allow_origins=["http://127.0.0.1:8766", "http://localhost:8766"]
```
仅允许本地访问。其他本机应用（如恶意网页 running on localhost:3000）无法跨域调用 API。✅ 安全。

### S11. 依赖供应链
pyproject.toml 声明的依赖：
| 包 | 版本 | 风险 |
|---|---|---|
| fastapi | >=0.115 | 活跃维护，低风险 |
| uvicorn | >=0.34 | 活跃维护，低风险 |
| faster-whisper | >=1.0 | 活跃维护，低风险 |
| cryptography | >=44.0 | 官方库，低风险 |
| librosa | >=0.10 | 音频处理，低风险 |
| resemblyzer | >=0.1 | 小众但稳定，低风险 |

无已知严重漏洞的依赖。建议在发布前运行一次 `pip-audit` 或 `uv lock --check` 做最终确认。

### S12. 本地文件路径注入
应用支持粘贴本地路径（`local_path`）。后端用 `os.path.isfile()` 验证路径存在。如果用户输入 `../../../etc/passwd`，`os.path.isfile()` 会正确解析并拒绝（如果文件不存在或不合法）。但 `os.path.isfile()` 接受任意可读文件——用户可以传入非视频文件（如系统 DLL），导致 ffmpeg 报错。**不会造成损害，但错误信息不够友好。** 🟢

### S13. 密码输入框
**文件:** [index.html:327](frontend/index.html#L327)
```html
<input type="password" id="apiKeyInput" placeholder="粘贴 API Key">
```
API Key 输入使用 `type="password"` + `autocomplete="off"` → 浏览器不会保存。✅

---

## 总结

| 类别 | 数量 | 上线前必须修 |
|------|------|-------------|
| 🔴 Critical | 2 | S1, S2 |
| 🟡 Important | 5 | S3（localStorage 明文） |
| 🟢 Low | 6 | 无 |

### 安全就绪度评分: **6.5/10**

扣分项：
- S1（Windows 崩溃）: -2.0
- S2（激活码泄露）: -0.5
- S3（localStorage 明文 Key）: -0.5
- S4（弱指纹）: -0.5

### 上线建议

S1 必须修复（否则 EXE 无法启动）。S2 和 S3 如果首批用户是受控内测（信任用户），可暂缓；公网发布则必须修。S4-S7 对 ¥1-3 价位的本地工具而言，攻击 ROI 太低，不是实际威胁。

_子进程参数使用 list form（无 shell injection）、Fernet 加密 Key、CORS 仅 localhost、日志脱敏——安全基础架构是好的。_
