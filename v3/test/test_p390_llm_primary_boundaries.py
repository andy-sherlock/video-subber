"""P390：LLM 只给词索引，程序用原始词重建最终句子组。"""

import re
import sys
from types import SimpleNamespace

import pytest


def _words(texts, *, step=0.35):
    return [
        {
            "word": text,
            "text": text,
            "start": round(index * step, 3),
            "end": round(index * step + 0.3, 3),
        }
        for index, text in enumerate(texts)
    ]


def _displayed_prompt_words(user_prompt):
    """从真实断句 prompt 里取回 ``索引: 临时展示词``。"""
    displayed = {}
    for line in user_prompt.splitlines():
        match = re.fullmatch(r"(\d+): (.*)", line)
        if match:
            displayed[int(match.group(1))] = match.group(2)
    return displayed


def _install_lightweight_segment(monkeypatch):
    class _Segment:
        def __init__(self, id, start, end, text):
            self.id = id
            self.start = start
            self.end = end
            self.text = text

    monkeypatch.setitem(
        sys.modules,
        "backend.transcriber_v2",
        SimpleNamespace(Segment=_Segment),
    )


def test_llm_indices_rebuild_continuous_raw_words_without_p389(monkeypatch):
    """LLM 选词索引后，程序必须只用原词和原时间建立最终组。"""
    from backend.core import sentence_groups

    rebuild = getattr(
        sentence_groups,
        "rebuild_sentence_groups_from_raw_break_indices",
        None,
    )
    assert rebuild is not None, "P390 必须提供从原始词索引重建句子组的入口"

    _install_lightweight_segment(monkeypatch)
    words = _words([
        "We", "stayed", "in", "the", "old",
        "town", "for", "hours", "then", "left",
    ])

    groups = rebuild(
        "We stayed in the old town for hours then left",
        words,
        break_after_indices=[4],
        language="en",
    )

    assert [group.text for group in groups] == [
        "We stayed in the old",
        "town for hours then left",
    ]
    assert [(group.start_word_index, group.end_word_index) for group in groups] == [
        (0, 4),
        (5, 9),
    ]
    assert [word["text"] for group in groups for word in group.words] == [
        word["text"] for word in words
    ]
    assert [(group.start, group.end) for group in groups] == [
        (words[0]["start"], words[4]["end"]),
        (words[5]["start"], words[9]["end"]),
    ]


def test_dense_indices_are_rejected_before_a_zero_duration_word_becomes_its_own_group(
    monkeypatch,
):
    """P390：格式正确的密集索引也不能把零时长原词单独切出来。"""
    from backend.core.sentence_groups import (
        SentenceGroupingError,
        rebuild_sentence_groups_from_raw_break_indices,
    )

    _install_lightweight_segment(monkeypatch)
    words = _words(["You", "know,", "even", "I", "left"])
    words[1]["start"] = words[0]["end"]
    words[1]["end"] = words[0]["end"]

    with pytest.raises(SentenceGroupingError, match="零时长词.*单独"):
        rebuild_sentence_groups_from_raw_break_indices(
            "You know, even I left",
            words,
            break_after_indices=[0, 1],
            language="en",
        )


def test_llm_response_only_accepts_the_exact_index_object():
    """P390：模型只能交回范围内的词索引，不能夹带任何内容。

    P395：范围校验从「查一张允许索引的名单」改成「比两个边界」——
    整篇模式下名单就是全部，会膨胀到 1806 个元素却零信息量。
    """
    from backend.core import sentence_groups

    parse = getattr(sentence_groups, "parse_llm_primary_break_response", None)
    assert parse is not None, "P390 必须严格读取 LLM 的索引 JSON 回答"

    assert parse(
        '{"break_after_indices": [12, 18]}',
        allowed_range=range(12, 22),
    ) == [12, 18]

    invalid_responses = [
        '{"break_after_indices": [12], "explanation": "这里是完整一句"}',
        '{"break_after_indices": [12], "text": "rewritten English"}',
        '{"break_after_indices": [12], "translation": "中文"}',
        '{"break_after_indices": [12], "time": [1.2, 2.4]}',
        '[12, 18]',
    ]
    for raw_response in invalid_responses:
        with pytest.raises(sentence_groups.SentenceGroupingError):
            parse(raw_response, allowed_range=range(12, 22))

    assert parse(
        '{"break_after_indices": [12, 12, 18, 17, 21, 99]}',
        allowed_range=range(12, 22),
    ) == [12, 18, 21]


def test_hard_limits_only_add_minimum_breaks_after_llm_choice(monkeypatch):
    """P390：先保留 LLM 断点，只有词数超限才补最少安全断点。"""
    from backend.core import sentence_groups

    enforce = getattr(sentence_groups, "enforce_llm_break_hard_limits", None)
    assert enforce is not None, "P390 必须在 LLM 断点之后执行硬限制"

    _install_lightweight_segment(monkeypatch)
    words = _words([
        "one", "two", "three", "four",
        "five", "six", "seven", "eight",
    ])
    full_text = "one two three four five six seven eight"

    breaks, added_by_limits = enforce(
        full_text,
        words,
        break_after_indices=[1],
        language="en",
        max_duration=99,
        max_words=3,
        max_chars=999,
    )

    assert breaks == [1, 4]
    # P395/6b：4 是硬限制自己补的，1 是 LLM 选的——两者必须分得开
    assert added_by_limits == {4}
    groups = sentence_groups.rebuild_sentence_groups_from_raw_break_indices(
        full_text,
        words,
        break_after_indices=breaks,
        language="en",
    )
    assert [(group.start_word_index, group.end_word_index) for group in groups] == [
        (0, 1),
        (2, 4),
        (5, 7),
    ]


def test_hard_limits_cover_duration_and_character_count():
    """P390：时长或字符超限时也只能补必要断点。"""
    from backend.core import sentence_groups

    enforce = getattr(sentence_groups, "enforce_llm_break_hard_limits", None)
    assert enforce is not None, "P390 必须在 LLM 断点之后执行硬限制"

    duration_words = _words(["one", "two", "three", "four"], step=1.0)
    assert enforce(
        "one two three four",
        duration_words,
        break_after_indices=[],
        language="en",
        max_duration=1.6,
        max_words=99,
        max_chars=999,
    ) == ([1], {1})

    character_words = _words(["alpha", "bravo", "charlie"])
    assert enforce(
        "alpha bravo charlie",
        character_words,
        break_after_indices=[],
        language="en",
        max_duration=99,
        max_words=99,
        max_chars=11,
    ) == ([1], {1})


def test_hard_limits_apply_after_llm_breaks_without_erasing_them(monkeypatch):
    """P390：硬限制在 LLM 断点之后补，不能把 LLM 原断点清掉。

    P395：从「逐窗汇总后补」改成「整篇一份答案上补」——窗口没了。
    """
    from backend.core.sentence_groups import resolve_llm_primary_breaks

    _install_lightweight_segment(monkeypatch)
    words = _words([
        "one", "two", "three", "four",
        "five", "six", "seven", "eight",
    ])

    breaks, added_by_limits = resolve_llm_primary_breaks(
        "one two three four five six seven eight",
        words,
        break_after_indices=[1],
        language="en",
        max_duration=99,
        max_words=3,
        max_chars=999,
    )

    assert breaks == [1, 4], "LLM 选的 1 必须保留，4 是硬限制补的"
    assert added_by_limits == {4}


def test_llm_prompt_contains_only_indexed_raw_words():
    """P390：LLM 只看连续索引词，不看旧组、时间或译文。

    P395：原来这条还断言提示词里列出了「本窗可选索引」（`4, 5, 6, 7`）。
    那一行已经删除——整篇模式下可选位置就是全部，逐个列出来是 9724 个字符的
    纯浪费，占词内容的 45%。**断言方向就此反转。**
    """
    from backend.core import sentence_groups

    build_prompt = getattr(sentence_groups, "build_llm_primary_boundary_prompt", None)
    assert build_prompt is not None, "P390 必须统一构造只含原始词的断句请求"

    words = _words([
        "zero", "one", "two", "before",
        "This", "is", "the", "middle",
        "after", "last",
    ])

    system_prompt, user_prompt, _allowed_range = build_prompt(
        words, language="en", start_index=3, end_index=8,
    )

    assert "break_after_indices" in system_prompt
    assert "4, 5, 6, 7" not in user_prompt, "P395：不得再逐个列举可选断点索引"
    assert "3: before" in user_prompt
    assert "4: This" in user_prompt
    assert "8: after" in user_prompt
    assert "2: two" not in user_prompt
    assert "9: last" not in user_prompt
    assert "0.0" not in user_prompt
    assert "P389" not in system_prompt
    assert "P389" not in user_prompt
    assert "文本里的标点是语音识别器自动加的" not in system_prompt


# ── P409（2026-08-04）：英文擦除分支已整体删除，本段六个测试随之移除 ──────────
#
# 原有六个测试锁定的是 `_llm_display_word` 的擦除行为：删词尾 .?! 、把句号后
# 首字母改小写、保护全大写缩写、单字母 I 不受保护、窗口首词读全局前词。
# 该函数在 P409 已删除——原词现在原样发给模型，改由提示词声明「标点可能不准」。
#
# 为什么反转 P390 的设计：P390 擦除的动机（ASR 标点不可靠）成立，但它连**真**
# 句号和**真**句首大写一起擦掉，模型看不见句子在哪结束。1807 词真实转录 + 真实
# API 四轮控制变量实测——切错 11 处 → 3 处。连 P390 当初针对的 `became. More`
# （索引 1640，ASR 在句中打了句号）在新方案下也没有断错。
#
# 覆盖去向：全部搬到 `test/test_p409_raw_word_display.py`，且不是简单取反——
# 原样保留标点、原样保留大小写、UK 与 I 都不动、全角标点不动、CJK 分支不动、
# 「不得修改调用方传入的原词」这条不变量也一并搬过去了。
#
# 下方 CJK 分支的测试保留在本文件：那条路径 P390 本来就不擦，P409 也没碰。


@pytest.mark.parametrize("language", ["zh", "ja", "ko"])
def test_explicit_cjk_languages_do_not_clean_prompt_words(language):
    """明确指定中、日、韩时，ASCII 标点和后词大写也完全不洗。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "Next"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words,
        language=language,
        start_index=0,
        end_index=1,
    )

    assert _displayed_prompt_words(user_prompt) == {0: "Done.", 1: "Next"}


def test_auto_language_with_any_cjk_content_does_not_clean_the_segment():
    """auto 只要全文含任意 CJK 字符，整段都保持原样。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "Next", "中文"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words,
        language="auto",
        start_index=0,
        end_index=2,
    )

    assert _displayed_prompt_words(user_prompt) == {
        0: "Done.",
        1: "Next",
        2: "中文",
    }
