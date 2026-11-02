"""G12 第三批 B · 源头检查（方案-源头检查-2026-09-27 §3、§8、§11、§12）。

识别模型遇到停顿或背景音乐时常把词的开头往前拉（新西兰 `All` 被标成 12.7 秒）。
源头检查在分组之前，从后往前把这些词挪回原位。全部用方案 §8 的真实例子。
"""

import copy

from backend.core.source_check import check_word_times


def _w(text, start, end):
    return {"word": text, "text": text, "start": start, "end": end}


def _by_text(words, text):
    return next(w for w in words if w["text"] == text)


# ── 规则 A：词长 > 2 秒 ──

def test_rule_a_keeps_end_of_stretched_word():
    words = [_w("Excellent.", 636.9, 637.7), _w("All", 637.72, 650.44), _w("right,", 650.44, 650.8)]
    fixed, changes, _skipped = check_word_times(words, [])
    assert (_by_text(fixed, "All")["start"], _by_text(fixed, "All")["end"]) == (650.24, 650.44)
    assert changes[0]["rule"].startswith("A")


def test_rule_a_keeps_start_when_word_ends_sentence():
    words = [_w("the", 827.2, 827.5), _w("hike.", 827.59, 834.79), _w("Next", 836.0, 836.3)]
    fixed, _changes, _skipped = check_word_times(words, [])
    assert (_by_text(fixed, "hike.")["start"], _by_text(fixed, "hike.")["end"]) == (827.59, 827.83)


# ── 规则 B：孤词贴到下一个词前面 ──

def test_rule_b_moves_lonely_word_next_to_following_word():
    words = [_w("then.", 782.5, 783.0), _w("The", 783.62, 784.42),
             _w("secret", 788.50, 788.9), _w("is", 789.0, 789.2)]
    fixed, changes, _skipped = check_word_times(words, [])
    assert (_by_text(fixed, "The")["start"], _by_text(fixed, "The")["end"]) == (788.3, 788.5)
    assert changes[0]["rule"].startswith("B")


def test_rule_b_does_not_drag_a_run_of_connected_words():
    texts = ["A", "little", "sour,", "but", "I'm", "gonna"]
    words = [_w(t, 40.0 + i * 0.4, 40.3 + i * 0.4) for i, t in enumerate(texts)]
    words.append(_w("try", 48.0, 48.3))
    fixed, changes, _skipped = check_word_times(words, [])
    assert fixed == words and changes == []


def test_voice_veto_keeps_lonely_word_when_its_place_has_voice():
    words = [_w("then.", 100.0, 100.5), _w("So", 101.0, 101.4), _w("anyway", 104.0, 104.5)]
    voice = [(100.9, 101.5)]                       # 原位置有人声，新位置没有
    fixed, changes, _skipped = check_word_times(words, voice)
    assert fixed == words and changes == []


# ── §11：挪词不许让词序倒退 ──

def test_move_that_would_reverse_word_order_is_skipped():
    words = [_w("Wanaka", 1950.0, 1951.0), _w("All", 1951.0, 1955.5), _w("today.", 1951.2, 1951.6)]
    fixed, changes, skipped = check_word_times(words, [])
    assert fixed == words and changes == []
    assert skipped and skipped[0]["word"] == "All"
    starts = [w["start"] for w in fixed]
    assert starts == sorted(starts)


# ── 只算一份、不改输入 ──

def test_input_words_are_not_mutated():
    words = [_w("Excellent.", 636.9, 637.7), _w("All", 637.72, 650.44), _w("right,", 650.44, 650.8)]
    original = copy.deepcopy(words)
    check_word_times(words, [])
    assert words == original


def test_change_record_matches_actual_changes():
    words = [_w("then.", 782.5, 783.0), _w("The", 783.62, 784.42),
             _w("secret", 788.50, 788.9), _w("Excellent.", 800.0, 800.5),
             _w("All", 800.52, 812.0), _w("right,", 812.0, 812.3)]
    fixed, changes, _skipped = check_word_times(words, [])
    moved = {i for i, (a, b) in enumerate(zip(words, fixed))
             if (a["start"], a["end"]) != (b["start"], b["end"])}
    assert moved == {c["index"] for c in changes}
    for c in changes:
        assert c["new"] == [fixed[c["index"]]["start"], fixed[c["index"]]["end"]]


# ── §12 合同：修好的时间只算一次，停顿提示与正式分组共用这一份；收据原样保留 ──

import json

from backend.core import sound_map
from test import locked_decision

_IDENTITY = {
    "source_content_sha256": "a" * 64,
    "track_identity": {"index": 0, "codec": "aac", "sample_rate": 48000, "channels": 2},
    "vad_asset_sha256": "b" * 64,
    "vad_parameters": sound_map.canonical_vad_parameters(),
}


def _receipt(worker, words, primary="qwen"):
    return worker.TranscriptionReceipt(
        routed_primary=primary, actual_primary=primary,
        word_source=f"{primary}_word_timestamps",
        source_text=" ".join(w["text"] for w in words),
        source_words=copy.deepcopy(words),
        language_decision=locked_decision(),
        engine_received_language={primary: "en"},
        artifact_language_check="ok",
    )


def _nz_words():
    return [_w("Excellent.", 636.9, 637.7), _w("All", 637.72, 650.44),
            _w("right,", 650.44, 650.8), _w("we", 650.9, 651.1), _w("go.", 651.2, 651.5)]


def _ok_map():
    return sound_map.make_map_record(_IDENTITY, [], total_samples=16000 * 700)


def test_worker_checks_once_and_keeps_receipt_original(tmp_path):
    from backend import worker
    receipt = _receipt(worker, _nz_words())
    fixed = worker._source_checked_words(receipt, _ok_map(), tmp_path)
    assert _by_text(fixed, "All")["start"] == 650.24
    assert _by_text(receipt.source_words, "All")["start"] == 637.72    # 收据原样（SC-0e）
    record = json.loads((tmp_path / "source_check.json").read_text())
    assert record["applied"] is True and record["changes"][0]["word"] == "All"   # SC-7


def test_pause_hints_use_the_checked_words(tmp_path):
    from backend import worker
    receipt = _receipt(worker, _nz_words())
    record = _ok_map()
    fixed = worker._source_checked_words(receipt, record, tmp_path)
    hints_fixed = worker._sound_reference_from_record(receipt, record, words=fixed).hints
    hints_raw = worker._sound_reference_from_record(receipt, record).hints
    assert 0 in {h.word_index for h in hints_fixed}                      # SC-0a
    assert 0 not in {h.word_index for h in hints_raw}


def test_formal_grouping_uses_the_checked_words_on_both_routes(tmp_path):
    from backend import worker
    receipt = _receipt(worker, _nz_words())
    fixed = worker._source_checked_words(receipt, _ok_map(), tmp_path)
    envelope = worker.TranscriptionEnvelope([], receipt)
    groups, _reason = worker._prepare_sentence_groups(envelope, source_words=fixed)  # 没配 Key：备用线路（SC-0f）
    all_word = next(w for g in groups for w in g.words if w["text"] == "All")
    assert all_word["start"] == 650.24                                        # SC-0b

    class _Llm:
        def translate_raw_json(self, user_prompt, system_prompt=None):
            return json.dumps({"break_after_indices": [0]})

    envelope = worker.TranscriptionEnvelope([], _receipt(worker, _nz_words()))
    groups, reason = worker._prepare_sentence_groups(
        envelope, llm_boundary_backend=_Llm(), source_words=fixed)
    all_word = next(w for g in groups for w in g.words if w["text"] == "All")
    assert all_word["start"] == 650.24


def test_whisper_primary_only_records_and_does_not_move(tmp_path):
    from backend import worker
    receipt = _receipt(worker, _nz_words(), primary="whisper")
    fixed = worker._source_checked_words(receipt, _ok_map(), tmp_path)        # SC-6
    assert _by_text(fixed, "All")["start"] == 637.72
    record = json.loads((tmp_path / "source_check.json").read_text())
    assert record["applied"] is False and len(record["changes"]) == 1


def test_unavailable_sound_map_skips_source_check(tmp_path):
    from backend import worker
    receipt = _receipt(worker, _nz_words())
    unavailable = sound_map.make_map_record(
        dict(_IDENTITY, source_content_sha256="none", track_identity="none"), [],
        total_samples=0, map_status="unavailable", reason_code="input_unavailable")
    fixed = worker._source_checked_words(receipt, unavailable, tmp_path)      # SC-3
    assert _by_text(fixed, "All")["start"] == 637.72


# ── §11-2：分组词序类错误要报真实原因，不许报成「连接失败」──

def test_word_order_error_is_not_reported_as_connection_failure(caplog):
    from backend import worker

    class _Llm:
        calls = 0

        def translate_raw_json(self, user_prompt, system_prompt=None):
            _Llm.calls += 1
            return json.dumps({"break_after_indices": [0]})

    words = [_w("One.", 1.0, 1.3), _w("two", 0.5, 0.8), _w("three.", 2.0, 2.3)]  # 词序倒退
    text = " ".join(w["text"] for w in words)
    with caplog.at_level("WARNING"):
        breaks, reason = worker._collect_llm_primary_breaks(text, words, "en", _Llm())
    assert breaks is None
    assert reason != "AI 服务连接失败"
    assert "顺序" in reason
    assert _Llm.calls == 0
