"""`_ratio_index` 英文名按词数算（用户 2026-10-12 定）：译文里每个连续的英文词/数字算 1 个字符，
不再按字母个数算。病根：句中的英文名（Redbrid Berry's、Spanish Boost Gaming）字母很多、英文原文只占 2~3 个词，
按字符比例换算会把切点推到更靠后的词，字幕因此晚出（T121/T191 晚 2.2 秒，T046 晚 2.9 秒）。
没有英文词的译文，结果必须和旧公式逐位相同。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt


def _old_formula(zh, pos, n, ignore_whitespace=False):
    if ignore_whitespace:
        total, prefix = len("".join(zh.split())), len("".join(zh[:pos].split()))
    else:
        total, prefix = len(zh), pos
    return 1 if total <= 0 else max(1, min(n - 1, round(prefix / total * n)))


@pytest.mark.parametrize("ws", [False, True])
def test_text_without_english_words_gets_exactly_the_old_answers(ws):
    for zh, n in (("我们先去超市买东西，然后回家做饭，再看一会儿电视吧。", 12),
                  ("好的啊 我们去 超市买东西了", 9), ("一二三四五六七八九十", 3)):
        for pos in range(1, len(zh)):
            assert dt._ratio_index(zh, pos, n, ignore_whitespace=ws) == _old_formula(zh, pos, n, ws)


def test_an_english_name_counts_as_one_word_not_one_letter_each():
    zh = "好了，我们在Redbrid Berry's，瓦纳卡又一个好天气，我们要去摘草莓了。"
    # 英文原句 19 个词：All right, we are at Redbrid Berry's | on another beautiful day in Wanaka, | and ...
    after_name = zh.index("Berry's，") + len("Berry's，")
    after_day = zh.index("好天气，") + len("好天气，")
    assert _old_formula(zh, after_name, 19) == 10 and _old_formula(zh, after_day, 19) == 15      # 旧：偏后 3、2 个词
    assert dt._ratio_index(zh, after_name, 19) == 7          # 前面正好是 7 个英文词
    assert dt._ratio_index(zh, after_day, 19) == 13          # 前面正好是 13 个英文词


def test_t046_cut_after_the_name_moves_much_earlier():
    zh = "所以要特别感谢Spanish Boost Gaming，因为正是看这些视频的那种感觉，启发我做了这个频道。"
    pos = zh.index("Gaming，") + len("Gaming，")
    assert _old_formula(zh, pos, 30) == 16                   # 旧：第 16 个词（说话人 'because' 在第 6 个词）
    assert dt._ratio_index(zh, pos, 30) == 10                # 新：第 10 个词（仍偏后，但少晚 6 个词）
