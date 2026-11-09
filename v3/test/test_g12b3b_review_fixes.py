"""G12 第三批 B · ⑦.5 /review 查出的施工缺陷（2026-09-29，两人审定「现在修」）。

①「N 条长字幕未能拆分」误报：普通两行屏不许计入。
② 源头检查只对英文生效（合同 SC-6）：日语句末词不许被挪。
③ 繁转简：转换时出错也只能原样不动、记一次日志（合同 L-17）。
④ 7 秒自检：只有真正满足窄例外的屏才带 long_ok（合同 L-6）。
⑤ 同一屏被封顶只记一次账。
"""

import copy
import json
import logging

from backend.core import display_timeline as dt
from backend.core import zh_script
from backend.transcriber_v2 import Segment


def _seg(i, start, end, text, zh):
    s = Segment(id=i, start=start, end=end, text=text)
    s.zh = zh
    s.speaker_change = False
    toks = text.split()
    step = (end - start) / len(toks)
    s.words = [{"word": t, "text": t, "start": start + k * step, "end": start + (k + 1) * step}
               for k, t in enumerate(toks)]
    return s


# ── ① 普通两行屏不算「未能拆分」 ──

def test_ordinary_two_line_screens_are_not_counted_as_unsplittable():
    zh = "我们今天早上一起去了湖边散步看日出"            # 17 字、无逗号、4 秒 → 两行正常显示
    segs = [_seg(i, i * 10.0, i * 10.0 + 4.0,
                 "we went to the lake this morning to see sunrise", zh) for i in range(3)]
    rep = dt.TimelineReport()
    dt.build_display_timeline(segs, report=rep)
    assert rep.no_safe_boundary_splits + rep.mapping_failed_splits + rep.single_word_over_max == 0


# ── ② 日语不挪 ──

def _receipt(worker, words, language):
    from test import locked_decision
    decision = locked_decision()
    decision = decision.__class__(**{**decision.__dict__, "language": language}) \
        if hasattr(decision, "__dict__") else decision
    return worker.TranscriptionReceipt(
        routed_primary="qwen", actual_primary="qwen", word_source="qwen_word_timestamps",
        source_text=" ".join(w["text"] for w in words), source_words=copy.deepcopy(words),
        language_decision=decision, engine_received_language={"qwen": language},
        artifact_language_check="ok")


def test_source_check_does_not_move_words_of_non_english_films(tmp_path):
    from backend import worker
    from test.test_source_check import _ok_map
    words = [{"word": "です。", "text": "です。", "start": 10.85, "end": 13.5},
             {"word": "次", "text": "次", "start": 16.0, "end": 16.3}]
    receipt = _receipt(worker, words, "ja")
    assert receipt.language_decision.language == "ja"
    fixed = worker._source_checked_words(receipt, _ok_map(), tmp_path).words
    assert (fixed[0]["start"], fixed[0]["end"]) == (10.85, 13.5)
    assert json.loads((tmp_path / "source_check.json").read_text())["applied"] is False


# ── ③ 转换中途出错 ──

def test_conversion_error_keeps_text_and_logs_once(monkeypatch, caplog):
    def _bad_convert(text, target):
        raise OSError("zhcdict.json missing")

    monkeypatch.setattr(zh_script, "_converter", _bad_convert)
    monkeypatch.setattr(zh_script, "_load_failed", False)

    class S:
        def __init__(self, zh):
            self.zh = zh

    segs = [S("我很開心"), S("這裡")]
    with caplog.at_level(logging.WARNING):
        zh_script.convert_segments(segs, {1: ["我很", "開心"]}, target="zh-hans")
    assert [s.zh for s in segs] == ["我很開心", "這裡"]
    assert caplog.text.count("繁简转换不可用") == 1


# ── ④ long_ok 只给真正的窄例外 ──

def test_long_ok_only_for_narrow_exception(monkeypatch):
    """模拟以后封顶出错漏掉一块 9 秒屏：它不满足窄例外，就不许带 long_ok，自检要能抓住。"""
    seg = _seg(0, 0.0, 9.0, "a b c d e f g h i", "一二三四五六七八九十")
    monkeypatch.setattr(dt, "_RULES", dt._RULES)          # _layout_pass 会改全局规则，测完还原
    monkeypatch.setattr(dt, "_long_screen_allowed", lambda words, zh: False)
    monkeypatch.setattr(dt, "_repair_times",
                        lambda drafts, rep, *_spans: [(0.0, 9.0, "一二三四五六七八九十", False, seg.words)])
    entries, _rep = dt._layout_pass([seg], dt._Rules(pref=False))
    assert entries[0].long_ok is False


# ── ⑤ 封顶只记一次 ──

def test_capped_screen_is_reported_once(monkeypatch):
    monkeypatch.setattr(dt, "_long_screen_allowed", lambda words, zh: False)
    seg = _seg(0, 0.0, 40.0, "word " * 3, "一二三四五六七八九十")
    rep = dt.TimelineReport()
    dt.build_display_timeline([seg], report=rep)
    assert len(rep.capped_no_legal_cut) == 1
