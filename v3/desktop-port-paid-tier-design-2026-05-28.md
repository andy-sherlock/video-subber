# V2.11 上线前关键设计验证

日期：2026-05-28
审查范围：desktop.py 桌面移植方案 + DEV 模式付费分层设计
前置审阅：eng-review / design-review / code-review / QA / security-audit 均已完成

---

## 任务 A：桌面移植方案验证

### 架构总览

```
desktop.py (入口)
  ├─ daemon thread: uvicorn.run(app, port=8766)
  ├─ health poll loop: 50 * 100ms = 最多等 5 秒
  ├─ webview.create_window(url="http://127.0.0.1:8766")
  └─ webview.start()  ← 阻塞在 Native GUI 消息循环
       │
       └─ [WebView2 (Win) / WKWebView (Mac)] 渲染 frontend/index.html
            │
            └─ JS <--> FastAPI (127.0.0.1:8766)
                       │
                       └─ WorkerManager (子进程 or Frozen: 同进程线程)
```

---

### A1. 窗口生命周期完整性

**当前时序：**

```
main()
  ├─ 1. server = Thread(target=_start_server, daemon=True)
  │      server.start()
  ├─ 2. for i in range(50):         ← 轮询 /api/health，每次 0.1s
  │      sleep(0.1)
  │      try: urlopen(URL + "/api/health")
  │      if r.status == 200: break   ← 提前跳出
  ├─ 3. webview.create_window(...)
  ├─ 4. webview.start()             ← 阻塞直到窗口关闭
  └─ 5. print("已退出。")
```

**发现的问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| A1-1 | **健康检查超时后不报错** | 中 | for 循环结束后不检查是否真的连上了。如果后端在 5 秒内没就绪（冷启动 + 模型扫描慢），窗口照样打开，webview 加载 `http://127.0.0.1:8766` 会显示连接错误白页。没有 retry 机制，也没有错误提示。 |
| A1-2 | **关闭窗口后无优雅退出** | 中 | `webview.start()` 返回后，daemon uvicorn 线程会随主线程退出而被强制 kill。但：<br>- worker 子进程（非 Frozen 模式）变成孤儿进程，继续占用 CPU/GPU<br>- Frozen 模式 worker 线程是 daemon=True，也会被强杀<br>- 没调 `uvicorn.Server.shutdown()`，端口可能 TIME_WAIT 几秒<br>- 正在写入的缓存文件可能损坏 |
| A1-3 | **pywebview 关闭事件无法拦截** | 低 | `webview.start()` 返回后窗口已销毁。如有未完成任务，无法弹出"有任务进行中，确定退出？"确认框。pywebview 的 `window.events.closing` 可以注册回调，但当前未使用。 |
| A1-4 | **服务端崩溃无感知** | 低 | uvicorn 线程如果崩溃（端口被占、模块导入失败），主线程的 health poll 只会静默超时，没有 try/except 包裹整个 `_start_server`。 |

**建议修复：**

1. health 轮询超时后加判断：超时则弹出错误对话框或至少在主线程打印明确错误，调用 `sys.exit(1)`，不要打开空窗口。
2. 窗口关闭时增加清理逻辑：用 `atexit.register()` 或 `webview` 的 closing 事件 -> 调 `/api/shutdown` 端点 -> 通知 worker_manager 取消所有 job -> 等待 2 秒 -> 退出。
3. 为 desktop.py 增加 `/api/shutdown` 端点，在 frozen 模式下负责 join worker 线程。

---

### A2. 系统拖拽：Windows 文件管理器拖文件到窗口

**结论：pywebview 原生不支持，但可以通过 JS bridge 实现。**

**详细分析：**

pywebview 的 `create_window` 创建的是一个系统原生窗口（Windows: Win32 HWND + WebView2）。WebView2 内部的 HTML5 Drag and Drop API 可以处理浏览器内的拖拽（如从网页拖图片），但**操作系统级的文件拖放（从文件管理器拖 .mp4 到窗口）不会被 WebView2 的 JS `drop` 事件捕获**。

原因是：
- Windows 文件拖放走的是 OLE Drag and Drop（`IDropTarget` 接口）
- WebView2 的 `CoreWebView2` 默认不把 OS 级 drop 事件转发给 JS
- 需要通过 `ICoreWebView2Controller3.put_DragDropEnabled` 或 native host 桥接

**可行方案（按推荐度排序）：**

| 方案 | 复杂度 | 可靠性 | 说明 |
|------|--------|--------|------|
| **A) pywebview JS API bridge** | 中 | 高 | 在 desktop.py 中通过 `webview.start(func=MyApi)` 暴露一个 Python 类给 JS。窗口本身监听 OS drag-drop (通过 win32 API)，把文件路径通过 `window.pywebview.api.on_file_dropped(path)` 传给前端。需要 `pip install pywin32`（仅 Windows 打包时）。 |
| **B) 前端本地路径输入框** | 低 | 高 | 当前已实现。用户从文件管理器 Shift+右键"复制为路径"-> 粘贴到输入框。但体验差，不是拖放。 |
| **C) Custom webview HTML** | 低 | 低 | 在 html 中用一个全屏透明 div 做 drag-over 视觉反馈，但 drop 事件仍需 native 层转发。视觉上好看了但本质问题没解决。 |

**推荐方案 A**。实现思路：

```python
# desktop.py 新增
import webview

class FileDropApi:
    def __init__(self):
        self._pending_path = None

    def get_pending_file(self):
        """JS polls this on window load and after native drop"""
        if self._pending_path:
            p = self._pending_path
            self._pending_path = None
            return p
        return None

api = FileDropApi()
window = webview.create_window(..., js_api=api)
webview.start()
```

然后在 Windows 上通过 `win32gui` / `win32api` 监听 `WM_DROPFILES` 消息（需要子类化 HWND），收到后调用 `api._pending_path = path` 并执行 JS `window.pywebview.api.get_pending_file()` 轮询或事件通知。

**注意：** 这仅在 Windows 打包时需要。macOS 版 `WKWebView` 天然支持文件拖放（因为 macOS 的 NSView drag-drop 会转发到 WebView 的 DOM）。macOS 用户可直接拖文件到 webview 窗口。

---

### A3. 文件关联：双击视频文件自动打开应用

**需要三层配合：**

**第 1 层：Windows 注册表（安装程序写入）**

```
HKEY_CLASSES_ROOT\.mp4\OpenWithProgids\VideoSubber.file  (注册扩展名)
HKEY_CLASSES_ROOT\VideoSubber.file\shell\open\command    (关联命令)
  → 默认值: "C:\Program Files\VideoSubber\video-subber.exe" "%1"
```

同时注册其他视频格式：`.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.webm`, `.ts`。

这部分由 NSIS/Inno Setup 安装程序负责，不是 Python 代码。

**第 2 层：desktop.py 接收命令行参数**

当前 `desktop.py` 的 `main()` 不接受任何参数。需要改为：

```python
def main():
    # 检查命令行参数：是否通过文件关联打开
    file_path = None
    if len(sys.argv) > 1:
        candidate = sys.argv[1]
        if os.path.isfile(candidate):
            file_path = os.path.abspath(candidate)

    # ... 启动服务器 + webview ...

    if file_path:
        # 方式 A: URL 参数传给前端
        encoded = urllib.parse.quote(file_path)
        url = f"http://127.0.0.1:{PORT}/?open={encoded}"
        webview.create_window(..., url=url)

    # 前端 onload 时检查 ?open= 参数，自动填入本地路径并提示用户
```

**第 3 层：前端接收参数**

`index.html` 的 `DOMContentLoaded` 中已使用 `URLSearchParams`，只需加：

```javascript
const openPath = urlParams.get('open');
if (openPath) {
    State.localPath = decodeURIComponent(openPath);
    $('localPathInput').value = State.localPath;
    // 自动触发开始（或至少填充路径 + 启用按钮）
    refreshStartBtn();
}
```

**Frozen 模式注意事项：** `sys.argv[0]` 打包后是 EXE 路径本身，`sys.argv[1]` 是 Windows 传进来的文件路径。

---

### A4. 单实例锁：防止双击 EXE 启动两个后端

**问题分析：**

当前代码没有任何单实例保护。双击 EXE 两次：
- 实例 1: uvicorn 绑定 `127.0.0.1:8766`，成功
- 实例 2: uvicorn 尝试绑定 `127.0.0.1:8766`，`OSError: [Errno 48] Address already in use`
- 实例 2 的 webview 窗口仍会打开（因为 health poll 超时后照开不误，见 A1-1），显示连接错误

**推荐方案：文件锁 + 端口复用检测**

```python
import socket
import sys
from pathlib import Path

LOCK_FILE = Path.home() / ".video-subber" / "instance.lock"

def acquire_instance_lock() -> bool:
    """Returns True if this is the first instance. Second instance should exit."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Layer 1: 尝试绑定端口 —— 最快最可靠的检测
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", PORT))
        sock.close()
        # 端口可用 → 我们是第一个实例
    except OSError:
        # 端口已被占用 → 已有实例在运行
        return False

    # Layer 2: 写 PID 文件锁（防御性，处理端口未释放的边界情况）
    try:
        import fcntl  # Unix only
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        # Windows fallback: 用 msvcrt
        import msvcrt
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            return False

    with os.fdopen(fd, 'w') as f:
        f.write(str(os.getpid()))
    return True

def main():
    if not acquire_instance_lock():
        # 第二个实例：把焦点给第一个实例的窗口，然后退出
        # (Windows: 可以用 FindWindow + SetForegroundWindow)
        # 或者：把 argv[1] 发给第一个实例的 /api/open 端点
        print("Video Subber 已在运行中。")
        sys.exit(0)

    # ... 正常启动 ...
```

**第二实例体验优化（可选但推荐）：**

如果第二个实例是用文件关联启动的（带文件路径），应该把路径传给第一个实例而不是直接退出：

```python
def main():
    file_path = sys.argv[1] if len(sys.argv) > 1 else None

    if not acquire_instance_lock():
        if file_path:
            # 把路径 POST 给第一个实例
            import urllib.request, json
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/api/open-file",
                    data=json.dumps({"path": file_path}).encode(),
                    timeout=2,
                )
            except Exception:
                pass
        sys.exit(0)
```

第一个实例在 `/api/open-file` 端点收到路径后，通过 WebSocket 或 JS bridge 通知前端。

**pywebview 窗口聚焦：** pywebview 的 `window.restore()` / `window.minimize()` / `window.show()` 可以操作窗口状态。但前提是第二个实例需要知道第一个实例的 window handle，或者直接用端口占用作为信号（不够精确）。生产中可忽略——用户双击两次 EXE 的概率很低。

---

### A5. 打包方案对比：PyInstaller vs Nuitka

已经过 eng-review 详细论证，结论是 PyInstaller。这里补充关键差异点：

| 维度 | PyInstaller | Nuitka |
|------|------------|--------|
| **打包原理** | 打包 Python 解释器 + .pyc 到 exe | 编译 Python → C → 机器码 |
| **启动时间** | 3-8s（解压 overhead） | 2-5s（直接执行） |
| **反编译难度** | 低（.pyc 可轻易反编译） | 高（C 编译后的二进制） |
| **Windows Defender 误报** | 较常见（Wacatac 签名） | 较少 |
| **faster-whisper 兼容性** | 好（社区已验证） | 可能有 C 扩展兼容问题 |
| **ctranslate2 / CTranslate2** | 已验证 | 未知（C++ 编译到 C 再编译可能出问题） |
| **打包体积** | 300-500MB | 200-400MB |
| **社区/文档** | 最大 | 中等 |
| **本项目已有工作** | desktop.py Frozen 逻辑已写完 | 零 |

**最终推荐：PyInstaller**。理由：

1. `desktop.py` 的 `if FROZEN: ...` 分支完全是为 PyInstaller 写的（`sys._MEIPASS`, `sys.frozen`），切换到 Nuitka 这些代码可能不兼容或需要重测
2. faster-whisper 依赖 ctranslate2 (C++)——PyInstaller 打包 .pyd 已验证，Nuitka 的 C 编译层可能引入新的兼容性问题
3. 反编译风险对 V2.11 阶段不重要（首批内测，用户受控）。公网发布时可考虑后续迁移到 Nuitka
4. 时间成本：切换 Nuitka 需要重写 Frozen 路径 + 完整测试，预计 2-3 天。PyInstaller 0 天。

**PyInstaller spec 关键配置：**

```python
# video-subber.spec
a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[('ffmpeg.exe', '.'), ('ffprobe.exe', '.')],  # Windows
    datas=[
        ('frontend/', 'frontend/'),
        ('../v1/v1.3/', 'v1.3/'),        # V1.3 锁定模块
    ],
    hiddenimports=[
        'faster_whisper', 'resemblyzer', 'cryptography',
        'uvicorn', 'fastapi', 'websockets',
        'ctranslate2', 'tokenizers',
        'av', 'deep_translator',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pandas', 'numpy.tests'],
)
```

---

### A6. 功能损失检查：Web UI 在 pywebview 中的限制

逐项审查所有前端特性在 pywebview WebView2 中的兼容性：

| 功能 | 浏览器 (Chrome) | pywebview (Edge WebView2) | 损失? |
|------|-----------------|--------------------------|-------|
| **HTML5 Drag & Drop (浏览器内)** | 支持 | 支持 | 无 |
| **文件管理器拖放 (OS 级)** | 支持 | 不支持（见 A2） | **有** |
| **文件上传 `<input type="file">`** | 支持 | 支持 | 无 |
| **本地文件路径访问** | 安全限制 | 无需限制（桌面应用） | 无（甚至更自由） |
| **WebSocket** | 支持 | 支持 | 无 |
| **`localStorage`** | 支持 | 支持 | 无 |
| **`prefers-color-scheme: dark`** | 支持 | 支持 | 无 |
| **`prefers-reduced-motion`** | 支持 | 支持 | 无 |
| **DevTools (F12)** | 支持 | 默认隐藏 | **有（调试时）** |
| **右键菜单** | 浏览器菜单 | WebView2 菜单 | **轻微差异** |
| **`window.open()` / `target="_blank"`** | 新标签页 | 新窗口 or 被拦截 | **有** |
| **下载行为** | 浏览器下载管理器 | 直接保存 or 无响应 | **有** |
| **`confirm()` 弹窗** | 浏览器样式 | WebView2 原生样式 | **视觉不统一** |
| **文件保存对话框 (`showSaveFilePicker`)** | Chrome 86+ | Edge WebView2 支持但可能受限 | **有** |
| **`navigator.clipboard`** | 支持 | 支持 | 无 |
| **`fetch()` CORS** | 受同源策略 | 127.0.0.1 同源，无问题 | 无 |
| **PDF 内嵌查看** | 支持 | 不支持 | 无（不涉及） |
| **`<video>` 标签** | 支持 | 支持 | 无 |
| **`<audio>` 标签** | 支持 | 支持 | 无 |
| **打印 `window.print()`** | 支持 | 支持 | 无（不涉及） |

**需要特殊处理的功能：**

**1) 下载链接无法触发浏览器下载**

当前代码中下载用 `<a href="/api/jobs/xxx/download/video" target="_blank">`。在 pywebview 中：
- `target="_blank"` 可能打开系统默认浏览器（而不是在 webview 内下载）
- 或者 `FileResponse` 被 webview 直接渲染（如果是视频格式，可能在内嵌播放器中播放而不是下载）

**修复：** desktop.py 中通过 JS bridge 暴露 `save_file_dialog()` 方法，下载按钮调 `window.pywebview.api.save_file(url, suggested_name)`，由 Python 端用 `tkinter.filedialog` 或 `windll.comdlg32` 弹出原生保存对话框，然后 `urllib.request.urlretrieve` 下载到用户选择的位置。

**2) 保存路径选择对话框**

当前 `savePathBtn` 点击后用 inline input 替换——体验不够原生。改为调用 JS bridge → Python 弹出原生文件夹选择对话框。

**3) DevTools 不可用**

开发/调试时需要在 webview 中打开 DevTools。pywebview 在 debug 模式下可以：
```python
webview.create_window(..., easy_drag=False)
# 然后右键 → "Inspect" (仅当 webview.start(debug=True))
```
或通过 `window.load_html()` 注入 eruda（移动端调试工具）作为 fallback。

**4) 下载完成通知**

浏览器会在下载栏显示已完成文件。pywebview 无此功能。完成后应该在页面上显示文件路径 + "打开文件夹"按钮，由 JS bridge 调 `os.startfile(path)` 或 `subprocess.run(["open", path])`。

---

## 任务 B：开发者模式付费分层设计

### 当前状态分析与缺陷

**当前代码路径（main.py 451-464）：**

```python
def _effective_tier(requested: str) -> str:
    if _dev_mode_active:
        tier = requested if requested in TIER_FEATURES else "free"
        return tier
    return "free"
```

**核心问题：`_dev_mode_active` 是布尔值，不是 tier。** 任何人只要激活 dev mode，就可以通过 `?tier=paid_3` URL 参数或前端 URL 参数获取全功能权限。激活码之间没有权限差异。

**前端现状（index.html）：**

```javascript
// 第 448-449 行
const urlParams = new URLSearchParams(location.search);
const urlTier = urlParams.get('tier');
// 第 747 行
if (isDev && urlTier) State.settings.tier = urlTier;
```

当前前端行为：
- 加载页面 → `checkDevStatus()` → 如果 dev_mode=true，`isDev=true`
- `URL ?tier=paid_3` → 直接写入 `State.settings.tier = "paid_3"`
- 这个 tier 值在 `/api/jobs` 和 `/api/backends` 请求中作为参数发送给后端
- 后端 `_effective_tier()` 信任客户端传来的 `tier=paid_3`
- 没有任何验证链

---

### B1. 前端快速切换 tier 方案

需要三种切换方式，服务于不同使用场景：

#### 方式 1: URL 参数 `?tier=paid_1`（已存在，需加固）

**当前行为：** 前端读取 URL 参数直接写入 State.settings.tier。

**需要的改动：**
- URL 参数只能**降级**不能**升级**。即如果激活码是 `paid_1`，`?tier=paid_3` 应该被忽略
- 前端从 `/api/dev-status` 获取 `activated_tier` 后，cap URL 请求的 tier

```javascript
// 修改 index.html loadConfig() 中:
if (isDev && urlTier) {
    const activatedTier = window.__activatedTier || 'free';
    const TIER_LEVEL = { free: 0, paid_1: 1, paid_3: 2 };
    if (TIER_LEVEL[urlTier] <= TIER_LEVEL[activatedTier]) {
        State.settings.tier = urlTier;
    }
    // 否则忽略，使用 activatedTier
}
```

#### 方式 2: 开发者工具栏 Tier 下拉选择器（新增）

在 `devToolbar` 中添加一个下拉选择器：

```html
<!-- 在 devToolbar 中新增 -->
<select id="devTierSelect" onchange="switchDevTier(this.value)" style="font-size:11px;margin-left:8px">
  <option value="free">Free</option>
  <option value="paid_1">Paid 1 (自带Key)</option>
  <option value="paid_3">Paid 3 (平台Key)</option>
</select>
```

- 下拉框的可选范围受 `activated_tier` 限制（free 的激活码只能选 free，paid_3 的可以全选）
- 切换后：更新 `State.settings.tier` → 重新 `fetch('/api/backends?tier=' + tier)` → 刷新 UI
- 选中的 tier 写入 URL searchParams（可分享给其他开发者复现问题）

```javascript
function switchDevTier(tier) {
    State.settings.tier = tier;
    const url = new URL(location);
    url.searchParams.set('tier', tier);
    history.replaceState(null, '', url);
    fetch('/api/backends?tier=' + encodeURIComponent(tier))
        .then(r => r.json())
        .then(updateBackendLists);
}

function updateTierSelect(activatedTier) {
    const sel = $('devTierSelect');
    if (!sel) return;
    const TIER_LEVEL = { free: 0, paid_1: 1, paid_3: 2 };
    const maxLevel = TIER_LEVEL[activatedTier] || 0;
    for (const opt of sel.options) {
        opt.disabled = TIER_LEVEL[opt.value] > maxLevel;
        if (opt.value === State.settings.tier) opt.selected = true;
    }
    // 如果当前选中的 tier 超过上限，降级
    if (TIER_LEVEL[State.settings.tier] > maxLevel) {
        State.settings.tier = activatedTier;
    }
}
```

每次 `checkDevStatus()` 返回后调用 `updateTierSelect()`。

#### 方式 3: 控制台命令（无需 UI，方便自动化测试）

在浏览器控制台直接操作：

```javascript
// 开发者在 DevTools 控制台输入：
__devSwitchTier('paid_3')
```

实现（仅 dev mode 生效）：

```javascript
window.__devSwitchTier = function(tier) {
    if (!isDev) { console.warn('Dev mode required'); return; }
    const activatedTier = window.__activatedTier || 'free';
    const TIER_LEVEL = { free: 0, paid_1: 1, paid_3: 2 };
    if (TIER_LEVEL[tier] > TIER_LEVEL[activatedTier]) {
        console.warn(`Tier ${tier} exceeds activated ${activatedTier}, capped.`);
        tier = activatedTier;
    }
    switchDevTier(tier);
    console.log(`Tier switched to: ${tier}`);
};
```

---

### B2. 后端配合：DEV 模式需要修改的点

eng-review 已经把方案设计好，这里补充实现细节和需修改的具体位置：

**改动 1：`activation.json` 格式升级（main.py）**

```python
# 当前格式
{"code": "ANDY", "fingerprint": "a1b2c3..."}

# 新格式（向后兼容，缺 tier 时默认 paid_3）
{
    "code": "ANDY-DEV-XXXX",
    "tier": "paid_3",
    "fingerprint": "a1b2c3...",
    "activated_at": "2026-05-28T12:00:00Z"
}
```

**改动 2：`_load_activation()` 返回结构体而非布尔**

```python
def _load_activation() -> dict | None:
    """Returns {code, tier, fingerprint} or None."""
    if not _ACTIVATION_FILE.exists():
        return None
    try:
        data = json.loads(_ACTIVATION_FILE.read_text(encoding="utf-8"))
        code = data.get("code", "")
        fp = data.get("fingerprint", "")
        if fp == _machine_fingerprint() and code:
            # 向后兼容：旧版 activation.json 无 tier 字段 → 默认 paid_3
            if "tier" not in data:
                data["tier"] = "paid_3"
            return data
    except Exception:
        pass
    return None

# 全局变量改为
_activation = None   # dict | None，替代 _dev_mode_active: bool
_dev_mode_active = property(lambda self: self._activation is not None)
```

**改动 3：`_effective_tier()` 分级封顶**

```python
TIER_LEVEL_MAP = {"free": 0, "paid_1": 1, "paid_3": 2}

def _effective_tier(requested: str) -> str:
    if _activation:
        activated_tier = _activation.get("tier", "paid_3")
        requested_tier = requested if requested in TIER_FEATURES else "free"
        # Cap: 不能请求超过激活码授予的 tier
        if TIER_LEVEL_MAP.get(requested_tier, 0) <= TIER_LEVEL_MAP.get(activated_tier, 0):
            return requested_tier
        return activated_tier
    return "free"
```

**改动 4：`dev_activate()` 写入 tier**

```python
@app.post("/api/dev/activate")
async def dev_activate(payload: dict):
    global _activation
    code = payload.get("code", "").strip().upper()
    valid_codes = _all_valid_codes()

    # 新版 codes 格式：{"codes": {"CODE": {"tier": "paid_1", "expires": "..."}, ...}}
    # 旧版兼容：{"codes": ["CODE1", "CODE2"]}
    code_info = valid_codes.get(code) if isinstance(valid_codes, dict) else None
    if code_info is None and isinstance(valid_codes, (list, set)):
        # 旧版格式：code 在集合中 → 默认 paid_3
        if code in valid_codes:
            code_info = {"tier": "paid_3", "expires": None}

    if code_info is None:
        return JSONResponse({"error": "激活码无效"}, status_code=403)

    # Check expiration
    expires = code_info.get("expires")
    if expires:
        from datetime import datetime
        if datetime.now().isoformat()[:10] > expires:
            return JSONResponse({"error": "激活码已过期"}, status_code=403)

    tier = code_info.get("tier", "paid_3")
    fp = _machine_fingerprint()
    _ACTIVATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVATION_FILE.write_text(
        json.dumps({
            "code": code, "tier": tier, "fingerprint": fp,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    _activation = {"code": code, "tier": tier, "fingerprint": fp}
    return {"ok": True, "tier": tier}
```

**改动 5：`dev_status()` 返回结构化信息**

```python
@app.get("/api/dev-status")
def dev_status():
    if not _activation:
        return {"dev_mode": False, "has_platform_keys": False}

    code = _activation.get("code", "")
    # 脱敏：前 4 位 + *** + 后 1 位
    if len(code) > 5:
        masked = code[:4] + "***" + code[-1]
    else:
        masked = code[:2] + "***"

    return {
        "dev_mode": True,
        "activated_tier": _activation.get("tier", "paid_3"),
        "activated_code": masked,
        "expires": _activation.get("expires"),
        "has_platform_keys": bool(_PLATFORM_KEYS_CACHE),
    }
```

**改动 6：`activation_codes.json` 远程格式升级**

```json
{
    "codes": {
        "ANDY-DEV-XXXX":  {"tier": "paid_3", "note": "开发者全权限", "expires": null},
        "TESTER-YYYY":    {"tier": "paid_1", "note": "内测用户", "expires": "2026-12-31"},
        "BETA-ZZZZ":      {"tier": "free",   "note": "免费功能测试", "expires": "2026-09-30"}
    },
    "updated_at": "2026-05-28T00:00:00Z"
}
```

同时保留旧版 `["CODE1", "CODE2"]` 格式的兼容读取（自动升级为 `paid_3`）。

**改动 7：环境变量 `VSUB_DEV_TIER`（本地开发跳过激活码）**

```python
# 在 startup() 中：
if _os.environ.get("VSUB_DEV_TIER"):
    env_tier = _os.environ["VSUB_DEV_TIER"]
    if env_tier in TIER_FEATURES:
        _activation = {"code": "ENV_DEV", "tier": env_tier, "fingerprint": "env"}
```

这个仅在 `request.client.host == "127.0.0.1"` 时生效（在 `_effective_tier` 中检查，或直接在 startup 中只允许本地访问）。

**设计决策记录：** eng-review 建议 "仅限 127.0.0.1"，但 desktop.py 启动的 webview 也是 127.0.0.1，所以 desktop 应用自然满足条件。简化方案：不做 IP 检查，环境变量就是开发者在本地 dev server 用的。

---

### B3. UI 差异清单：Free vs Paid_1 vs Paid_3

逐项列出所有 UI 差异，确保前端三种 tier 下的行为明确定义：

| # | UI 元素 | Free（默认） | Paid_1（用户自带 LLM Key） | Paid_3（平台提供 Key） |
|---|---------|-------------|--------------------------|----------------------|
| 1 | **环境栏** | 相同 | 相同 | 相同 |
| 2 | **上传区** | 相同 | 相同 | 相同 |
| 3 | **开发者工具栏** | 不显示 | "已激活: Paid 1 (自带Key)" | "已激活: Paid 3 (平台Key)" |
| 4 | **模型选择** | tiny/base/small/medium/large-v3 全可选 | 相同 | 相同 |
| 5 | **翻译模式 toggle** | **仅极速模式** | 极速 + 质量优先 | 极速 + 质量优先 |
| 6 | **翻译优先级列表** | 显示，可拖拽排序 | 显示，可拖拽排序 | 显示，可拖拽排序 |
| 7 | **"LLM 智能翻译"卡片** | 显示但不可操作 | 完全可操作 | 显示"平台已提供" |
| 8 | **API Key 输入行** | **隐藏** | **显示**，可添加 Key | **隐藏**（平台 Key 自动注入） |
| 9 | **API Tip 文案** | "升级后可启用 LLM 智能翻译。可用平台: Claude / GPT / Gemini / ..." | "可用平台: Claude / GPT / Gemini / ... 添加 API Key 即可启用" | "LLM 智能翻译已启用（平台提供 API，无需配置）" |
| 10 | **API Key 列表** | **隐藏** | 显示已添加的 Key | **隐藏** |
| 11 | **高级设置（上下文感知）** | **隐藏整个卡片** | 显示，可切换 | 显示，可切换 |
| 12 | **"上下文感知" toggle** | 不可见 | 可见，可操作 | 可见，可操作 |
| 13 | **字幕样式** | 相同 | 相同 | 相同 |
| 14 | **保存路径** | 相同 | 相同 | 相同 |
| 15 | **开始按钮** | 相同 | 相同 | 相同 |
| 16 | **开发者小齿轮** | 显示（可激活） | 不显示（已激活） | 不显示（已激活） |
| 17 | **Tier 下拉选择器** | 不显示 | 显示（可选 free 或 paid_1） | 显示（可选全部三个） |
| 18 | **"退出开发者"按钮** | 不显示 | 显示 | 显示 |

**前端实现映射（index.html 修改位置）：**

当前 `updateBackendLists()` 函数（第 493-533 行）已经有部分 tier 分支逻辑，但需要根据上表补充。具体改动点：

| 行号范围 | 当前逻辑 | 需要改 |
|----------|---------|--------|
| 510-513 | `isDev && tier==='paid_3'` → 隐藏 API Row，显示"平台提供" | 改为读 `activated_tier` 而非 `State.settings.tier`（因为 activated_tier 是 cap） |
| 515-518 | `isDev && tier==='paid_1'` → 显示 API Row | 保留，增加 activated_tier cap 检查 |
| 519-523 | `!isDev` → 显示 "升级后可启用"，隐藏 API Row + 高级设置 | 保留免费用户逻辑 |
| 528-532 | else → paid_3 fallback | 需要区分 `paid_1` vs `paid_3` vs 未知 tier |

**重构后的 `updateBackendLists()` tier 分支：**

```javascript
function updateBackendLists(data) {
    // ... 填充 backends 列表 ...

    const tier = State.settings.tier;
    const activatedTier = window.__activatedTier || 'free';
    const isDevActive = isDev && activatedTier !== 'free';

    if (tier === 'paid_3' && activatedTier === 'paid_3') {
        // Paid_3: 平台提供 Key
        $('apiTip').innerHTML = '✅ <b>LLM 智能翻译已启用</b>（平台提供 API，无需配置）';
        $('apiRow').style.display = 'none';
        $('apiKeyList').style.display = 'none';
        $('modeRow').style.display = 'flex';
        $('modelHint').style.display = 'none';
        $('advCard').style.display = 'block';
        $('transModeTitle').textContent = '翻译模式';
    } else if (tier === 'paid_1' && activatedTier === 'paid_1') {
        // Paid_1: 用户自带 Key
        $('apiTip').textContent = `可用 LLM 平台: ${data.llm.map(b=>b.name).join(' / ')}。添加 API Key 即可启用。`;
        $('apiRow').style.display = 'flex';
        $('apiKeyList').style.display = '';
        $('modeRow').style.display = 'flex';
        $('advCard').style.display = 'block';
        $('transModeTitle').textContent = '翻译模式';
    } else {
        // Free: 无 LLM 访问
        $('apiTip').textContent = `升级后可启用 LLM 智能翻译。可用平台: ${data.llm.map(b=>b.name).join(' / ')}`;
        $('apiRow').style.display = 'none';
        $('apiKeyList').style.display = 'none';
        $('modeRow').style.display = 'flex';
        $('modelHint').style.display = 'none';
        $('advCard').style.display = 'none';
        $('transModeTitle').textContent = '翻译模式';
        // Disable contextAware
        if (State.settings.contextAware) {
            State.settings.contextAware = false;
            document.querySelectorAll('#ctxToggle button').forEach(b =>
                b.classList.toggle('active', b.dataset.v === 'false'));
        }
    }
}
```

**关键设计决策：** `activated_tier` 是硬 cap。即使用户通过 URL `?tier=paid_3` 请求了 paid_3，如果激活码只授予了 paid_1，界面也按 paid_1 显示，后端也按 paid_1 执行。这保证了 URL 参数只是"向下切换"的便利工具，而非权限提升通道。

---

## 综合总结

### 上线前必须修复（P0）

| ID | 问题 | 位置 |
|----|------|------|
| C1 | `os.getuid()` Windows 崩溃 | key_manager.py:24 |
| C2 | 版本号 2.08 → 2.11 | main.py:469 |
| C3 | .version 写入 2.08 → 2.11 | main.py:375 |
| C4 | orchestrator 不可达死代码 | orchestrator.py:383 |
| **A1-1** | health poll 超时后无错误处理 | desktop.py:143-151 |
| **A4** | 无双实例锁 | desktop.py:无 |

### 内测前必须修复（P1）

| ID | 问题 | 位置 |
|----|------|------|
| **B2** | 激活码 tier 分级（全部 7 个改动点） | main.py |
| **A1-2** | 窗口关闭无优雅退出 | desktop.py |
| **A6-1** | pywebview 下载行为适配（JS bridge） | desktop.py + index.html |
| M1 | 硬编码激活码泄露风险 | main.py:151 |

### 公网发布前（P2）

| ID | 问题 |
|----|------|
| A2 | OS 级文件拖放（pywin32 API bridge） |
| A3 | 文件关联 + 命令行参数接收 |
| A5 | EV 代码签名证书 + NSIS 安装程序 |
| A1-3 | 关闭前未完成任务确认 |
| M2 | 机器指纹增强 |
| M6 | localStorage 明文 Key |

### 无需修复（设计决策确认）

| 项目 | 决策 |
|------|------|
| 打包方案 | PyInstaller，不做 Nuitka |
| Whisper 模型 | 不捆绑，按需下载到 huggingface cache |
| ffmpeg | 不捆绑，首次启动引导下载（B 方案） |
| 在线验证 | 不做，本地优先 |
| 心跳反共享 | 不做，用 fingerprint + 激活频率限制替代 |
| Nuitka 迁移 | V2 不做，公网发布前评估 |
