"""V3.09-B1: Tauri dev shell wiring.

B1 is the Mac development shell. It must keep the existing backend/worker
pipeline intact while adding a desktop wrapper around it.
"""

import subprocess
import time
from pathlib import Path


def test_backend_run_accepts_desktop_port(monkeypatch):
    import backend.main as main

    calls = {}

    def fake_run(app_or_path, **kwargs):
        calls["app_or_path"] = app_or_path
        calls.update(kwargs)

    monkeypatch.setattr(main.uvicorn, "run", fake_run)

    main.run(["--host", "127.0.0.1", "--port", "18777", "--no-reload"])

    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 18777
    assert calls["reload"] is False
    assert calls["app_or_path"] is main.app


def test_backend_run_reads_desktop_port_from_environment(monkeypatch):
    import backend.main as main

    calls = {}

    def fake_run(app_or_path, **kwargs):
        calls["app_or_path"] = app_or_path
        calls.update(kwargs)

    monkeypatch.setattr(main.uvicorn, "run", fake_run)
    monkeypatch.setenv("VSUB_PORT", "18778")

    main.run(["--no-reload"])

    assert calls["port"] == 18778
    assert calls["reload"] is False


def test_backend_run_ignores_invalid_desktop_port_environment(monkeypatch):
    import backend.main as main

    calls = {}

    def fake_run(app_or_path, **kwargs):
        calls["app_or_path"] = app_or_path
        calls.update(kwargs)

    monkeypatch.setattr(main.uvicorn, "run", fake_run)
    monkeypatch.setenv("VSUB_PORT", "not-a-port")

    main.run(["--no-reload"])

    assert calls["port"] == 8766
    assert calls["reload"] is False


def test_backend_run_cli_port_overrides_environment(monkeypatch):
    import backend.main as main

    calls = {}

    def fake_run(app_or_path, **kwargs):
        calls["app_or_path"] = app_or_path
        calls.update(kwargs)

    monkeypatch.setattr(main.uvicorn, "run", fake_run)
    monkeypatch.setenv("VSUB_PORT", "18778")

    main.run(["--port", "18779", "--no-reload"])

    assert calls["port"] == 18779
    assert calls["reload"] is False


def test_worker_manager_shutdown_all_kills_every_live_worker():
    from backend.worker_manager import WorkerManager

    class FakeProc:
        def __init__(self):
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True

    manager = WorkerManager()
    proc_a = FakeProc()
    proc_b = FakeProc()
    manager._processes = {"a": proc_a, "b": proc_b}
    manager._job_dirs = {"a": Path("/tmp/a"), "b": Path("/tmp/b")}

    manager.shutdown_all()

    assert proc_a.killed is True
    assert proc_a.waited is True
    assert proc_b.killed is True
    assert proc_b.waited is True
    assert manager._processes == {}
    assert manager._job_dirs == {}


def test_worker_manager_process_lookup_error_still_waits_to_reap_process():
    from backend.worker_manager import WorkerManager

    class AlreadyExitedProc:
        def __init__(self):
            self.waited = False

        def kill(self):
            raise ProcessLookupError("already exited")

        def wait(self, timeout=None):
            self.waited = True

    manager = WorkerManager()
    proc = AlreadyExitedProc()
    manager._processes = {"done": proc}
    manager._job_dirs = {"done": Path("/tmp/done")}

    manager._kill_if_alive("done")

    assert proc.waited is True
    assert manager._processes == {}
    assert manager._job_dirs == {}


def test_worker_manager_shutdown_all_continues_after_single_worker_error():
    from backend.worker_manager import WorkerManager

    class FailingProc:
        def kill(self):
            raise RuntimeError("boom")

        def wait(self, timeout=None):
            raise AssertionError("wait should not run after kill failure")

    class FakeProc:
        def __init__(self):
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True

    manager = WorkerManager()
    survivor = FakeProc()
    manager._processes = {"bad": FailingProc(), "good": survivor}
    manager._job_dirs = {"bad": Path("/tmp/bad"), "good": Path("/tmp/good")}

    manager.shutdown_all()

    assert survivor.killed is True
    assert survivor.waited is True
    assert manager._processes == {}
    assert manager._job_dirs == {}


def test_worker_manager_shutdown_all_kills_real_subprocess():
    from backend.worker_manager import WorkerManager

    proc = subprocess.Popen(["sleep", "30"])
    manager = WorkerManager()
    manager._processes = {"real": proc}
    manager._job_dirs = {"real": Path("/tmp/real")}

    try:
        manager.shutdown_all(timeout=2)
        deadline = time.time() + 3
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_tauri_b1_skeleton_starts_existing_python_backend():
    root = Path(__file__).resolve().parents[1]
    main_rs = root / "src-tauri" / "src" / "main.rs"
    conf = root / "src-tauri" / "tauri.conf.json"
    package_json = root / "package.json"

    assert main_rs.exists()
    assert conf.exists()
    assert package_json.exists()

    source = main_rs.read_text(encoding="utf-8")
    assert ".venv/bin/python" in source
    assert "-m" in source
    assert "backend.main" in source
    assert "VSUB_PORT" in source
    assert "kill_backend" in source


def test_tauri_b1_shutdown_uses_unix_process_group_and_is_idempotent():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    cargo_toml = (root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")

    assert "process_group(0)" in source
    assert "libc::SIGTERM" in source
    assert "libc::SIGKILL" in source
    assert "libc::kill" in source
    assert "guard.take()" in source
    assert "child.try_wait()" in source
    assert "Duration::from_millis(100)" in source
    assert "sleep(Duration::from_secs(5))" not in source
    assert "libc" in cargo_toml


def test_tauri_b1_cleanup_hooks_cover_window_and_process_exit_paths():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    cargo_toml = (root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")

    assert "CloseRequested" in source
    assert "RunEvent::Exit" in source or "RunEvent::ExitRequested" in source
    assert "ctrlc::set_handler" in source
    assert "kill_backend" in source
    assert "ctrlc" in cargo_toml


def test_tauri_b1_startup_failure_and_health_timeout_are_wired():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")

    assert "set_read_timeout" in source
    assert "set_write_timeout" in source
    assert "Duration::from_secs(2)" in source
    assert "Duration::from_secs(30)" in source
    assert "desktop-startup-error.html" in source
    assert "show_startup_error" in source
