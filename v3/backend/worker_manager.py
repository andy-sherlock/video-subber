"""Manages subprocess worker lifecycle. Reads JSON-line progress from stdout.

Solution A: Uses asyncio.run_coroutine_threadsafe to safely forward messages
from reader thread to the main event loop where WebSocket connections live.
"""
import subprocess, sys, json, threading, asyncio, logging, os, uuid
from pathlib import Path

from .env_check import check_ffmpeg

_log = logging.getLogger(__name__)


def _worker_env(run_id: str | None = None, run_kind: str = "normal") -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # P388: 运行身份走环境，不进 config —— 否则会改变配置哈希，
    # 破坏 checkpoint 匹配和任务去重。
    if run_id:
        env["VIDEO_SUBBER_RUN_ID"] = run_id
        env["VIDEO_SUBBER_RUN_KIND"] = run_kind or "normal"
    ffmpeg = check_ffmpeg()
    ffmpeg_path = ffmpeg.get("path")
    if ffmpeg_path and os.path.isabs(ffmpeg_path):
        ffmpeg_dir = str(Path(ffmpeg_path).parent)
        current_path = env.get("PATH", "")
        parts = [p for p in current_path.split(os.pathsep) if p]
        if ffmpeg_dir not in parts:
            env["PATH"] = os.pathsep.join([ffmpeg_dir, *parts])
    return env


class WorkerManager:
    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}
        self._job_dirs: dict[str, Path] = {}
        self._current_runs: dict[str, dict] = {}
        self._main_loop = None
        self._progress_callback = None
        self._exit_callback = None
        self._terminal_callback = None

    def set_main_loop(self, loop):
        """Store the main event loop so reader threads can schedule coroutines on it."""
        self._main_loop = loop

    def set_progress_callback(self, callback):
        """Register the main-service hook that syncs worker messages to job state."""
        self._progress_callback = callback

    def set_exit_callback(self, callback):
        """Register the main-service hook that converges state after process exit."""
        self._exit_callback = callback

    def set_terminal_callback(self, callback):
        """Register the parent hook that owns done/partial/error.

        It runs the delivery transaction, so it must stay off the event loop:
        copying a 1GB file there would freeze the whole server."""
        self._terminal_callback = callback

    def _send(self, job_id: str, message: dict, loop):
        """把一条已经可信的快照发给 WebSocket 客户端。"""
        from .ws_manager import ws_manager
        if not (loop and loop.is_running()):
            return
        future = asyncio.run_coroutine_threadsafe(
            ws_manager.send(job_id, message), loop
        )
        try:
            future.result(timeout=10)
        except Exception as exc:
            _log.warning("[worker:%s] 广播失败: %s", job_id, exc)

    def _handle_terminal(self, job_id: str, msg: dict, loop):
        """终态交给父进程处理，父进程说什么就广播什么。

        事件类型由父进程的 status 决定——父进程在搬运期间生成的
        delivering 快照是 progress，不得被重新标成 done / partial。"""
        if not self._terminal_callback:
            return
        trusted = self._terminal_callback(
            job_id, msg, lambda _snapshot: None
        )
        if not isinstance(trusted, dict) or not trusted.get("status"):
            return
        self._send(job_id, {"type": "state", "state": trusted}, loop)

    def _kill_if_alive(self, job_id: str, timeout: float = 5):
        """Pop, kill, and wait for a worker process if one exists for this job_id.
        Shared by start() (defensive cleanup) and cancel() (user-requested stop).
        Waiting is critical: without it, a subsequent start() can race the dying process.
        """
        proc = self._processes.pop(job_id, None)
        self._job_dirs.pop(job_id, None)
        self._current_runs.pop(job_id, None)
        if proc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _log.warning("[worker:%s] kill timeout after %ss", job_id, timeout)

    def start(
        self,
        job_id: str,
        job_dir: Path,
        video_path: str,
        config: dict,
        *,
        run_id: str | None = None,
        run_kind: str = "normal",
    ) -> str:
        if self.is_alive(job_id):
            raise RuntimeError(f"任务 {job_id} 正在运行，不能重复启动")
        self._processes.pop(job_id, None)
        self._job_dirs.pop(job_id, None)
        self._current_runs.pop(job_id, None)

        config["video_path"] = video_path
        config_path = job_dir / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False)

        # 先定下本轮身份，再启动进程，让 Worker 从环境里拿到同一个编号。
        actual_run_id = run_id or uuid.uuid4().hex
        worker_module = "backend.worker"
        proc = subprocess.Popen(
            [sys.executable, "-m", worker_module, str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(Path(__file__).parent.parent),
            env=_worker_env(actual_run_id, run_kind),
            stdin=subprocess.DEVNULL,
        )
        # A7: Popen 之后的每一步都必须全有或全无。半途失败若把进程留着，
        # 下一次 start() 会和这个没人读、没人杀的孤儿进程抢同一个任务目录。
        try:
            self._processes[job_id] = proc
            self._job_dirs[job_id] = job_dir
            self._current_runs[job_id] = {
                "run_id": actual_run_id,
                "run_kind": run_kind,
                "process": proc,
            }
            threading.Thread(
                target=self._read_stdout,
                args=(job_id, proc, actual_run_id),
                daemon=True,
            ).start()
            threading.Thread(
                target=self._read_stderr, args=(job_id, proc), daemon=True
            ).start()
        except BaseException:
            # 直接对着本地 proc 收尸：异常可能发生在写映射之前，
            # 那时 _kill_if_alive 在字典里找不到它。
            self._processes.pop(job_id, None)
            self._job_dirs.pop(job_id, None)
            self._current_runs.pop(job_id, None)
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _log.warning("[worker:%s] start 回滚时 kill 超时", job_id)
            raise
        return actual_run_id

    def current_run(self, job_id: str) -> dict | None:
        current = self._current_runs.get(job_id)
        return dict(current) if current else None

    def is_alive(self, job_id: str) -> bool:
        proc = self._processes.get(job_id)
        return proc is not None and proc.poll() is None

    def cancel(self, job_id: str):
        """Kill the worker process. Does NOT delete job_dir.
        Cache files (audio.wav, transcript.json, translated.json) are expensive
        to regenerate. The caller (cancel_job endpoint) decides what to clean up.
        """
        self._kill_if_alive(job_id)

    def shutdown_all(self, timeout: float = 5):
        """Kill all tracked workers before the backend process exits."""
        for job_id in list(self._processes):
            try:
                self._kill_if_alive(job_id, timeout=timeout)
            except Exception as exc:
                _log.warning("[worker:%s] shutdown cleanup failed: %s", job_id, exc)

    def _is_current_run(self, job_id: str, proc, run_id: str | None) -> bool:
        if run_id is None:
            return True
        current = self._current_runs.get(job_id)
        return bool(
            current
            and current.get("run_id") == run_id
            and current.get("process") is proc
        )

    def _read_stdout(
        self, job_id: str, proc: subprocess.Popen, run_id: str | None = None
    ):
        """Read JSON lines from worker stdout.
        Uses run_coroutine_threadsafe to safely forward messages to the main
        event loop where WebSocket connections are registered. No message queue
        needed — each line is independently scheduled on the main loop."""
        loop = self._main_loop
        last_msg_type = None
        pending = []
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                last_msg_type = msg.get("type")
                if not self._is_current_run(job_id, proc, run_id):
                    continue
                if run_id is not None:
                    msg["run_id"] = run_id
                if msg.get("type") in self._TERMINAL_TYPES:
                    # 先把排在前面的进度消息发完，保证广播顺序；
                    # 再在本线程里跑交付事务（会阻塞，绝不能占事件循环）。
                    self._drain(job_id, pending)
                    pending = []
                    self._handle_terminal(job_id, msg, loop)
                elif loop and loop.is_running():
                    pending.append(asyncio.run_coroutine_threadsafe(
                        self._forward(job_id, msg), loop
                    ))
            except json.JSONDecodeError:
                _log.debug("[worker:%s] non-JSON stdout (likely C++ layer): %s", job_id, line[:200])

        proc.wait()
        self._drain(job_id, pending)

        if self._is_current_run(job_id, proc, run_id) and self._exit_callback:
            # 退出收敛也可能触发交付（终态消息丢失时靠 done checkpoint 恢复），
            # 同样留在读取线程里跑，只把广播交给事件循环。
            snapshot = self._exit_callback(
                job_id, run_id, proc, proc.returncode, last_msg_type
            )
            if isinstance(snapshot, dict) and snapshot.get("status"):
                self._send(job_id, {"type": "state", "state": snapshot}, loop)

        if self._is_current_run(job_id, proc, run_id):
            if self._processes.get(job_id) is proc:
                self._processes.pop(job_id, None)
                self._job_dirs.pop(job_id, None)
            self._current_runs.pop(job_id, None)

    _TERMINAL_TYPES = ("done", "partial", "error")

    def _drain(self, job_id: str, pending: list) -> None:
        for future in pending:
            try:
                future.result(timeout=10)
            except Exception as exc:
                _log.warning("[worker:%s] progress delivery failed: %s", job_id, exc)

    async def _forward(self, job_id: str, msg: dict):
        """把运行消息交给父进程，再广播父进程返回的公开状态。"""
        from .ws_manager import ws_manager
        snapshot = None
        if self._progress_callback:
            snapshot = self._progress_callback(job_id, msg)
        if not isinstance(snapshot, dict):
            return
        await ws_manager.send(job_id, {"type": "state", "state": snapshot})

    def _read_stderr(self, job_id: str, proc: subprocess.Popen):
        for line in proc.stderr:
            line_str = line.rstrip()
            try:
                print(f"[worker:{job_id}] {line_str}", flush=True)
            except UnicodeEncodeError:
                print(f"[worker:{job_id}] [encoding error — run with PYTHONIOENCODING=utf-8]", flush=True)
            _log.debug("[worker:%s stderr] %s", job_id, line_str)


worker_manager = WorkerManager()
