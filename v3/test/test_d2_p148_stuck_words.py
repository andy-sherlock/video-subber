"""D-2 · P148：识别模型在一段里卡住，同一个词连写几十遍。

**用户看到的**：09-03 新西兰片头 30 秒音乐里，识别写出 `Sam Sam Sam…`（30 遍）。

**病根**：两个识别模型都只有「整部片」检查（三成以上段落一模一样才判坏），
一段里连写几十遍，占全片不到一成，两道检查都看不见。

**改法（锁定方案）**：在正式词流的统一入口（`worker._source_checked_words`，
不管千问还是 Whisper 当主力都经过这里）加一条检查——同一个词连续出现 ≥10 遍，
这串词不用，那里留空，任务卡告诉用户「第 X 分钟识别失败」。原文留在识别收据里。
门槛 10：真人最多连说 5 遍（「No, no, no, no, no」），坏掉的是 30 遍。
不做：换另一个模型同一时段的文字（标准档只跑一个模型）；中文、日文（按字数会撞上「哈哈哈」）。

Run: cd v3 && .venv/bin/python -m pytest test/test_d2_p148_stuck_words.py -v
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import worker
from backend.core import sound_map
from backend.core.source_check import find_stuck_word_runs
from test import locked_decision
from test.test_source_check import _IDENTITY, _ok_map


def _w(text, start, end=None):
    return {"word": text, "text": text, "start": start,
            "end": start + 0.15 if end is None else end}


def _stuck_film(repeats=30, word="Sam", at=2.0):
    """09-03 的形状：片头一串卡住的词，后面是正常说话。"""
    words = [_w(word, at + i * 0.2) for i in range(repeats)]
    words += [_w("What's", 70.0), _w("up,", 70.3), _w("Turtle.", 70.6)]
    return words


def _receipt(words, primary="qwen", language="en"):
    return worker.TranscriptionReceipt(
        routed_primary=primary, actual_primary=primary,
        word_source=f"{primary}_word_timestamps",
        source_text=" ".join(w["text"] for w in words),
        source_words=copy.deepcopy(words),
        language_decision=locked_decision(language),
        engine_received_language={primary: language},
        artifact_language_check="ok")


def _unavailable_map():
    return sound_map.make_map_record(
        dict(_IDENTITY, source_content_sha256="none", track_identity="none"), [],
        total_samples=0, map_status="unavailable", reason_code="input_unavailable")


# ── 找卡住的串 ──

def test_ten_in_a_row_is_stuck_nine_is_not():
    assert find_stuck_word_runs([_w("no", i) for i in range(10)]) == [(0, 10)]
    assert find_stuck_word_runs([_w("no", i) for i in range(9)]) == []


def test_same_word_ignores_case_and_punctuation():
    words = [_w("Sam,", 0)] + [_w("sam", i) for i in range(1, 9)] + [_w("SAM.", 9)]
    assert find_stuck_word_runs(words) == [(0, 10)]


def test_punctuation_only_token_breaks_the_run():
    words = [_w("go", i) for i in range(5)] + [_w("…", 5)] + [_w("go", 6 + i) for i in range(5)]
    assert find_stuck_word_runs(words) == []


# ── 正式词流入口：两个模型都管 ──

def test_stuck_run_is_removed_for_both_engines(tmp_path):
    for primary in ("qwen", "whisper"):
        checked = worker._source_checked_words(
            _receipt(_stuck_film(), primary), _ok_map(), tmp_path)
        assert [w["text"] for w in checked.words] == ["What's", "up,", "Turtle."], primary


def test_check_runs_even_when_sound_map_is_unavailable(tmp_path):
    """人声地图不可用时源头检查整个跳过，卡住检查不能跟着跳过。"""
    checked = worker._source_checked_words(
        _receipt(_stuck_film()), _unavailable_map(), tmp_path)
    assert "Sam" not in {w["text"] for w in checked.words}


def test_receipt_keeps_the_original_words(tmp_path):
    receipt = _receipt(_stuck_film())
    worker._source_checked_words(receipt, _ok_map(), tmp_path)
    assert sum(w["text"] == "Sam" for w in receipt.source_words) == 30


def test_real_speaker_repeating_five_times_is_kept(tmp_path):
    words = [_w("No,", i * 0.3) for i in range(5)] + [_w("thanks.", 2.0)]
    checked = worker._source_checked_words(_receipt(words), _ok_map(), tmp_path)
    assert len(checked.words) == 6 and checked.stuck_note is None
    assert checked.text == _receipt(words).source_text


def test_chinese_and_japanese_are_not_checked(tmp_path):
    """按字数会把笑声「哈哈哈…」误判成卡住；卡住的证据只在英文片里出现过。"""
    laugh = [_w("哈", i * 0.1) for i in range(12)] + [_w("好。", 2.0)]
    for language in ("zh", "ja"):
        checked = worker._source_checked_words(
            _receipt(laugh, language=language), _ok_map(), tmp_path)
        assert len(checked.words) == 13, language


def test_stream_that_is_only_a_stuck_run_is_not_emptied(tmp_path):
    """和「零宽同词串」同一条边界：摘除不得把正式词流摘成空。"""
    words = [_w("Sam", i * 0.2) for i in range(12)]
    checked = worker._source_checked_words(_receipt(words), _ok_map(), tmp_path)
    assert len(checked.words) == 12 and checked.stuck_note is None


# ── 组句接得住 + 任务卡告知 ──

def test_grouping_uses_removed_words_and_rebuilt_text(tmp_path):
    """全文必须跟着词一起变，否则组句校验「全文与词级文字不一致」整篇抛错。"""
    receipt = _receipt(_stuck_film())
    checked = worker._source_checked_words(receipt, _ok_map(), tmp_path)
    groups, _reason = worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], receipt),
        source_words=checked.words, source_text=checked.text)
    assert " ".join(g.text for g in groups) == "What's up, Turtle."


def test_note_names_the_minutes(tmp_path):
    words = _stuck_film(at=2.0) + [_w("Sam", 125.0 + i * 0.2) for i in range(10)]
    checked = worker._source_checked_words(_receipt(words), _ok_map(), tmp_path)
    assert checked.stuck_note == "第 1、3 分钟识别失败，那里的字幕已留空。"


def test_note_joins_the_transcription_shortfall():
    """走现有的「识别说明」通道：终态消息、持久状态、任务卡第一行都已接好。"""
    note = "第 1 分钟识别失败，那里的字幕已留空。"
    assert worker._with_stuck_note(None, note) == {"severity": "info", "message": note}
    existing = {"severity": "error", "message": "语音识别未完成。", "reason": "x"}
    merged = worker._with_stuck_note(existing, note)
    assert merged == {"severity": "error", "message": "语音识别未完成。 " + note,
                      "reason": "x"}
    assert existing["message"] == "语音识别未完成。"
    assert worker._with_stuck_note(existing, None) is existing


def test_removing_the_run_does_not_change_source_check_timing(tmp_path):
    """⑦.5：先摘串再做源头检查，会让紧挨卡住串的词后面凭空多出一大段空白，
    「孤词贴到下一个词前面」那条规则就会把 0 秒的 `Hi,` 挪到 70 秒。
    摘串必须在源头检查之后，源头检查的结果一个字不能变。"""
    words = [_w("Hi,", 0.0, 0.3)] + _stuck_film(at=0.5)
    checked = worker._source_checked_words(_receipt(words), _ok_map(), tmp_path)
    hi = next(w for w in checked.words if w["text"] == "Hi,")
    assert (hi["start"], hi["end"]) == (0.0, 0.3)


# ── 真跑 worker.main()：提示只跟着「完成 / 部分完成」走 ──

def _main_with_stuck_note(monkeypatch, tmp_path, **kwargs):
    from types import SimpleNamespace
    from test.test_p367_worker_terminal_flow import _run_worker
    note = "第 1 分钟识别失败，那里的字幕已留空。"
    real = worker._source_checked_words
    calls = []

    def _with_note(receipt, map_record, cache_dir):
        calls.append(1)
        checked = real(receipt, map_record, cache_dir)
        return SimpleNamespace(words=checked.words, text=checked.text, stuck_note=note)

    monkeypatch.setattr(worker, "_source_checked_words", _with_note)
    messages, _calls, _ck = _run_worker(monkeypatch, tmp_path, **kwargs)
    assert calls, "main() 必须经过正式词流入口"
    return messages, note


def test_done_message_carries_the_stuck_note(monkeypatch, tmp_path):
    messages, note = _main_with_stuck_note(monkeypatch, tmp_path, all_success=True)
    done = next(m for m in messages if m["type"] == "done")
    assert done["transcription_shortfall"] == {"severity": "info", "message": note}


def test_partial_message_with_output_carries_the_stuck_note(monkeypatch, tmp_path):
    """接受部分结果、交付了成品的部分完成。（翻译失败、尚未出成品的那条 partial
    由 D-3 补上，见 `test_d3_ui_truth.py`。）"""
    messages, note = _main_with_stuck_note(monkeypatch, tmp_path, accept=True)
    partial = next(m for m in messages if m["type"] == "partial")
    assert partial["transcription_shortfall"]["message"] == note


def test_failed_task_does_not_carry_the_stuck_note(monkeypatch, tmp_path):
    """⑦.5：出错时这句话和失败原因无关，而且带着它的失败任务会在刷新后被重新列出
    （`main.py` 把带转录欠账说明的失败任务当成「有行动线索」）。只给交付成品的终态。"""
    messages, _note = _main_with_stuck_note(
        monkeypatch, tmp_path, all_success=True, terminal_failure="ordinary")
    error = next(m for m in messages if m["type"] == "error")
    assert not error.get("transcription_shortfall")
