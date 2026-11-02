import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend import main


OPEN_HEADERS = {
    "Host": "127.0.0.1:8766",
    "X-Video-Subber-Action": "open-job-log",
}


def _prepare_job(monkeypatch, tmp_path, job_id="job 1"):
    jobs_dir = tmp_path / "jobs"
    logs_dir = tmp_path / "logs"
    (jobs_dir / job_id).mkdir(parents=True)
    log_dir = logs_dir / job_id
    log_dir.mkdir(parents=True)
    log_path = log_dir / "user.log"
    log_path.write_text("用户可见日志", encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "LOG_DIR", logs_dir)
    return log_path


def test_open_log_endpoint_opens_only_fixed_user_log(monkeypatch, tmp_path):
    log_path = _prepare_job(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr(
        main, "_open_with_default_app", lambda path: opened.append(path), raising=False
    )

    response = TestClient(main.app).post(
        "/api/jobs/job%201/log/open", headers=OPEN_HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert opened == [log_path.resolve()]


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "127.0.0.1:8766"},
        {"Host": "127.0.0.1:8766", "X-Video-Subber-Action": "wrong"},
        {"Host": "attacker.example", "X-Video-Subber-Action": "open-job-log"},
    ],
)
def test_open_log_endpoint_rejects_non_app_requests(
    monkeypatch, tmp_path, headers
):
    _prepare_job(monkeypatch, tmp_path)

    response = TestClient(main.app).post(
        "/api/jobs/job%201/log/open", headers=headers
    )

    assert response.status_code == 403


def test_old_get_endpoint_no_longer_returns_log_text(monkeypatch, tmp_path):
    _prepare_job(monkeypatch, tmp_path)

    response = TestClient(main.app).get("/api/jobs/job%201/log", headers=OPEN_HEADERS)

    assert response.status_code == 404
    assert "用户可见日志" not in response.text


@pytest.mark.parametrize("job_id", ["../other", "missing"])
def test_open_log_endpoint_rejects_invalid_or_missing_job(
    monkeypatch, tmp_path, job_id
):
    jobs_dir = tmp_path / "jobs"
    logs_dir = tmp_path / "logs"
    jobs_dir.mkdir()
    logs_dir.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "LOG_DIR", logs_dir)

    response = TestClient(main.app).post(
        f"/api/jobs/{job_id}/log/open", headers=OPEN_HEADERS
    )

    assert response.status_code == 404


def test_open_log_endpoint_rejects_job_directory_symlink(monkeypatch, tmp_path):
    jobs_dir = tmp_path / "jobs"
    logs_dir = tmp_path / "logs"
    real_job = tmp_path / "real-job"
    jobs_dir.mkdir()
    logs_dir.mkdir()
    real_job.mkdir()
    (jobs_dir / "job1").symlink_to(real_job, target_is_directory=True)
    log_dir = logs_dir / "job1"
    log_dir.mkdir()
    (log_dir / "user.log").write_text("log", encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "LOG_DIR", logs_dir)

    response = TestClient(main.app).post(
        "/api/jobs/job1/log/open", headers=OPEN_HEADERS
    )

    assert response.status_code == 404


@pytest.mark.parametrize("symlink_part", ["log_dir", "log_file"])
def test_open_log_endpoint_rejects_log_symlinks(
    monkeypatch, tmp_path, symlink_part
):
    jobs_dir = tmp_path / "jobs"
    logs_dir = tmp_path / "logs"
    (jobs_dir / "job1").mkdir(parents=True)
    logs_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_log = outside / "user.log"
    outside_log.write_text("outside", encoding="utf-8")
    if symlink_part == "log_dir":
        (logs_dir / "job1").symlink_to(outside, target_is_directory=True)
    else:
        (logs_dir / "job1").mkdir()
        (logs_dir / "job1" / "user.log").symlink_to(outside_log)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "LOG_DIR", logs_dir)

    response = TestClient(main.app).post(
        "/api/jobs/job1/log/open", headers=OPEN_HEADERS
    )

    assert response.status_code == 404


def test_open_log_endpoint_returns_short_error_when_system_open_fails(
    monkeypatch, tmp_path
):
    _prepare_job(monkeypatch, tmp_path)

    def fail_open(_path):
        raise OSError("private system detail")

    monkeypatch.setattr(main, "_open_with_default_app", fail_open, raising=False)

    response = TestClient(main.app).post(
        "/api/jobs/job%201/log/open", headers=OPEN_HEADERS
    )

    assert response.status_code == 500
    assert "打开" in response.json()["error"]
    assert "private system detail" not in response.text


@pytest.mark.parametrize(
    ("platform", "expected_args"),
    [
        ("darwin", ["open", "-a", "TextEdit"]),
        ("linux", ["xdg-open"]),
    ],
)
def test_posix_open_uses_argument_list_without_shell(
    monkeypatch, tmp_path, platform, expected_args
):
    path = tmp_path / "folder with spaces" / "user.log"
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(main.sys, "platform", platform)
    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main._open_with_default_app(path)

    args, kwargs = calls[0]
    assert args == [*expected_args, str(path)]
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 10
    assert kwargs.get("shell") is not True


def test_windows_open_uses_os_startfile(monkeypatch, tmp_path):
    path = tmp_path / "folder with spaces" / "user.log"
    opened = []
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", lambda value: opened.append(value), raising=False)

    main._open_with_default_app(path)

    assert opened == [str(path)]


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["open"]),
        subprocess.TimeoutExpired(["open"], 10),
    ],
)
def test_open_log_endpoint_handles_command_failure(monkeypatch, tmp_path, error):
    _prepare_job(monkeypatch, tmp_path)

    def fail_open(_path):
        raise error

    monkeypatch.setattr(main, "_open_with_default_app", fail_open, raising=False)

    response = TestClient(main.app).post(
        "/api/jobs/job%201/log/open", headers=OPEN_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"error": "无法打开任务日志，请稍后重试"}
