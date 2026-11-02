"""汉字口径（用户 2026-10-12 定）：字数 = 去掉标点后的中文汉字个数；读速 = 汉字数 ÷ 这一屏显示的秒数。

只管切点梯子：要不要进梯子、每块上下限、一行 16、读速上限。英文字母、数字、空格、标点一律不算。
排版阶段（两行屏怎么拆）仍用旧的"字宽"，这里不碰。
"""
from backend.core import display_timeline as dt


def _words(n, start, end):
    step = (end - start) / n
    return [{"word": f"w{i}", "start": start + i * step, "end": start + i * step + step * 0.9}
            for i in range(n)]


def test_han_count_counts_only_chinese_characters():
    assert dt._han_count("我昨天在Costco买了三箱牛奶") == 10
    assert dt._han_count("你好，世界！ 2024 abc\n再见？") == 6        # 标点、空格、数字、字母、换行都不算


def test_han_speed_is_han_chars_per_second():
    assert dt._han_speed(1.0, 3.0, "我昨天在Costco买了三箱牛奶") == 5.0
    assert dt._han_speed(1.0, 1.0, "你") == 0.0                      # 零时长不除零


def test_ladder_is_needed_by_han_count_and_han_speed():
    t109 = "如果你们记得我那期Trader Joe's的视频"                      # 12 个汉字，旧字宽 18
    assert dt._units(t109) > 16 and dt._han_count(t109) == 12
    assert dt._ladder_needed(t109, 0.0, 3.0) is False                # 汉字 ≤16 且读速 4/秒：一行放得下
    assert dt._ladder_needed("一二三四五六七八九十甲乙丙丁戊己", 0.0, 9.0) is False   # 恰好 16 个：不算超
    assert dt._ladder_needed("一二三四五六七八九十甲乙丙丁戊己庚", 0.0, 9.0) is True   # 17 个：超
    assert dt._ladder_needed("一二三四五六七八九", 0.0, 1.0) is False                 # 恰好每秒 9：不算快
    assert dt._ladder_needed("一二三四五六七八九十", 1.0, 2.0 - 1e-6) is True          # 每秒 10：快


NAMES = "我在Trader Joe's Whole Foods买了很多东西，然后我们回家做饭了"


def test_english_names_do_not_make_a_block_too_wide_or_too_fast():
    flat, cuts = dt._segment_cuts(NAMES)
    p = cuts[0]
    assert dt._units(flat[:p]) > 16 and dt._han_count(flat[:p]) == 8     # 旧口径嫌宽，汉字口径放得下
    words = _words(14, 0.0, 4.0)
    kw = dict(min_units=5, cps=dt.READING_CPS, best=True)
    assert dt._split_time(0.0, 4.0, flat, words, p, **kw) is None         # 旧口径：切不开
    assert dt._split_time(0.0, 4.0, flat, words, p, han=True, **kw) is not None   # 汉字口径：切得开
    r = dt._cut_ladder(0.0, 4.0, NAMES, words, False, False)
    assert r is not None and len(r) == 2 and r[0][2].startswith("我在Trader")


def test_han_floor_and_cap_apply_to_han_counts():
    # 每块至少 5 个汉字：左块只有 3 个汉字（英文再长也不算）→ 不许在这里切
    text = "好的啊 Trader Joe's Whole Foods，我们去超市买很多东西然后回家做饭了"
    flat, cuts = dt._segment_cuts(text)
    assert dt._han_count(flat[:cuts[0]]) == 3
    kw = dict(min_units=5, cps=dt.READING_CPS, best=True, han=True)
    assert dt._split_time(0.0, 5.0, flat, _words(14, 0.0, 5.0), cuts[0], **kw) is None
    # 每块至多 16 个汉字：右块 17 个汉字 → 不许切
    text2 = "我们先去超市，然后回家做饭再看一会儿电视节目然后睡觉"
    flat2, cuts2 = dt._segment_cuts(text2)
    assert dt._han_count(flat2[cuts2[0]:]) > 16
    assert dt._split_time(0.0, 9.0, flat2, _words(14, 0.0, 9.0), cuts2[0], **kw) is None
