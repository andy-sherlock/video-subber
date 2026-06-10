"""Manages subprocess worker lifecycle. Reads JSON-line progress from stdout.

Solution A: Uses asyncio.run_coroutine_threadsafe to safely forward messages
from reader thread to the main event loop where WebSocket connections live.
"""
import subprocess, sys, json, threading, asyncio, logging, os
from pathlib import Path

_log = logging.getLogger(__name__)


class WorkerManager:
    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}
        self._job_dirs: dict[str, Path] = {}
        self._main_loop = None

    def set_main_loop(self, loop):
        """Store the main event loop so reader threads can schedule coroutines on it."""
        self._main_loop = loop

    def _kill_if_alive(self, job_id: str, timeout: float = 5):
        """Pop, kill, and wait for a worker process if one exists for this job_id.
        Shared by start() (defensive cleanup) and cancel() (user-requested stop).
        Waiting is critical: without it, a subsequent start() can race the dying process.
        """
        proc = self._processes.pop(job_id, None)
        self._job_dirs.pop(job_id, None)
        if proc:
            proc.kill()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _log.warning("[worker:%s] kill timeout after %ss", job_id, timeout)

    def start(self, job_id: str, job_dir: Path, video_path: str, config: dict):
        self._kill_if_alive(job_id)

        config["video_path"] = video_path
        config_path = job_dir / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False)

        worker_module = "backend.worker"
        proc = subprocess.Popen(
            [sys.executable, "-m", worker_module, str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self._processes[job_id] = proc
        self._job_dirs[job_id] = job_dir
        threading.Thread(target=self._read_stdout, args=(job_id, proc), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(job_id, proc), daemon=True).start()

    def is_alive(self, job_id: str) -> bool:
        proc = self._processes.get(job_id)
        return proc is not None and proc.poll() is None

    def cancel(self, job_id: str):
        """Kill the worker process. Does NOT delete job_dir.
        Cache files (audio.wav, transcript.json, translated.json) are expensive
        to regenerate. The caller (cancel_job endpoint) decides what to clean up.
        """
        self._kill_if_alive(job_id)

    def _read_stdout(self, job_id: str, proc: subprocess.Popen):
        """Read JSON lines from worker stdout.
        Uses run_coroutine_threadsafe to safely forward messages to the main
        event loop where WebSocket connections are registered. No message queue
        needed — each line is independently scheduled on the main loop."""
        loop = self._main_loop
        last_msg_type = None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                last_msg_type = msg.get("type")
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._forward(job_id, msg), loop
                    )
            except json.JSONDecodeError:
                _log.debug("[worker:%s] non-JSON stdout (likely C++ layer): %s", job_id, line[:200])

        proc.wait()
        if proc.returncode != 0 and last_msg_type not in ("done", "error"):
            error_msg = {
                "type": "error",
                "message": f"处理过程异常中断（退出码 {proc.returncode}），请查看 debug.log 日志排查具体原因。",
            }
            # Disk-first: persist error to meta.json regardless of server state
            job_dir = self._job_dirs.get(job_id)
            if job_dir:
                meta_path = job_dir / "meta.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        meta["status"] = "error"
                        meta["error"] = error_msg["message"]
                        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
            # Best-effort WebSocket broadcast
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._forward(job_id, error_msg), loop
                )

    async def _forward(self, job_id: str, msg: dict):
        """Forward a progress message to all WebSocket clients for this job.
        This runs on the main event loop, so send_json is safe.
        Also syncs progress to JOBS dict so polling returns fresh data after restart."""
        from .ws_manager import ws_manager
        from .main import JOBS
        await ws_manager.send(job_id, msg)
        # Sync progress to JOBS for polling fallback
        if msg.get("type") == "progress" and job_id in JOBS:
            JOBS[job_id]["progress"] = msg.get("percent", 0)
            JOBS[job_id]["stage"] = msg.get("stage", "")
            JOBS[job_id]["message"] = msg.get("message", "")

    def _read_stderr(self, job_id: str, proc: subprocess.Popen):
        for line in proc.stderr:
            line_str = line.rstrip()
            try:
                print(f"[worker:{job_id}] {line_str}", flush=True)
            except UnicodeEncodeError:
                print(f"[worker:{job_id}] [encoding error — run with PYTHONIOENCODING=utf-8]", flush=True)
            _log.debug("[worker:%s stderr] %s", job_id, line_str)


worker_manager = WorkerManager()
