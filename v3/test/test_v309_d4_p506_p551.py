"""D-4：P506 AI 说「不用切」不再被当成出错；P551 删掉永不触发的对齐失败提示。

P506 合同（pipelines/bug-pipeline-2026-11-04-D4.md ② 末尾）：
  C1 整段 ≤80 词、AI 空答案（含编号全被删光）→ 合格，不重试，返回 ([], None)
  C2 >80 词空答案 → 照旧重试一次，再空整篇退回
  C3 中间块空答案 → 照旧重试一次后整篇退回
  C4 合格后不读 parsed[-1]（C1 不抛 IndexError 即证）
  C5 合格的空切点写进断句缓存，续做时复用、不再问 AI
P551：C6 四处删除。
"""
import json
from pathlib import Path

from backend import job_state, worker

ROOT = Path(__file__).resolve().parents[1]


class _LLM:
    max_input_tokens = 1_000_000

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls += 1
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _words(n):
    return [{"word": f"w{i}", "text": f"w{i}", "start": round(i * 0.3, 3),
             "end": round(i * 0.3 + 0.2, 3)} for i in range(n)]


def _collect(n, llm):
    words = _words(n)
    return worker._collect_llm_primary_breaks(
        " ".join(w["text"] for w in words), words, "en", [llm])


EMPTY = json.dumps({"break_after_indices": []})


def test_c1_short_text_empty_answer_is_accepted_without_retry():
    llm = _LLM(EMPTY)
    assert _collect(22, llm) == ([], None)
    assert llm.calls == 1


def test_c1_exactly_80_words_empty_answer_is_accepted():
    llm = _LLM(EMPTY)
    assert _collect(80, llm) == ([], None)
    assert llm.calls == 1


def test_c1_all_indices_dropped_counts_as_no_cut():
    llm = _LLM(json.dumps({"break_after_indices": [999, "x"]}))
    assert _collect(22, llm) == ([], None)
    assert llm.calls == 1


def test_c2_over_80_words_empty_answer_still_retries_then_falls_back():
    llm = _LLM(EMPTY)
    assert _collect(81, llm) == (None, "AI 答案未通过校验")
    assert llm.calls == 2


def test_c2_retry_can_still_rescue_over_80_words():
    llm = _LLM(EMPTY, json.dumps({"break_after_indices": [40]}))
    assert _collect(81, llm) == ([40], None)
    assert llm.calls == 2


def test_c3_middle_chunk_empty_answer_still_falls_back(monkeypatch):
    monkeypatch.setattr(worker, "_p395_chunk_words", lambda _llm: 100)
    # 第一块（0..99）推进到 89，第二块空两次 → 整篇退回
    llm = _LLM(json.dumps({"break_after_indices": [89]}), EMPTY)
    assert _collect(300, llm) == (None, "AI 答案未通过校验")
    assert llm.calls == 3


def _receipt(words):
    from backend.core.whisper_runner import LanguageDecision
    from backend.transcriber_v2 import TranscriptionReceipt

    return TranscriptionReceipt(
        routed_primary="qwen", actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text=" ".join(w["text"] for w in words),
        source_words=[dict(w) for w in words],
        language_decision=LanguageDecision(
            language="en", locked=True, split=False,
            expected_count=7, valid_count=7),
        engine_received_language={"qwen": "en"},
        artifact_language_check="ok",
    )


def test_c5_accepted_empty_breaks_are_cached_and_reused(tmp_path):
    words = _words(22)
    cache_path = tmp_path / "sentence_boundaries.json"

    first = _LLM(EMPTY)
    _groups, reason = worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], _receipt(words)),
        boundary_cache_path=str(cache_path), llm_boundary_backend=first)
    assert reason is None, "≤80 词 AI 说不用切，不该退回规则断句"
    assert first.calls == 1
    assert json.loads(cache_path.read_text())["break_after_indices"] == []

    second = _LLM(EMPTY)
    _groups, reason = worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], _receipt(words)),
        boundary_cache_path=str(cache_path), llm_boundary_backend=second)
    assert reason is None
    assert second.calls == 0, "续做时应复用空切点缓存，不该再问 AI"


def test_c6_alignment_fallback_notice_is_deleted_from_frontend():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for gone in ("showAlignmentFallbackNotice", "时间轴对齐失败",
                 "fa_quality", "alignment_fallback"):
        assert gone not in html, gone


def test_c6_public_state_no_longer_carries_alignment_fields():
    state = job_state.build_public_task_state(
        {"status": "done", "fa_quality": "fallback", "alignment_fallback": True})
    quality = state["result"]["quality"]
    assert "fa_quality" not in quality
    assert "alignment_fallback" not in quality
