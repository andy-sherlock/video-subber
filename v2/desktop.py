"""Desktop launcher — 双击打开，弹出真正的应用窗口。

用法：
  开发时：python desktop.py
  打包后：双击 video-subber.exe
"""

import sys
import os
import threading
import time
import json
import socket
from pathlib import Path


# ═══════════════════════════════════════════════════════════
# 0. 检测运行模式
# ═══════════════════════════════════════════════════════════

FROZEN = getattr(sys, "frozen", False)

# ── 单实例锁：使用 TCP 端口锁，确保只有一个实例在运行 ──
_SINGLETON_PORT = 18766
_SINGLETON_SOCKET = None


def _acquire_singleton():
    """尝试绑定一个专属端口。成功 → 当前是唯一实例。失败 → 已有实例在运行。"""
    global _SINGLETON_SOCKET
    try:
        _SINGLETON_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _SINGLETON_SOCKET.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _SINGLETON_SOCKET.bind(("127.0.0.1", _SINGLETON_PORT))
        return True
    except OSError:
        return False


def _release_singleton():
    global _SINGLETON_SOCKET
    if _SINGLETON_SOCKET:
        try:
            _SINGLETON_SOCKET.close()
        except Exception:
            pass
        _SINGLETON_SOCKET = None

# ═══════════════════════════════════════════════════════════
# 1. 打包模式：修正 V1.3 路径
#    main.py / worker.py / transcriber_v2.py 都用下面这行找 v1.3：
#      Path(__file__).parent.parent.parent / "v1" / "v1.3"
#    打包后 __file__ 指向 BUNDLE_DIR/backend/xxx.pyc
#    → parent.parent.parent 会跑到 BUNDLE_DIR 外面去，路径崩了。
#    解决方案：在 import 任何后端模块之前，把正确的 V1.3 路径塞进 sys.path。
#    Python 导入时发现 sys.path 里已有 video_subber，就不会报错。
#    __file__ 算出来的错误路径也会被 insert，但不存在的路径不影响导入。
# ═══════════════════════════════════════════════════════════

if FROZEN:
    BUNDLE_DIR = Path(sys._MEIPASS)
    V1_3_DIR = BUNDLE_DIR / "v1.3"
    if str(V1_3_DIR) not in sys.path:
        sys.path.insert(0, str(V1_3_DIR))
    if str(BUNDLE_DIR) not in sys.path:
        sys.path.insert(0, str(BUNDLE_DIR))


# ═══════════════════════════════════════════════════════════
# 2. 导入后端模块
#    FRONTEND 路径在 frozen 下自然正确：
#    __file__ = <BUNDLE>/backend/main.pyc
#    parent.parent = <BUNDLE>/ → / "frontend" = <BUNDLE>/frontend/ ✅
#    只要 auto-py-to-exe 里 frontend/ 解压到 BUNDLE_DIR/frontend/ 即可。
# ═══════════════════════════════════════════════════════════

import backend.main as _main
import backend.worker as _worker


# ═══════════════════════════════════════════════════════════
# 3. 打包模式：替换 Worker 的启动方式
#    原始方案：subprocess.Popen([sys.executable, "-m", "backend.worker", ...])
#    打包后 sys.executable 是 video-subber.exe，无法 -m 启动 Python 模块。
#    替代方案：直接在当前进程内调用 worker.main()。
#    单用户桌面应用不需要进程隔离——多一个进程只是浪费内存。
#    同时把 worker 的 emit() 接到 ws_manager，跳过 stdout JSON IPC。
# ═══════════════════════════════════════════════════════════

if FROZEN:

    _orig_worker_start = _main.worker_manager.start
    _orig_worker_cancel = _main.worker_manager.cancel
    _worker_threads = {}

    def _start_in_thread(job_id, job_dir, video_path, config):
        """同进程线程跑 Worker，emit 直连 ws_manager。"""
        config["video_path"] = video_path
        config_path = job_dir / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False)

        _main.worker_manager._job_dirs[job_id] = job_dir

        # 替换 worker.emit：跳过 stdout JSON IPC，消息直送 ws_manager
        _orig_emit = _worker.emit
        _orig_stderr_progress = _worker.stderr_progress

        def _direct_emit(event_type, **kwargs):
            msg = {"type": event_type, **kwargs}
            loop = _main.worker_manager._main_loop
            if loop and loop.is_running():
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    _main.worker_manager._forward(job_id, msg), loop
                )

        _worker.emit = _direct_emit

        def _direct_stderr_progress(stage, percent, message, detail=""):
            _direct_emit("progress", stage=stage, percent=percent,
                         message=message, detail=detail)

        _worker.stderr_progress = _direct_stderr_progress

        def _run():
            _old_stdout = sys.stdout
            _old_argv = sys.argv
            sys.argv = ["video-subber", str(config_path)]
            try:
                _worker.main()
            except Exception as e:
                _direct_emit("error", message=str(e), failed_segments=[])
            finally:
                sys.argv = _old_argv
                sys.stdout = _old_stdout
                _worker.emit = _orig_emit
                _worker.stderr_progress = _orig_stderr_progress

        t = threading.Thread(target=_run, daemon=True)
        _worker_threads[job_id] = t
        t.start()

    def _cancel_in_thread(job_id):
        _main.worker_manager._job_dirs.pop(job_id, None)
        _worker_threads.pop(job_id, None)

    _main.worker_manager.start = _start_in_thread
    _main.worker_manager.cancel = _cancel_in_thread
    _main.worker_manager._threads = _worker_threads


# ═══════════════════════════════════════════════════════════
# 4. 启动
# ═══════════════════════════════════════════════════════════

PORT = 8766
URL = f"http://127.0.0.1:{PORT}"


def _start_server():
    import uvicorn
    import logging
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _project_dir = Path(__file__).parent
    uvicorn.run(
        _main.app, host="127.0.0.1", port=PORT, log_level="warning",
        reload=not FROZEN,
        reload_dirs=[str(_project_dir / "backend"), str(_project_dir / "frontend")] if not FROZEN else None,
    )


def main():
    # ── 单实例锁 ──
    if not _acquire_singleton():
        print("Video Subber 已在运行中。请查看系统托盘或任务栏中的已有窗口。")
        sys.exit(0)

    print("Video Subber 正在启动...")

    # 后端后台线程
    server = threading.Thread(target=_start_server, daemon=True)
    server.start()

    # 等后端就绪（最多 15 秒，比之前的 5 秒更宽容）
    backend_ready = False
    for _ in range(150):
        time.sleep(0.1)
        try:
            import urllib.request
            r = urllib.request.urlopen(URL + "/api/health", timeout=0.5)
            if r.status == 200:
                backend_ready = True
                break
        except Exception:
            pass

    if not backend_ready:
        print("启动失败：后端服务未能就绪。")
        print("请检查端口 8766 是否被其他程序占用，或尝试重启电脑后重试。")
        _release_singleton()
        sys.exit(1)

    print("后端就绪，打开窗口...")

    import webview
    webview.create_window(
        "Video Subber — 视频字幕生成",
        URL,
        width=1100,
        height=750,
        resizable=True,
        min_size=(800, 650),
    )
    webview.start()

    # 用户关闭窗口 → 退出
    _release_singleton()
    print("已退出。")


if __name__ == "__main__":
    main()
