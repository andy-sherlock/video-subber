from backend.translation.base import TranslationResult
from backend.translation import orchestrator
import json


class _Api:
    name = "用户API"

    def translate_batch(self, texts):
        return TranslationResult(["[翻译失败]"] * len(texts), self.name, failure_kind="auth")

    def translate_raw(self, prompt, max_tokens=512):
        return TranslationResult(["[翻译失败]"], self.name, failure_kind="auth")


def test_api_all_keys_rejected_stops_with_error(monkeypatch):
    """D-1 A5（合同变更）：付费逐段路线不转别的引擎（D-6 P503 起已没有免费引擎）。
    唯一的 Key 被拒 → 任务出错，状态只有「正在使用 API」一段，也不泄露 Key。"""
    import pytest
    from backend.translation.paid_request import PaidAIUnavailable

    monkeypatch.setattr(orchestrator, "_build_llm_backends", lambda keys: [_Api()])
    events = []

    with pytest.raises(PaidAIUnavailable) as info:
        orchestrator.translate_with_orchestrator(
            ["hello"], api_keys=[{"key": "SECRET"}],
            engine_status_callback=events.append,
            progress_callback=lambda *a: None,
        )

    assert [event["phase"] for event in events] == ["trying_api"]
    assert "SECRET" not in repr(events) and "SECRET" not in str(info.value)
    assert all(set(event) <= {"engine_category", "phase", "failure_kind"} for event in events)


def test_main_keeps_engine_status_in_memory_but_not_restart_meta(monkeypatch, tmp_path):
    from backend import main

    job_id = "engine-job"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {"status": "running", "run_id": "run", "config": {}}
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, **meta}})

    main._sync_job_from_worker_message(job_id, {
        "type": "engine_status", "run_id": "run", "engine_category": "api",
        "phase": "trying_api", "failure_kind": None,
    })

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    expected = {
        "engine_category": "api", "phase": "trying_api", "failure_kind": None,
    }
    assert "engine_status" not in saved
    assert main.JOBS[job_id]["engine_status"] == expected
    public = main.job_state.build_public_task_state(main.JOBS[job_id])
    assert public["display_message"] == "正在使用用户配置的 API"
