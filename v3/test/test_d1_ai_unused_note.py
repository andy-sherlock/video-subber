"""D-1 C12 / A5：任务卡说明的数据来源。

  · 「没用上 AI」只在该步结束时记一次（步骤 + 原因），随任务结果存，前端现算文字；
  · 三种原因分开：平台不支持（用户选「只用 AI 翻译」）/ AI 回复格式有误 /
    识别结果校验未通过；长句切分不出说明；
  · 成功对照（含 no_change、读回已有成品、没跑断句）不得出说明；
  · 重试 / 接受部分结果时不跑润色 → 沿用上一轮的润色说明（只看最终实际采用的结果）；
  · 保留原文条数进入公开状态，刷新后还在。
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import job_state


def _notes(**kwargs):
    from backend import worker

    base = dict(translate_only=False, break_reason=None, refine_status=None, previous=None)
    base.update(kwargs)
    return worker._ai_unused_notes(**base)


def test_success_has_no_note():
    assert _notes() == []


def test_translate_only_choice_note_covers_breaks_and_refine():
    assert _notes(translate_only=True, break_reason="未配置 API") == [
        {"step": "breaks_and_refine", "reason": "platform_unsupported"}
    ]


def test_break_format_failure_note():
    assert _notes(break_reason="AI 答案未通过校验") == [{"step": "breaks", "reason": "format"}]


def test_break_validation_failure_note():
    assert _notes(break_reason="断句输入校验失败：词的时间顺序倒退") == [
        {"step": "breaks", "reason": "validation"}
    ]


def test_free_mode_rule_breaks_has_no_note():
    assert _notes(break_reason="未配置 API") == []


def test_refine_format_failure_note():
    assert _notes(refine_status="format_failed") == [{"step": "refine", "reason": "format"}]


def test_multiple_steps_are_listed_together_in_order():
    assert _notes(break_reason="AI 答案未通过校验", refine_status="format_failed") == [
        {"step": "breaks", "reason": "format"},
        {"step": "refine", "reason": "format"},
    ]


def test_refine_not_run_carries_previous_refine_note_only():
    previous = [{"step": "breaks", "reason": "format"}, {"step": "refine", "reason": "format"}]
    assert _notes(refine_status="not_run", previous=previous) == [
        {"step": "refine", "reason": "format"}
    ]


def test_refine_not_run_without_previous_note_has_none():
    assert _notes(refine_status="not_run", previous=[]) == []


def test_previous_notes_read_from_job_meta(tmp_path):
    from backend import worker
    import json

    (tmp_path / "meta.json").write_text(json.dumps({
        "translation_summary": {"ai_unused": [{"step": "refine", "reason": "format"}]}
    }), encoding="utf-8")
    assert worker._previous_ai_unused(tmp_path) == [{"step": "refine", "reason": "format"}]
    assert worker._previous_ai_unused(tmp_path / "missing") == []


# ── 公开状态（刷新后还在） ─────────────────────────────

def _public_summary(summary):
    state = job_state.build_public_task_state({
        "id": "j1", "status": "done", "translation_summary": summary,
    })
    return state["result"]["quality"]["translation_summary"]


def test_public_state_carries_kept_original_count_and_ai_unused():
    public = _public_summary({
        "api_success_count": 8, "api_total_count": 10, "kept_original_count": 2,
        "ai_unused": [{"step": "refine", "reason": "format"}],
    })
    assert public["kept_original_count"] == 2
    assert public["ai_unused"] == [{"step": "refine", "reason": "format"}]


def test_public_state_drops_unknown_ai_unused_values():
    public = _public_summary({
        "api_success_count": 1, "api_total_count": 1,
        "ai_unused": [
            {"step": "refine", "reason": "format"},
            {"step": "<script>", "reason": "format"},
            {"step": "breaks", "reason": "whatever"},
            "not a dict",
        ],
    })
    assert public["ai_unused"] == [{"step": "refine", "reason": "format"}]


# ── 出错时的文字（不被 _friendly_error 改写） ─────────────────────────────

def test_unavailable_error_message_shown_as_is():
    from backend import worker
    from backend.translation.paid_request import PaidAIUnavailable

    error = PaidAIUnavailable("auth", "DeepSeek 的 API Key 全部被拒绝，请检查 Key 是否正确、是否已过期")
    assert worker._terminal_error_message(error) == str(error)
    assert worker._terminal_error_message(RuntimeError("Timeout")) == worker._friendly_error("Timeout")


# ── 主流程接线：worker.main 真跑（生产调用点） ─────────────────────────────

import json as _json

import pytest


def _run_main(monkeypatch, tmp_path, *, config_extra=None, refine_status=None,
              translate_side_effect=None):
    from backend import worker
    from test import locked_decision
    from test.test_p389_sentence_groups import _segments_from_words, _words

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    video_path = job_dir / "input.mp4"
    video_path.write_bytes(b"video")
    config = {
        "video_path": str(video_path), "video_filename": "input.mp4",
        "outputMode": "srt", "language": "en", "transcriptionTier": "standard",
        "apiKeys": [{"platform": "claude", "key": "k"}],
    }
    config.update(config_extra or {})
    config_path = job_dir / "config.json"
    config_path.write_text(_json.dumps(config), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)

    def fake_transcribe(*args):
        words = _words(["The", "only", "path."])
        receipt = worker.TranscriptionReceipt(
            routed_primary="qwen", actual_primary="qwen",
            word_source="qwen_word_timestamps", source_text="The only path.",
            source_words=words, language_decision=locked_decision(),
            artifact_language_check="ok",
        )
        return worker.TranscriptionEnvelope(_segments_from_words(words, [(0, 3)]), receipt)

    seen = {}
    emitted = []

    def spy_prepare(transcription, **kwargs):
        seen["break_keys"] = kwargs.get("api_keys")
        return transcription.segments, ("未配置 API" if not kwargs.get("api_keys") else None)

    def spy_refiner(api_keys, *args, **kwargs):
        seen["refine_keys"] = api_keys
        return refine_status

    def fake_translate(*args, **kwargs):
        if translate_side_effect:
            raise translate_side_effect
        return ([], [], len(args[1]), {}, {"api_success_count": 1, "api_total_count": 1})

    monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "d1-note")
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(worker, "_validate_config", lambda config: None)
    monkeypatch.setattr(worker, "_download_source_if_url", lambda config, directory, path: path)
    monkeypatch.setattr(worker, "find_versioned_cache", lambda base, path, **_k: (cache, "hash"))
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(worker, "_read_checkpoint_meta", lambda path: {})
    monkeypatch.setattr(worker, "_invalidate_stale_caches", lambda *a: "start")
    monkeypatch.setattr(worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    monkeypatch.setattr(worker, "_step_transcribe", fake_transcribe)
    monkeypatch.setattr(worker, "_prepare_sentence_groups", spy_prepare)
    monkeypatch.setattr(worker, "_run_transcript_refiner", spy_refiner)
    monkeypatch.setattr(worker, "_step_translate", fake_translate)
    monkeypatch.setattr(worker, "_step_subtitle", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_step_burn_or_mux", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_validate_output_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker, "_deliver_outputs",
        lambda *args, on_delivered, **kwargs: on_delivered() or True,
    )
    monkeypatch.setattr(worker, "emit", lambda kind, **fields: emitted.append((kind, fields)))
    worker.main()
    return seen, emitted


def _terminal(emitted):
    return [(kind, fields) for kind, fields in emitted if kind in {"done", "partial", "error"}][-1]


def test_main_translate_only_choice_skips_ai_breaks_and_refine_and_notes_it(monkeypatch, tmp_path):
    seen, emitted = _run_main(monkeypatch, tmp_path, config_extra={"aiTranslateOnly": True})
    assert not seen["break_keys"] and not seen["refine_keys"]
    kind, fields = _terminal(emitted)
    assert kind == "done"
    assert fields["translation_summary"]["ai_unused"] == [
        {"step": "breaks_and_refine", "reason": "platform_unsupported"}
    ]


def test_main_without_choice_uses_ai_and_has_no_note(monkeypatch, tmp_path):
    seen, emitted = _run_main(monkeypatch, tmp_path)
    assert seen["break_keys"] and seen["refine_keys"]
    kind, fields = _terminal(emitted)
    assert kind == "done" and "ai_unused" not in fields["translation_summary"]


def test_main_refine_format_failure_is_noted(monkeypatch, tmp_path):
    _seen, emitted = _run_main(monkeypatch, tmp_path, refine_status="format_failed")
    kind, fields = _terminal(emitted)
    assert fields["translation_summary"]["ai_unused"] == [{"step": "refine", "reason": "format"}]


def test_main_unavailable_error_is_shown_as_is(monkeypatch, tmp_path):
    from backend.translation.paid_request import PaidAIUnavailable

    error = PaidAIUnavailable("auth", "Claude 的 API Key 全部被拒绝，请检查 Key 是否正确、是否已过期")
    _seen, emitted = _run_main(monkeypatch, tmp_path, translate_side_effect=error)
    kind, fields = _terminal(emitted)
    assert kind == "error" and fields["message"] == str(error)


def test_step_translate_summary_carries_kept_original_count(tmp_path):
    """D-1 A5：保留原文条数从翻译统计进入任务结果摘要（任务卡「有 N 句未能翻译」）。"""
    from unittest.mock import patch
    from backend import worker

    segments = [worker.Segment(0, 0.0, 1.0, "one"), worker.Segment(1, 1.0, 2.0, "two")]

    def _spy(**kwargs):
        kwargs["translation_stats_callback"]({
            "api_success_count": 1, "api_total_count": 2, "kept_original_count": 1,
        })
        return ["一", "two"], [], None, {}, {}

    class _Ck:
        def save(self, *a, **k):
            pass

    with patch("backend.translation.orchestrator.translate_with_orchestrator", _spy):
        result = worker._step_translate(
            "translate", segments, tmp_path / "translated.json", "speed", ["google"],
            [{"platform": "deepseek", "key": "k"}], False, {}, tmp_path, len(segments), _Ck(),
            include_failure_details=True,
        )
    summary = result[-1]
    assert summary["kept_original_count"] == 1
    assert summary["auto_fallback"] is False
