# Engineering Architecture Review — V2.10 上线前审查

审查日期：2026-05-28
审查范围：EXE 打包策略 + 开发者模式付费分层

---

## 任务 1: Web UI → Windows EXE 打包方案

### 当前状态

`desktop.py` 已经实现了一个工作原型：
- 使用 `pywebview` 创建原生窗口（1100×750，最小 800×500）
- 后台线程启动 uvicorn 服务器
- 在 webview 中加载 `http://127.0.0.1:8766`
- Frozen 模式下（`sys.frozen=True` → PyInstaller 打包），将 Worker 从子进程切换为同进程线程（避免 `-m backend.worker` 在 EXE 中不可用）

**当前方案的优点：**
- 单一入口 `python desktop.py` 即可启动
- Frozen 模式已处理路径修正（`sys._MEIPASS`）
- Worker 在 frozen 下用线程替代子进程——内存和启动时间优化

### 方案评估

| 方案 | 打包尺寸 | 启动时间 | Windows 可靠性 | 反病毒误报 | 自动更新 |
|------|---------|---------|---------------|-----------|---------|
| **PyInstaller + webview**（当前） | 300-500MB（含模型） | 3-8s | 中高 | 中等风险 | 需自建 |
| **Nuitka** | 200-400MB | 2-5s | 中 | 低风险 | 需自建 |
| **Briefcase** | 较大 | 较慢 | 中 | 低风险 | 内置 |
| **Electron + Python** | 200MB+80MB | 3-10s | 高 | 极低 | 内置 |

### 推荐方案：PyInstaller + webview（当前方案，需加固）

**理由：**
1. `desktop.py` 已经完成了 80% 的工作——路径修正、Frozen 模式 Worker 切换、webview 窗口
2. `pywebview` 在 Windows 上使用 Edge WebView2（Windows 10+ 内置），不需要捆绑 Chromium
3. PyInstaller 社区最大，Windows 打包文档最丰富
4. 切换 Nuitka/Electron 意味着重写已工作的 Frozen 逻辑——ROI 低

**需要加固的点：**

#### 1.1 PyInstaller spec 文件配置
```
关键配置项：
- hidden-imports: faster_whisper, resemblyzer, cryptography, uvicorn
- datas: frontend/index.html → frontend/
- datas: v1.3/ → v1.3/ (Whisper 模型 + 流水线模块)
- binaries: ffmpeg.exe, ffprobe.exe → ./
- exclude-modules: tkinter, matplotlib, pandas (减小体积)
- icon: app.ico
```

#### 1.2 ffmpeg 分发策略
三个选项，推荐 **B 方案（智能检测 + 引导安装）**：

| 方案 | 体积 | 复杂度 |
|------|------|--------|
| A) 捆绑 ffmpeg.exe (~80MB) | +80MB | 低 |
| B) 首次启动检测 + 引导下载 | +0MB | 中 |
| C) 要求用户自行安装 | +0MB | 低（但用户体验差）|

B 方案：桌面应用首次启动时用 `check_ffmpeg()` 检测，缺失时弹出对话框引导用户下载 ffmpeg-full build。下载后自动解压到 `%APPDATA%/video-subber/ffmpeg/`。

#### 1.3 Whisper 模型分发
5 个模型总计 ~5.5GB，不可能全部捆绑。当前方案（按需自动下载到 `~/.cache/huggingface/`）是正确的——首次使用某模型时自动下载并缓存。**保持不变。**

但需注意：Windows 上 `~/.cache/` 映射为 `C:\Users\<user>\.cache\`，这个路径对普通用户不可见。建议在 UI 中显示缓存大小和清除按钮。

#### 1.4 反病毒误报降低策略
PyInstaller 打包的 EXE 容易被 Windows Defender 标记为 `Trojan:Win32/Wacatac`：
- 使用 `--clean` 标志每次重新构建
- 向 Microsoft 提交误报申诉（提交 EXE 到 https://www.microsoft.com/en-us/wdsi/filesubmission）
- 用 EV Code Signing Certificate 签名 EXE（~$300-500/年）
- **签名是上线必须项**——未签名的 EXE 会被 SmartScreen 拦截

#### 1.5 安装包格式
推荐 NSIS 或 Inno Setup 生成 `.exe` 安装程序：
- 解压到 `C:\Program Files\VideoSubber\`
- 创建桌面快捷方式
- 注册卸载程序
- 写入注册表（安装路径、版本号）

#### 1.6 自动更新
MVP 方案：应用启动时 GET GitHub Releases API → 比较版本号 → 有新版本则弹出提示 + 打开下载页。
V2 方案：下载 `.msi`/`.exe` 安装包 → 静默安装 → 重启应用。

#### 1.7 Windows 路径
当前代码中 `Path.home() / ".video-subber"` 在 Windows 上展开为 `C:\Users\<user>\.video-subber\`。Windows 惯例是 `%APPDATA%\VideoSubber\`。**建议打包时覆盖默认路径：**
```python
if sys.platform == "win32":
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "VideoSubber"
else:
    DATA_DIR = Path.home() / ".video-subber"
```

### 打包实施步骤

| 步骤 | 内容 | 预计耗时 |
|------|------|---------|
| 1 | 修复 `os.getuid()` 为跨平台方案 | 10 min |
| 2 | 修复版本号为 "2.10" | 5 min |
| 3 | 编写 PyInstaller `.spec` 文件 | 30 min |
| 4 | 测试打包 + Windows VM 验证 | 2 h |
| 5 | 配置 NSIS/Inno Setup 安装程序 | 1 h |
| 6 | 代码签名（EV Certificate） | 1-3 天（外部流程） |
| 7 | 反病毒误报申诉 | 1 周（等待 MS 处理） |
| 8 | 自动更新逻辑 | 2 h |

---

## 任务 2: 开发者模式付费分层

### 当前状态

当前开发者模式是一个**布尔开关**：
- 输入激活码 → `dev_mode = True` → 前端可以选任何 tier（free/paid_1/paid_3）
- `_effective_tier()` 在 dev mode 下直接返回请求的 tier
- 激活码不分等级——ANDY/MENG/远程码 都是一样的权限
- `POST /api/dev/activate` 接受任意有效码并绑定本机

### 问题

1. **没有 tier 区分**：开发者在 dev mode 下可以手动切到 `paid_3`，但这个切换是前端 URL 参数控制的，没有后端验证——任何人知道 `?tier=paid_3` 就能用
2. **激活码不分级**：所有激活码权限相同，无法区分 ¥1 用户和 ¥3 用户
3. **无过期机制**：激活码一旦激活就是永久的
4. **无吊销机制**：无法远程禁用已泄露的激活码

### 设计方案

#### 2.1 激活码 → Tier 映射

激活码从「字符串集合」升级为「结构化对象」：

```json
// activation_codes.json (GitHub 远程)
{
  "codes": {
    "ANDY-DEV-XXXX":  {"tier": "paid_3", "note": "开发者全权限", "expires": null},
    "TESTER-YYYY":    {"tier": "paid_1", "note": "内测用户 ¥1 档", "expires": "2026-12-31"},
    "BETA-ZZZZ":      {"tier": "free",   "note": "仅免费功能测试", "expires": "2026-09-30"}
  }
}
```

#### 2.2 激活流程

```
用户输入激活码 → POST /api/dev/activate {code: "ANDY-DEV-XXXX"}
  → 后端查 activation_codes.json
  → 找到 code → 读取 tier + expires
  → 验证未过期
  → 写入 activation.json: {code, tier, fingerprint, activated_at}
  → 返回 {ok: true, tier: "paid_3"}
```

#### 2.3 激活状态持久化

```json
// ~/.video-subber/activation.json
{
  "code": "ANDY-DEV-XXXX",
  "tier": "paid_3",           // 新增：激活时确定的 tier
  "fingerprint": "a1b2c3d4e5f6g7h8",
  "activated_at": "2026-05-28T12:00:00Z"
}
```

此后整个会话中 `_dev_mode_active` 不再只是布尔值，而是该用户的 tier。

#### 2.4 `_effective_tier()` 修改

```python
def _effective_tier(requested: str) -> str:
    if _dev_mode_active:
        # Dev mode: use the tier from activation, cap at activated level
        activated_tier = _load_activation_tier()  # "free" | "paid_1" | "paid_3"
        requested_tier = requested if requested in TIER_FEATURES else "free"
        # Don't allow requesting higher than what the code grants
        TIER_LEVEL = {"free": 0, "paid_1": 1, "paid_3": 2}
        if TIER_LEVEL.get(requested_tier, 0) <= TIER_LEVEL.get(activated_tier, 0):
            return requested_tier
        return activated_tier
    return "free"
```

#### 2.5 离线验证

激活文件包含 `fingerprint`（本机绑定），启动时：
1. 读取 `activation.json`
2. 验证 `fingerprint` 匹配当前机器
3. 验证 `expires` 未过期（无 expires = 永不过期）
4. 加载对应的 `tier`

**不需要联网验证。** 这是本地应用的核心承诺。

#### 2.6 反共享措施

| 措施 | 效果 | 用户影响 |
|------|------|----------|
| 机器指纹绑定（MAC + 用户名 + 机器名 + OS） | 换机需新码 | 换电脑需联系 |
| 激活频率限制（同一 code 每小时最多 3 次激活） | 阻止批量共享 | 无（正常用户不会频繁重装） |
| 远程吊销（24h TTL 缓存） | 泄露码 24h 内失效 | 正常用户无感 |
| 显示激活信息（"已激活: ¥1 档 · ANDY-DEV-XXXX"） | 截图分享 = 泄露身份 | 用户知道码是绑定的 |

**不做：** 在线心跳验证（违反本地优先承诺）、硬件 ID 绑定（重装系统导致码失效）。

#### 2.7 前端 UI 变更

| 当前 | 变更后 |
|------|--------|
| 开发者工具栏显示 "全 tier 解锁" | 显示 "¥1 档已激活" 或 "¥3 档已激活" |
| tier URL 参数手动切换 | 下拉选择受限于激活码的 tier 上限 |
| `apiTip` 在 dev mode 下文案固定 | 根据 activated_tier 动态显示 |
| API Key 行：dev mode 全可见 | paid_1 可见可操作，paid_3 显示"平台已提供" |

#### 2.8 `/api/dev-status` 扩展

```json
{
  "dev_mode": true,
  "activated_tier": "paid_3",
  "activated_code": "ANDY-***X",
  "expires": null,
  "has_platform_keys": true
}
```

前端读取 `activated_tier` 决定 UI 行为。`activated_code` 脱敏显示（前几位 + 后一位）。

#### 2.9 开发/测试用环境变量

保持简单：
```bash
VSUB_DEV_TIER=paid_3 python -m backend.main
```
设置环境变量后 `_dev_mode_active` 直接等于该 tier，跳过激活码流程。仅限 `127.0.0.1` 访问（检查 `request.client.host`）。

### 实施步骤

| 步骤 | 内容 | 代码改动范围 |
|------|------|-------------|
| 1 | `activation.json` schema 增加 `tier` 字段 | main.py `dev_activate()` |
| 2 | `_effective_tier()` 改为基于 activated tier 封顶 | main.py |
| 3 | `dev_status()` 返回 `activated_tier` + `activated_code`(masked) | main.py |
| 4 | `activation_codes.json` schema 升级（code → {tier, expires}） | 远程 GitHub |
| 5 | 前端 `updateBackendLists()` 根据 `activated_tier` 调整 UI | index.html |
| 6 | 开发者工具栏显示实际 tier 而非 "全 tier 解锁" | index.html |
| 7 | 环境变量 `VSUB_DEV_TIER` 支持（本地开发跳过激活码） | main.py |

---

## 综合建议

### 上线优先级

| 优先级 | 任务 | 依赖 |
|--------|------|------|
| **P0（上线前）** | 修复 `os.getuid()` Windows 崩溃（C1） | — |
| **P0（上线前）** | 修复版本号（C2, C3） | — |
| **P0（上线前）** | 修复 orchestrator 不可达代码（C4） | — |
| **P0（上线前）** | PyInstaller .spec 文件 + Windows 打包测试 | C1 |
| **P1（首批内测前）** | 激活码 tier 区分（任务 2 步骤 1-6） | — |
| **P1（首批内测前）** | EV Code Signing 证书 | 时间/费用 |
| **P2（公网发布前）** | NSIS 安装程序 | P0 打包 |
| **P2（公网发布前）** | 自动更新逻辑 | P0 打包 |
| **P2（公网发布前）** | 反病毒误报申诉 | P1 签名 |
