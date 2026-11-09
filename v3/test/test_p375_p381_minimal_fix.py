"""P375-P381 minimal bugfix regression tests.

These tests intentionally lock the narrow contracts approved in pipeline section ㉞.
P380 is excluded from this file's production scope.
"""

from __future__ import annotations

# P464：`run_qwen_asr` 返回 QwenAsrOutcome（块 + 完整性状态），不再是裸列表。
# stub 必须跟上真模块的契约，否则测试就在撒谎。
def _Outcome(**kw):
    """惰性构造真的 QwenAsrOutcome。

    **不能在顶层 `from backend.core.qwen_asr_runner import ...`**：
    多个测试用 `sys.modules` 把该模块换成假模块；本文件若在那期间被导入
    （例如被别的测试 `from test.xxx import _stub_environment` 拉进来），
    顶层 import 会拿到假模块并炸在 `(unknown location)`。
    `transcriber_v2` 顶层持有真类的引用，不受 sys.modules 替换影响。
    """
    from backend.transcriber_v2 import QwenAsrOutcome
    return QwenAsrOutcome(**kw)

import sys
from backend.core.qwen_asr_runner import (
    count_chunks_missing_punctuation as _real_count_chunks_missing_punctuation,
)
import types
import json
from pathlib import Path
from types import SimpleNamespace


V3_ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (V3_ROOT / relative).read_text(encoding="utf-8")


def test_precise_serial_qwen_forwards_progress_callback(monkeypatch):
    from backend import transcriber_v2

    callback = object()
    seen = {}

    fake_runner = types.ModuleType("backend.core.qwen_asr_runner")
    # 假模块必须带上真模块被用到的符号，否则 transcriber_v2 的
    # 局部 import 会炸。直接挂真实现，避免假货与真货漂移。
    fake_runner.count_chunks_missing_punctuation = (
        _real_count_chunks_missing_punctuation)
    fake_runner.is_qwen_asr_available = lambda: True

    def fake_run(audio_path, **kwargs):
        seen.update(audio_path=audio_path, **kwargs)
        return _Outcome(complete=True, chunks=["qwen-result"])

    fake_runner.run_qwen_asr = fake_run
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_runner)

    result = transcriber_v2._collect_qwen_chunks(
        None,
        {},
        "precise",
        True,
        "audio.wav",
        "en",
        "cache",
        progress_callback=callback,
    )

    assert result.chunks == ["qwen-result"]  # P464：返回 QwenAsrOutcome，块在 .chunks 上
    assert seen["progress_callback"] is callback


def test_parallel_qwen_path_forwards_progress_callback():
    source = _source("backend/transcriber_v2.py")
    # CI-06 后 _run_qwen 改为按值接收语言（def _run_qwen(_language)），锚点随之放宽。
    _start = source.index("def _run_qwen(")
    parallel_block = source[_start:source.index(
        "qwen_thread = threading.Thread", _start
    )]
    assert "progress_callback=progress_callback" in parallel_block


def test_sensevoice_phase_is_emitted_only_when_it_will_run():
    source = _source("backend/transcriber_v2.py")
    assert "will_run_sensevoice = bool(" in source
    assert 'progress_callback({"phase": "analyzing_sound"})' in source
    assert "and is_sensevoice_available()" in source


def test_download_hook_reports_video_and_audio_kind():
    from backend.core.video_downloader import _make_progress_hook

    messages = []
    video_hook = _make_progress_hook(messages.append, kind="video")
    audio_hook = _make_progress_hook(messages.append, kind="audio")

    payload = {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100}
    video_hook(payload)
    audio_hook(payload)

    assert messages == [
        {"download_kind": "video", "download_percent": 50},
        {"download_kind": "audio", "download_percent": 50},
    ]


def test_worker_consumes_download_dict_without_stringifying_it():
    source = _source("backend/worker.py")
    start = source.index("def _dl_cb(")
    block = source[start:source.index("dl_result = download_video", start)]
    assert 'msg.get("download_kind")' in block
    assert 'msg.get("download_percent")' in block
    assert "正在下载视频" in block
    assert "正在下载音频" in block


def test_transcription_ui_shows_phase_text_without_fake_numbers():
    from backend import job_state
    recognizing = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe",
        "transcription_phase": "recognizing_speech",
    })
    analyzing = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe",
        "transcription_phase": "analyzing_sound",
    })
    assert recognizing["display_message"] == "正在识别语音，请稍等"
    assert analyzing["display_message"] == "正在分析声音类型，请稍等"
    assert recognizing["progress"] == dict.fromkeys(job_state.PROGRESS_FIELDS)


def test_transcription_phase_and_phase_only_state_are_persisted(tmp_path,
                                                                monkeypatch):
    """**保护权交接（2026-08-19 · G9/P422）**：原来断言 `transcription_phase`
    出现在 `_TRANSCRIPTION_PROGRESS_FIELDS` 这份白名单里。P422 把那份白名单
    删了（改成反向排除，新增字段零登记），所以改为**真跑一遍**：
    喂一条转录进度消息，断言它真的落进了 meta.json。"""
    import json
    from backend import main as _main

    job_id = "p375job"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(json.dumps(
        {"status": "running", "run_id": "r1", "config": {}}), encoding="utf-8")
    monkeypatch.setattr(_main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(_main, "JOBS", {job_id: {
        "id": job_id, "status": "running", "run_id": "r1", "config": {},
        **_main._empty_run_state()}})

    _main._sync_job_from_worker_message(job_id, {
        "type": "transcription_progress", "run_id": "r1",
        "transcription_phase": "recognizing_speech", "completed_segments": 2,
    })

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert "transcription_phase" not in saved
    assert "completed_segments" not in saved
    assert _main.JOBS[job_id]["transcription_phase"] == "recognizing_speech"
    state = _main.job_state.build_public_task_state(_main.JOBS[job_id])
    assert state["phase"] == "transcribe"
    # P453：识别阶段不再显示段数
    assert "已识别" not in (state["activity"] or "")

    worker_source = _source("backend/worker.py")
    assert "last_completed_segments" in worker_source
    assert "last_processed_duration" in worker_source
    assert 'progress.get("phase")' in worker_source


def test_workflow_progress_rejects_invalid_values_and_is_monotonic_per_run():
    source = _source("frontend/index.html")
    start = source.index("function applyWorkflowProgress")
    block = source[start:source.index("function setJobProgress", start)]
    compact = "".join(block.split())

    assert "workflowProgress==null||workflowProgress===''" in compact
    assert "Number.isFinite(value)" in block
    assert "runId!==job.lastRunId" in compact
    assert "job.lastWorkflowProgress=undefined" in compact
    assert "pct<job.lastWorkflowProgress" in compact
    assert "return" in block


def test_running_progress_callers_forward_run_id_and_transcription_does_not_write_total():
    """**保护权交接（2026-08-19 · G9/P422）**：原来断言前端有两处
    `setJobProgress(...,msg.run_id)` / `(...,j.run_id)` 的调用——WebSocket 一处、
    轮询一处。P422 把两条通道合成一个入口 `applyCurrentState`，所以只剩一处，
    `run_id` 从固定 state 取。保护的事实没变：**进度渲染必须带上 run_id**，
    否则 `applyWorkflowProgress` 无法在新一轮开始时重置单调性。"""
    source = _source("frontend/index.html")
    apply_start = source.index("function applyTaskState")
    apply_block = source[apply_start:source.index("function connectCard", apply_start)]
    compact = "".join(apply_block.split())
    assert "setJobProgress(" in compact
    assert "state.run_id" in compact, "进度渲染没有带上 run_id"
    assert compact.count("setJobProgress(jid,state.percent,state.phase,state.display_message,state.activity,state.run_id)") >= 1


def test_engine_status_only_wins_during_translate_stage():
    """**保护权交接（2026-08-19 · G9/P422）**：判据从 `j.stage` 换成
    `current_state.phase`（不再靠散装顶层字段猜阶段），条件本身一字未改——
    引擎状态仍然只在翻译阶段才抢占进度文案。"""
    from backend import job_state
    state = job_state.build_public_task_state({
        "id": "j", "status": "running", "stage": "transcribe",
        "engine_status": {"phase": "trying_api", "engine_category": "api"},
    })
    assert state["phase"] == "translate"
    assert state["display_message"] == "正在使用用户配置的 API"


def test_p380_chunk_numbers_and_live_api_calls_are_not_added():
    touched = "\n".join(
        _source(path)
        for path in (
            "backend/transcriber_v2.py",
            "backend/core/video_downloader.py",
            "backend/worker.py",
            "backend/core/sentence_splitter.py",
            "backend/main.py",
            "frontend/index.html",
        )
    )
    assert "_compute_chunk_size(" not in touched


def test_per_batch_item_callbacks_keep_global_local_index_and_report_cumulative_stats(monkeypatch):
    from backend.translation import orchestrator

    monkeypatch.setattr(orchestrator, "_split_into_batches", lambda texts: [[texts[0]], texts[1:]])
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _seconds: None)

    class ApiBackend:
        name = "api"

        def translate_batch(self, texts):
            translated = ["甲"] if len(texts) == 1 else ["丙", ""]
            return SimpleNamespace(translated=translated, tokens_used=None, failure_kind=None)

        def translate_raw(self, *_args, **_kwargs):
            return SimpleNamespace(translated=[""], tokens_used=None, failure_kind="empty_translation")

    items = []
    stats = []
    result = orchestrator._translate_llm_per_batch(
        ["A", "C", "E"],
        [ApiBackend()],
        item_result_callback=lambda *args: items.append(args),
        translation_stats_callback=stats.append,
        progress_callback=lambda *a: None,
    )

    # D-1 A5（合同变更）：付费路线修不好的「E」保留原文，以普通结果回报
    # （第 4 个参数 False，不是免费补全），并单独计数。
    assert result[0] == ["甲", "丙", "E"]
    assert items == [
        (0, "甲", True, False),
        (1, "丙", True, False),
        (2, "E", True, False),
    ]
    assert stats == [
        {"api_success_count": 1, "api_total_count": 1, "kept_original_count": 0},
        {"api_success_count": 2, "api_total_count": 3, "kept_original_count": 1},
    ]


def test_public_translation_return_contract_stays_five_tuple(monkeypatch):
    from backend.translation import orchestrator

    monkeypatch.setattr(orchestrator, "_build_llm_backends", lambda _keys: [object()])
    monkeypatch.setattr(
        orchestrator,
        "_translate_llm_per_batch",
        lambda *args, **kwargs: (["甲"], [], None, {}, {}),
    )
    result = orchestrator.translate_with_orchestrator(
        ["A"], item_result_callback=lambda *_args: None,
        progress_callback=lambda *a: None,
    )
    assert isinstance(result, tuple)
    assert len(result) == 5


class _Checkpoint:
    def save(self, *_args, **_kwargs):
        pass


def _segments():
    return [
        SimpleNamespace(id=10 + idx, start=float(idx), end=float(idx + 1), text=text, zh=zh)
        for idx, (text, zh) in enumerate(
            [("A", "已有甲"), ("B", ""), ("C", "已有丙"), ("D", ""), ("E", "")]
        )
    ]


def test_non_cached_resume_translates_only_pending_and_writes_back_by_position(
    monkeypatch, tmp_path
):
    from backend import worker

    segments = _segments()
    translated_path = tmp_path / "translated.json"
    (tmp_path / ".snapshot.json").write_text(
        json.dumps({"10": "快照不应覆盖", "12": "快照也不应覆盖"}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls = []

    def fake_translate(**kwargs):
        calls.append(kwargs["texts"])
        for idx, text in enumerate(("译乙", "译丁", "译戊")):
            kwargs["item_result_callback"](idx, text, True, False)
        kwargs["translation_stats_callback"](
            {"api_success_count": 3, "api_total_count": 3}
        )
        return ["译乙", "译丁", "译戊"], [], None, {}, {}

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator", fake_translate
    )
    result = worker._step_translate(
        "translate", segments, translated_path,
        [{"key": "fake"}], False, {}, tmp_path, 5, _Checkpoint(),
        include_failure_details=True,
    )

    assert calls == [["B", "D", "E"]]
    assert [segment.zh for segment in segments] == ["已有甲", "译乙", "已有丙", "译丁", "译戊"]
    assert len(result) == 5
    # D-6 P503：免费兜底计数（auto_fallback / fallback_*）已随免费翻译删除。
    assert result[4] == {
        "requested_tier": "api_segment",
        "api_success_count": 3,
        "api_total_count": 3,
        # D-1 A5：新增保留原文条数；本例统计回调没报这个数 → None。
        "kept_original_count": None,
    }


def test_snapshot_uses_segment_ids_and_keeps_prior_successes(tmp_path):
    from backend import worker

    path = tmp_path / ".snapshot.json"
    worker._write_translation_snapshot(path, {10: "甲", 12: "丙"})
    restored = worker._read_translation_snapshot(path)
    worker._write_translation_snapshot(path, {**restored, 11: "乙"})

    assert worker._read_translation_snapshot(path) == {10: "甲", 11: "乙", 12: "丙"}


def test_precomputed_splits_are_consumed_by_segment_id(monkeypatch):
    from backend.core import sentence_splitter

    segments = [
        SimpleNamespace(id=20, zh="甲乙", text="A", start=0.0, end=1.0),
        SimpleNamespace(id=30, zh="丙丁", text="B", start=1.0, end=2.0),
    ]
    seen = []
    monkeypatch.setattr(
        sentence_splitter,
        "_build_split_segments",
        lambda segment, parts: seen.append((segment.id, parts)) or [segment, segment],
    )
    sentence_splitter._apply_precomputed_splits(segments, {30: ["丙", "丁"]})
    assert seen == [(30, ["丙", "丁"])]


def test_subtitle_output_error_carries_existing_summary_only_when_given(monkeypatch):
    from backend import worker

    events = []
    monkeypatch.setattr(worker, "emit", lambda event_type, **kwargs: events.append((event_type, kwargs)))
    error = SimpleNamespace(
        stage="subtitle",
        failure_kind="write_failed",
        technical_detail="disk",
        user_message="写入失败",
    )
    summary = {
        "requested_tier": "api_context",
        "api_success_count": 2,
        "api_total_count": 3,
        "auto_fallback": True,
    }
    worker._emit_subtitle_output_error(error, translation_summary=summary)
    worker._emit_subtitle_output_error(error)

    assert events[0][1]["translation_summary"] == summary
    assert "translation_summary" not in events[1][1]


def test_translation_snapshot_is_invalidated_with_translated_cache():
    source = _source("backend/worker.py")
    assert '"translate": [str(translated_path), str(snapshot_path)]' in source


def test_translation_summary_is_in_all_existing_persistence_exits():
    source = _source("backend/main.py")
    assert '_TRANSLATION_SUMMARY_FIELD = "translation_summary"' in source
    # **保护权交接（2026-08-19 · G9/P422）**：原来靠数 `_TRANSLATION_SUMMARY_FIELD`
    # 在几份白名单里各出现一次（≥8 次）来证明「每个落盘出口都带上它」。
    # P422 把那些白名单全删了改成反向排除——**次数必然下降，而保护反而更强**：
    # 现在它跟其他字段一样自动通过每一个出口，不需要在任何地方登记。
    # 真正的保护由 `test_g9_p422_current_state.py::test_c8_落盘过的字段能活过
    # 冷启动而不需要在任何名单里登记` 接管（连没登记过的新字段都能照出来）。
    assert "_PARTIAL_SNAPSHOT_FIELDS" in source
    assert "_RESTORE_EXCLUDED_FROM_META" in source, "反向排除的排除集不见了"
    assert "translation_summary" not in source[
        source.index("_RESTORE_EXCLUDED_FROM_META"):
        source.index("def _restored_from_meta")
    ], "translation_summary 被排除在恢复之外了，刷新后会读不到"


def test_cache_hit_done_message_does_not_clear_existing_summary(monkeypatch):
    from backend import main

    old_summary = {
        "requested_tier": "api_segment",
        "api_success_count": 2,
        "api_total_count": 2,
        "auto_fallback": False,
    }
    job = {"status": "running", "progress": 80, "translation_summary": old_summary}
    monkeypatch.setattr(main, "_persist_job_status", lambda *_args, **_kwargs: True)
    main._sync_current_job_message(
        "job", job, {"type": "done", "success_count": 2, "segment_count": 2}
    )
    assert job["translation_summary"] == old_summary


def test_worker_terminal_paths_attach_private_summary_without_changing_three_tuple():
    source = _source("backend/worker.py")
    assert "translation_result = _step_translate(" in source
    assert "if len(translation_result) == 5:" in source
    assert "translation_summary = None" in source
    assert "translation_summary=translation_summary" in source
    assert "if include_failure_details:" in source
    assert "return failed, sub_splits, ok_count" in source


def test_frontend_done_summary_copy_covers_api_tiers():
    source = _source("frontend/index.html")
    summary_start = source.index("function renderTranslationSummary")
    summary_block = source[summary_start:source.index("function renderCardError", summary_start)]
    # 2026-08-29 产品裁决：没有翻译记录时**不写这一行**。
    # 「历史任务，翻译层级未知」是软件在说「我不知道」——
    # 用户不需要知道我们不知道什么，那一块留空。
    assert "历史任务，翻译层级未知" not in summary_block
    # D-6 P503：免费翻译三行文案已删。
    assert "免费" not in summary_block
    assert "API 上下文专业翻译" in summary_block and "API 逐段翻译" in summary_block
    assert "本轮 API 成功" in summary_block
    done_start = source.index("function renderCardDone")
    done_block = source[done_start:source.index("function dismissRestoredCard", done_start)]
    assert "renderTranslationSummary(jid,msg)" in done_block
