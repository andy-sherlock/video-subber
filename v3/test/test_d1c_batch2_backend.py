"""D-1 补修第二批（2026-10-03）主程序（main.py）合同测试。

合同见 pipelines/方案-D1补修-第二批-v2-2026-10-03.md。
"""

import io
import json

import pytest

from backend import main


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _platform_http(monkeypatch):
    """替身：按地址回 Claude / Gemini 官方格式的成功回答。"""
    def fake(req, timeout=None):
        if "anthropic.com" in req.full_url:
            reply = {"content": [{"type": "text", "text": '{"ok": true}'}]}
        elif "googleapis.com" in req.full_url:
            reply = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
        else:
            raise AssertionError(f"意外地址 {req.full_url!r}")
        return _Resp(json.dumps(reply).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake)


# ── P519 A19-1：Key 有效时不再报「只能翻译」 ───────────────────────────

@pytest.mark.parametrize("platform,model", [("claude", "claude-sonnet-4-6"), ("gemini", "gemini-2.0-flash")])
def test_gate_passes_claude_and_gemini_when_the_key_works(monkeypatch, platform, model):
    _platform_http(monkeypatch)
    res = main.test_llm_connection({"apiKeys": [{"platform": platform, "key": "fake-key", "model": model}]})
    assert res["ok"] is True and res["translate_only"] is False, res


@pytest.mark.parametrize("platform", ["claude", "gemini"])
def test_gate_still_reports_bad_key_and_no_network(monkeypatch, platform):
    import urllib.error

    def unauthorized(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("urllib.request.urlopen", unauthorized)
    res = main.test_llm_connection({"apiKeys": [{"platform": platform, "key": "bad"}]})
    assert (res["ok"], res["translate_only"], res["kind"]) == (False, False, "auth")

    def offline(req, timeout=None):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    res = main.test_llm_connection({"apiKeys": [{"platform": platform, "key": "k"}]})
    assert (res["ok"], res["translate_only"], res["kind"]) == (False, False, "connection")


# ── P512：Key 只从本机设置文件读 ─────────────────────────────────────────

from test.test_d1b_backend import _meta, _partial_job, _write_job, isolated  # noqa: E402,F401

PAGE_KEYS = [{"platform": "deepseek", "key": "fake-page-key"}]      # 网页（旧写法）带来的
FILE_KEYS = [{"platform": "deepseek", "key": "fake-file-key"}]      # 本机设置文件里的当前 Key


@pytest.fixture
def settings_file(monkeypatch):
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": FILE_KEYS, "translationPriority": ["bing"]},
    )


def test_resume_runs_with_the_settings_file_keys_and_writes_them_back(isolated, tmp_path, settings_file):
    _write_job(tmp_path, "k1", status="interrupted")
    response = main.resume_job("k1", {})
    assert response["status"] == "running"
    assert isolated[0]["apiKeys"] == FILE_KEYS
    assert _meta(tmp_path, "k1")["config"]["apiKeys"] == FILE_KEYS, "写回任务记录（A3 照旧）"


def test_resume_ignores_keys_sent_by_the_page(isolated, tmp_path, settings_file):
    _write_job(tmp_path, "k2", status="interrupted")
    main.resume_job("k2", {"apiKeys": PAGE_KEYS, "aiTranslateOnly": True})
    assert isolated[0]["apiKeys"] == FILE_KEYS
    assert isolated[0]["aiTranslateOnly"] is True, "「只翻译」仍是用户这一次的选择，照收"


def test_partial_retry_runs_with_the_settings_file_keys(isolated, tmp_path, settings_file):
    _partial_job(tmp_path, "k3")
    response = main.partial_action("k3", {"action": "retry", "apiKeys": PAGE_KEYS})
    assert response["ok"] is True
    assert isolated[0]["apiKeys"] == FILE_KEYS
    assert _meta(tmp_path, "k3")["config"]["apiKeys"] == FILE_KEYS


def test_new_job_runs_with_the_settings_file_keys(isolated, tmp_path, settings_file):
    from fastapi.testclient import TestClient

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    config = {"model": "large-v2", "apiKeys": PAGE_KEYS}
    res = TestClient(main.app).post("/api/jobs", json={"local_path": str(video), "config": json.dumps(config)})
    assert res.status_code == 200, res.text
    assert isolated[0]["apiKeys"] == FILE_KEYS
    assert _meta(tmp_path, res.json()["id"])["config"]["apiKeys"] == FILE_KEYS


def _capture_sent_key(monkeypatch):
    sent = []

    def fake(req, timeout=None):
        sent.append(req.headers.get("Authorization") or req.headers.get("X-api-key"))
        return _Resp(json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return sent


def test_gate_reads_keys_from_the_settings_file_only(monkeypatch, settings_file):
    # A12-2 / A12-3 / §8-1：门口检测入口不收 Key——请求里带了也不看
    sent = _capture_sent_key(monkeypatch)
    res = main.llm_gate({"apiKeys": PAGE_KEYS})
    assert res["ok"] is True
    assert sent == ["Bearer fake-file-key"]


def test_gate_without_any_saved_key_reports_no_key(monkeypatch):
    monkeypatch.setattr("backend.config_manager.load_config", lambda: {"apiKeys": []})
    assert main.llm_gate({})["kind"] == "no_key"


def test_settings_drawer_check_tests_the_key_being_typed(monkeypatch, settings_file):
    # §8-1：抽屉里的实时检测测「手里这个 Key」，不是设置文件里的
    sent = _capture_sent_key(monkeypatch)
    res = main.test_llm_connection({"apiKeys": PAGE_KEYS})
    assert res["ok"] is True
    assert sent == ["Bearer fake-page-key"]


# ── P510：导出失败后，出错卡一直给「继续生成字幕文件」 ─────────────────

from backend import job_state  # noqa: E402

_ROUND_FIELDS = ("status", "run_id", "run_kind", "revision", "partial_resume_action")


def _export_fails(job_id):
    """干活程序报：保存文件时出错（磁盘满 / 文件夹没权限）。"""
    run_id = main.JOBS[job_id]["run_id"]
    main._sync_job_from_worker_message(job_id, {
        "type": "error", "run_id": run_id, "message": "保存字幕文件失败：磁盘空间不足",
    })
    return job_state.build_public_task_state(main.JOBS[job_id])


def test_export_keeps_offering_continue_export_after_repeated_failures(isolated, tmp_path, monkeypatch):
    _partial_job(tmp_path, "ex1")

    assert main.partial_action("ex1", {"action": "accept"})["ok"] is True
    state = _export_fails("ex1")
    assert state["actions"]["resume_action"] == "accept", "第一次导出失败：按钮是「继续生成字幕文件」"

    for attempt in (2, 3):
        response = main.resume_job("ex1", {})
        assert response["status"] == "running"
        assert isolated[-1].get("_accept_partial") is True, "继续生成字幕文件：这一轮仍是导出"
        state = _export_fails("ex1")
        assert state["actions"]["resume_action"] == "accept", f"第 {attempt} 次导出失败仍是「继续生成字幕文件」"


@pytest.mark.parametrize("entry", ["partial_retry", "partial_accept", "resume"])
def test_this_round_info_is_the_same_on_disk_and_in_memory(isolated, tmp_path, entry):
    # A10-2：同一份「这一轮运行的信息」写硬盘、写内存
    if entry == "resume":
        _write_job(tmp_path, "rd1", status="interrupted", partial_resume_action="accept",
                   failed_keys=[[1, 0.0, 1.0]], retry_count=1)
        main.resume_job("rd1", {})
    else:
        _partial_job(tmp_path, "rd1")
        main.partial_action("rd1", {"action": "retry" if entry == "partial_retry" else "accept"})
    disk = _meta(tmp_path, "rd1")
    memory = main.JOBS["rd1"]
    for field in _ROUND_FIELDS:
        assert memory.get(field) == disk.get(field), field


# ── P494：外层 local_path 按地址建任务、不复制；坏地址照旧提示 ─────────

def test_local_path_job_is_created_from_the_address_without_copying(isolated, tmp_path):
    from fastapi.testclient import TestClient

    video = tmp_path / "旅行.mp4"
    video.write_bytes(b"video")
    res = TestClient(main.app).post("/api/jobs", json={"local_path": str(video), "config": json.dumps({"model": "large-v2"})})
    assert res.status_code == 200, res.text
    job_dir = tmp_path / res.json()["id"]
    assert len(isolated) == 1
    assert not [p for p in job_dir.iterdir() if p.suffix == ".mp4"], "任务文件夹里不得有视频副本"


@pytest.mark.parametrize("make", ["missing", "folder"])
def test_bad_local_path_is_rejected_without_creating_a_job(isolated, tmp_path, make):
    from fastapi.testclient import TestClient

    target = tmp_path / "nope.mp4"
    if make == "folder":
        target.mkdir()
    before = set(tmp_path.iterdir())
    res = TestClient(main.app).post("/api/jobs", json={"local_path": str(target), "config": "{}"})
    assert res.status_code == 400
    assert "找不到文件" in res.json()["error"]
    assert set(tmp_path.iterdir()) == before and isolated == []
