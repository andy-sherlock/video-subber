"""P409：断句 prompt 把原词原样交给模型，并声明标点可能不准。

P390 的做法是「替模型把 ASR 的错标点擦掉」——抹掉尾部 .?! 、把句号后的
首字母改小写。动机是对的（ASR 标点确实不可靠），但它同时抹掉了**可靠的
那部分**：真句号和真句首大写。模型看不见句子在哪结束，只能靠猜。

2026-08-04 用 1807 词真实转录 + 真实 API 跑了四轮控制变量实验
（evidence/p395-real-api-2026-08-03/）。判定标准不是断点数量，而是每一刀
切得对不对——切在真句尾、或切在逗号/连词处算对，把词组拆开算错：

    抹标点 + 抹大小写（P390 现状）        切错 11 处
    抹标点 + 抹大小写 + 简化提示词          切错 11 处
    抹标点 + 保大小写 + 简化提示词          切错  8 处
    保标点 + 保大小写 + 声明标点不可靠      切错  3 处   ← 本次采用

信息给一半比给全了更糟：只给大小写时，模型知道「这是新句开头」却不知道
「上一句在哪结束」，于是反复把 `It's` 落在上一组末尾（913、1121）。

P390 当初针对的 `became. More`（ASR 在句中打了句号）在本方案下并未切错——
声明标点不可靠，比替模型擦掉标点更有效，且不会连真标点一起损失。
"""

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
    displayed = {}
    for line in user_prompt.splitlines():
        match = re.fullmatch(r"(\d+): (.*)", line)
        if match:
            displayed[int(match.group(1))] = match.group(2)
    return displayed


def test_english_prompt_keeps_sentence_ending_punctuation():
    """真句号必须原样出现在 prompt 里——它是模型判断句子边界的主要线索。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "Next", "one"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=2,
    )

    assert _displayed_prompt_words(user_prompt)[0] == "Done."


def test_english_prompt_keeps_capitalization_after_period():
    """句号后的首字母大写必须保留，不再临时改成小写。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "Next", "one"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=2,
    )

    assert _displayed_prompt_words(user_prompt)[1] == "Next"


def test_wrong_asr_period_is_shown_as_is_not_scrubbed():
    """`became. More`：ASR 打错的句号也原样给模型，由提示词声明其不可靠。

    P390 在这里替模型擦成 `became more`。实验证明擦除得不偿失——真标点
    一起被擦掉，代价远大于这个错句号本身。
    """
    from backend.core import sentence_groups

    words = _words(["English", "became.", "More", "widely", "spoken"])
    original = [dict(word) for word in words]
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="auto", start_index=0, end_index=4,
    )
    shown = _displayed_prompt_words(user_prompt)

    assert shown[1] == "became."
    assert shown[2] == "More"
    assert words == original, "prompt 构造不得修改调用方传入的原始词"


def test_system_prompt_declares_punctuation_may_be_wrong():
    """必须明确告诉模型标点来自 ASR、可能不准，否则它会盲信错标点。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "Next"])
    system_prompt, _, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=1,
    )

    assert "可能不准确" in system_prompt
    assert "语义" in system_prompt


def test_system_prompt_drops_the_rule_list():
    """规则表已移除：逐条列举连接词反而把模型框死在清单内。

    原提示词逐条列举 `but / and / so / because / if / when / that`，
    结果清单外的词（`during` / `into` / `that's`）被模型当成可以在前面断。
    砍掉清单后这四处全部不再断错。
    """
    from backend.core import sentence_groups

    words = _words(["Done.", "Next"])
    system_prompt, _, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=1,
    )

    for banned in ("because", "转折词", "连接词", "正例", "反例", "至少 3 个"):
        assert banned not in system_prompt, f"规则表残留：{banned!r}"


def test_system_prompt_does_not_mention_the_char_limit():
    """240 字符不再写进提示词，但仍是程序端硬限制。

    实测该约束从未单独生效——4 次机械补刀全部由 20 秒触发，唯一超 240 的
    那组同时也超了 20 秒。模型拿回标点后自己断得更碎，补刀 4 次 → 1 次。
    """
    from backend.core import sentence_groups

    words = _words(["Done.", "Next"])
    system_prompt, _, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=1,
    )

    assert "240" not in system_prompt, "字符上限不应再写进提示词"
    assert "字符" not in system_prompt, "不应再用字符数约束模型"
    assert sentence_groups._DEFAULT_MAX_CHARS == 240, (
        "程序端硬限制必须保留——撤掉的只是「告诉模型」这一步"
    )


def test_prompt_builder_no_longer_takes_max_chars():
    """`max_chars` 参数随字符约束一起移除，不留没人用的形参。"""
    import inspect

    from backend.core import sentence_groups

    params = inspect.signature(
        sentence_groups.build_llm_primary_boundary_prompt
    ).parameters
    assert "max_chars" not in params
    assert "max_words" in params, "词数上限仍要能被调用方覆盖"


def test_hard_limit_enforcer_still_takes_max_chars():
    """程序端补刀函数必须仍然认 240——本次只改提示词，不改闸门。"""
    import inspect

    from backend.core import sentence_groups

    params = inspect.signature(
        sentence_groups.enforce_llm_break_hard_limits
    ).parameters
    for name in ("max_chars", "max_words", "max_duration"):
        assert name in params, f"三条硬限制不得被削弱：{name} 缺失"


def test_system_prompt_keeps_output_contract_and_word_budget():
    """输出格式契约和每组词数上限必须保留——它们不是「规则表」的一部分。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "Next"])
    system_prompt, _, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=1, max_words=80,
    )

    assert "break_after_indices" in system_prompt
    assert "80" in system_prompt
    assert "不得改写" in system_prompt


@pytest.mark.parametrize("language", ["zh", "ja", "ko"])
def test_cjk_languages_still_receive_untouched_words(language):
    """CJK 分支本来就不洗词，本次改动不得影响它。"""
    from backend.core import sentence_groups

    words = _words(["完成。", "下一个"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language=language, start_index=0, end_index=1,
    )

    assert _displayed_prompt_words(user_prompt) == {0: "完成。", 1: "下一个"}


def test_full_width_punctuation_in_english_mode_is_preserved():
    """英文模式下遇到全角标点也原样保留（P390 已保证，不得回退）。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "句号。", "问号？", "叹号！"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=3,
    )
    shown = _displayed_prompt_words(user_prompt)

    assert [shown[index] for index in (1, 2, 3)] == ["句号。", "问号？", "叹号！"]


def test_acronyms_and_single_letter_i_keep_their_case():
    """UK 和 I 都保持原样——不再有「缩写保护」这条特例分支。"""
    from backend.core import sentence_groups

    words = _words(["Done.", "UK", "and", "I", "agree"])
    _, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words, language="en", start_index=0, end_index=4,
    )
    shown = _displayed_prompt_words(user_prompt)

    assert shown[1] == "UK"
    assert shown[3] == "I"
