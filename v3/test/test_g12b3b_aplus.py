"""G12 第三批 B · A+ 分组（施工合同 A-1 / A-2 / A-3 / A-4 / A-10）。

合同来源：pipelines/方案-断句A加-第二版-2026-09-25.md §4 规则 1、§10、§12 规则 4、
§15.1、§16（丙）、§11；落地表见 pipelines/bug-pipeline-2026-09-28.md ⑤.5。

全部走生产入口 ``resolve_llm_primary_breaks``（语义闸在生产里只有这一个调用处）。
"""

import pytest

from backend.core import qwen_asr_runner
from backend.core import sentence_groups as sg


def _words(texts, *, dur=0.3, gap=0.1):
    out, clock = [], 0.0
    for text in texts:
        out.append({"word": text, "text": text,
                    "start": round(clock, 3), "end": round(clock + dur, 3)})
        clock += dur + gap
    return out


def _resolve(words, breaks, language="en"):
    full_text = " ".join(w["text"] for w in words)
    final, added = sg.resolve_llm_primary_breaks(
        full_text, words, break_after_indices=breaks, language=language)
    return final, added


def _idx(words, text, start=0):
    return next(i for i in range(start, len(words)) if words[i]["text"] == text)


# ── A-1 规则 1：组尾无任何标点、下一个词小写 → 撤刀 ──

def test_rule1_removes_cut_between_how_and_to():
    w = _words("I would like to learn how to bake a pie.".split())
    cut = _idx(w, "how")
    final, _ = _resolve(w, [cut])
    assert cut not in final


def test_rule1_keeps_cut_before_capitalised_word():
    # 大写专名接续属于明确接受的残留（A+ §9），规则 1 不管
    w = _words("I told her English is hard.".split())
    cut = _idx(w, "her")
    final, _ = _resolve(w, [cut])
    assert cut in final


def test_rule1_keeps_cut_after_punctuation():
    # 前一组 7 词，规则 4（≤4 词逗号小组）不管；只测「有标点时规则 1 不撤」
    w = _words("After the long day we went home, then we slept well.".split())
    cut = _idx(w, "home,")
    final, _ = _resolve(w, [cut])
    assert cut in final


def test_rule1_does_not_apply_to_cjk():
    w = _words("I would like to learn how to bake a pie.".split())
    cut = _idx(w, "how")
    assert sg.find_unfinished_group_endings(w, [cut], language="ja") == []


# ── A-2 §10：连接词 + I 系列 → 撤刀；「for you ‖ I think」不撤 ──

def test_section10_removes_cut_between_and_then_and_I():
    w = _words("I was kind of average, and then I just found some books.".split())
    cut = _idx(w, "then")
    final, _ = _resolve(w, [cut])
    assert cut not in final


def test_section10_keeps_question_answer_cut():
    w = _words("How easy was it for you I think it was easy.".split())
    cut = _idx(w, "you")
    final, _ = _resolve(w, [cut])
    assert cut in final


# ── A-3 规则 4：逗号结尾且整组 ≤4 词 → 并入下一组 ──

def test_rule4_merges_four_word_comma_group():
    w = _words("Look at that. And oh my god, I got a scoop of ice cream.".split())
    first = _idx(w, "that.")
    cut = _idx(w, "god,")
    final, _ = _resolve(w, [first, cut])
    assert first in final
    assert cut not in final


def test_rule4_keeps_five_word_comma_group():
    w = _words("Look at that. And then oh my god, I got a scoop of ice cream.".split())
    first = _idx(w, "that.")
    cut = _idx(w, "god,")
    final, _ = _resolve(w, [first, cut])
    assert cut in final


# ── A-4 丙：撤刀会超上限就不撤；右端取下一个实际保留的刀（外审第二轮第 1 条反例）──

def test_keep_cut_when_removal_would_exceed_limits():
    # 30 个 "ordinary"（每个 9 字符含空格）连成一组 = 269 字符 > 240。
    # 刀 19 落在 "and" 上被原有语义闸撤掉；刀 9 若再被规则 1 撤掉，0–29 连成超限组。
    w = _words(["ordinary"] * 30, dur=0.1, gap=0.0)
    w[19]["text"] = w[19]["word"] = "and"
    final, added = _resolve(w, [9, 19])
    assert 9 in final and 9 not in added


def test_remove_cut_when_merged_group_fits():
    # 对照：只有 20 个词、不超限 → 规则 1 照撤
    w = _words(["ordinary"] * 20, dur=0.1, gap=0.0)
    w[19]["text"] = w[19]["word"] = "and."
    final, _ = _resolve(w, [9])
    assert 9 not in final


def test_rule1_chain_stops_before_group_exceeds_limits():
    # 无标点长段：AI 每 10 个词一刀；规则 1 连撤时从真正组头累计，撤到会超限就停
    w = _words(["ordinary"] * 60, dur=0.1, gap=0.0)
    breaks = [9, 19, 29, 39, 49]
    final, added = _resolve(w, breaks)
    assert not added, "丙生效时不该再靠保险丝硬切"
    group_start = 0
    for end in [*final, len(w) - 1]:
        text = " ".join(x["text"] for x in w[group_start:end + 1])
        assert len(text) <= 240
        group_start = end + 1
    assert len(final) < len(breaks), "能撤的刀要撤"


# ── A-10 §11：分组不再贴第二遍标点；文字一致性检查照旧 ──

def test_grouping_does_not_reattach_punctuation(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("分组不许再贴第二遍标点")

    monkeypatch.setattr(qwen_asr_runner, "_attach_full_text_punctuation", _boom)
    w = _words(['"Yeah,', "that", "is", "right.", "We", "went", "home."])
    full_text = " ".join(x["text"] for x in w)
    _resolve(w, [3])
    sg.rebuild_sentence_groups_from_raw_break_indices(
        full_text, w, break_after_indices=[3], language="en")
    sg.build_sentence_groups(full_text, w, language="en")


def test_text_consistency_check_still_raises():
    w = _words(["We", "went", "home."])
    with pytest.raises(sg.SentenceGroupingError):
        sg.resolve_llm_primary_breaks(
            "We went somewhere else.", w, break_after_indices=[0], language="en")


# ── A-5 规则 2：停顿提示处、前词带句末或逗号类标点 → 强制切 ──

from backend.core.sound_map import SoundHint


def _timed(pairs):
    """pairs: [(text, start, end), ...]"""
    return [{"word": t, "text": t, "start": s, "end": e} for t, s, e in pairs]


def _hint(i):
    return SoundHint(word_index=i, gap="9.000", voiced_total="0.000")


def _resolve_h(words, breaks, hints, **kw):
    full_text = " ".join(w["text"] for w in words)
    return sg.resolve_llm_primary_breaks(
        full_text, words, break_after_indices=breaks, language="en",
        sound_hints=hints, **kw)


def test_rule2_forces_cut_at_sentence_end_pause_even_if_ai_did_not_cut():
    w = _timed([("Let's", 0, .3), ("carry", .4, .7), ("on.", .8, 1.1),
                ("That's", 12.9, 13.2), ("it", 13.3, 13.6), ("folks.", 13.7, 14.0)])
    final, _ = _resolve_h(w, [], (_hint(2),))
    assert 2 in final


def test_rule2_forces_cut_at_comma_pause():
    w = _timed([("Okay", 0, .3), ("so", .4, .7), ("well,", .8, 1.1),
                ("well", 8.5, 8.8), ("I", 8.9, 9.1), ("think.", 9.2, 9.5)])
    final, _ = _resolve_h(w, [], (_hint(2),))
    assert 2 in final


def test_rule2_does_not_force_cut_without_punctuation():
    w = _timed([("I", 0, .2), ("forgot", .3, .6), ("a", 2.7, 2.8),
                ("thing", 2.9, 3.2), ("today.", 3.3, 3.6)])
    final, _ = _resolve_h(w, [], (_hint(1),))
    assert 1 not in final


def test_rule2_forced_cut_survives_single_word_gate():
    # 「Well,」单独成组：AI 刀会被单词组闸撤掉，强制刀加在两道闸之后，不会被撤
    w = _timed([("Okay.", 0, .3), ("Well,", .4, .7), ("that", 7.5, 7.8),
                ("is", 7.9, 8.1), ("nice.", 8.2, 8.5)])
    final, _ = _resolve_h(w, [0], (_hint(1),))
    assert 0 in final and 1 in final


def test_rule2_zero_duration_forced_cut_is_dropped_and_logged(caplog):
    w = _timed([("seven,", 1.0, 1.0), ("two", 4.0, 4.3), ("more.", 4.4, 4.7)])
    with caplog.at_level("INFO"):
        final, _ = _resolve_h(w, [], (_hint(0),))
    assert 0 not in final
    assert "规则2" in caplog.text


# ── A-6 规则 3：保险丝往回找 句末 → 逗号 → 停顿 → 旧做法 ──

def test_rule3_fuse_goes_back_to_sentence_end():
    w = _words("I would like to do. I would like to learn how to bake a pie.".split())
    final, added = sg.resolve_llm_primary_breaks(
        " ".join(x["text"] for x in w), w, break_after_indices=[],
        language="en", max_chars=45)
    assert final and final[0] == _idx(w, "do.")
    assert _idx(w, "how") not in final


def test_rule3_fuse_uses_comma_when_no_sentence_end():
    w = _words("After the long day, we would like to learn how to bake a pie".split())
    final, _ = sg.resolve_llm_primary_breaks(
        " ".join(x["text"] for x in w), w, break_after_indices=[],
        language="en", max_chars=45)
    assert final[0] == _idx(w, "day,")


def test_rule3_fuse_falls_back_to_old_cut_without_punctuation():
    w = _words("we would like to learn how to bake a pie with my mom".split())
    final, _ = sg.resolve_llm_primary_breaks(
        " ".join(x["text"] for x in w), w, break_after_indices=[],
        language="en", max_chars=30)
    # 旧做法：超限前那个词
    group = " ".join(x["text"] for x in w[:final[0] + 1])
    assert len(group) <= 30
    assert len(group + " " + w[final[0] + 1]["text"]) > 30


def test_rule3_japanese_uses_sentence_end_then_comma():
    chars = list("今日は晴れです。明日も晴れるでしょう、でも少し寒いかもしれませんね")
    w = _words(chars, dur=0.1, gap=0.0)
    final, _ = sg.resolve_llm_primary_breaks(
        "".join(chars), w, break_after_indices=[], language="ja", max_chars=20)
    assert final[0] == chars.index("。")
    assert chars.index("、") in final


# ── A-7 备用线路：当成 AI 分组，走同一套规则 ──

def test_fallback_line_removes_half_sentence_cut():
    # 备用线路在 0.6 秒停顿处切 `right ‖ now`；规则 1 应撤掉
    w = _timed([("They're", 0, .3), ("very", .4, .7), ("viral", .8, 1.1),
                ("right", 1.2, 1.5), ("now", 2.1, 2.4), ("online.", 2.5, 2.8)])
    text = " ".join(x["text"] for x in w)
    groups = sg.group_qwen_raw_for_translation([], text, w, language="en")
    assert [g.text for g in groups] == [text]


def test_fallback_line_applies_rule2(monkeypatch):
    w = _timed([("Let's", 0, .3), ("carry", .4, .7), ("on,", .8, 1.1),
                ("that's", 12.9, 13.2), ("it", 13.3, 13.6), ("folks.", 13.7, 14.0)])
    text = " ".join(x["text"] for x in w)
    groups = sg.group_qwen_raw_for_translation(
        [], text, w, language="en", sound_hints=(_hint(2),))
    assert groups[0].text == "Let's carry on,"


# ── A-8 分组缓存版本 8 → 9 ──

def test_boundary_cache_version_is_9():
    from backend import worker
    assert worker._P390_BOUNDARY_CACHE_VERSION == 9
