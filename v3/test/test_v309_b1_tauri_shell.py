"""V3.09-B1: Tauri dev shell wiring.

B1 is the Mac development shell. It must keep the existing backend/worker
pipeline intact while adding a desktop wrapper around it.
"""

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
