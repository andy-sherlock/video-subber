"""G12 第三批 B · A-11：润色之后、翻译之前，把「切在润色删掉的句号上」的两组并回去。

合同：A+ §13 + §15.2（连续并组按已合成的整组查三条上限）。判断「润色删了句号」用
逐词比对、只认一一对应的词（同排版改动 3 的办法）。
"""

from backend.core import sentence_groups as sg


def _groups(texts_per_group, *, dur=0.3, gap=0.1):
    words, breaks, clock = [], [], 0.0
    for group in texts_per_group:
        for text in group:
            words.append({"word": text, "text": text,
                          "start": round(clock, 3), "end": round(clock + dur, 3)})
            clock += dur + gap
        breaks.append(len(words) - 1)
    full = " ".join(w["text"] for w in words)
    return sg.group_qwen_raw_from_break_indices(
        [], full, words, break_after_indices=breaks[:-1], language="en")


def _merge(groups):
    return sg.merge_groups_split_on_refiner_removed_periods(groups, language="en")


def test_merges_group_split_on_period_removed_by_refiner():
    groups = _groups([["There's", "definitely."], ["A", "few."]])
    groups[0].text = "There's definitely"
    groups[1].text = "a few."
    merged = _merge(groups)
    assert len(merged) == 1
    assert merged[0].text == "There's definitely a few."
    assert [w["text"] for w in merged[0].words] == ["There's", "definitely.", "A", "few."]
    assert merged[0].start == groups[0].start and merged[0].end == groups[1].end
    assert merged[0].end_word_index == groups[1].end_word_index


def test_no_merge_when_refiner_keeps_period():
    groups = _groups([["It", "was", "great."], ["We", "left."]])
    assert len(_merge(groups)) == 2


def test_no_merge_when_raw_has_no_period():
    groups = _groups([["It", "was", "great,"], ["we", "left."]])
    groups[0].text = "It was great"
    assert len(_merge(groups)) == 2


def test_no_merge_when_last_word_not_one_to_one_aligned():
    # 润色把句尾两个词改写成一个词（`super great.` → `superb`），组尾词没有一一对应的润色词 → 不认
    groups = _groups([["It", "was", "so", "super", "great."], ["We", "left."]])
    groups[0].text = "It was so superb"
    assert len(_merge(groups)) == 2


def test_chain_merge_checks_accumulated_group_against_limits():
    # 三组各 10 词；A+B 合法（179 字符），A+B+C 超 240 字符 → 只并 A+B（外审第二轮第 2 条）
    block = ["ordinary"] * 9
    groups = _groups([block + ["ordinary."]] * 3, dur=0.1, gap=0.0)
    for g in groups[:2]:
        g.text = g.text.rstrip(".")
    merged = _merge(groups)
    assert len(merged) == 2
    assert len(merged[0].words) == 20


def test_merge_is_deterministic():
    def build():
        groups = _groups([["There's", "definitely."], ["A", "few."], ["Okay."]])
        groups[0].text = "There's definitely"
        groups[1].text = "a few."
        return [(g.text, g.start, g.end) for g in _merge(groups)]

    assert build() == build()


def test_worker_merge_step_updates_segments_in_place():
    from backend import worker

    groups = _groups([["There's", "definitely."], ["A", "few."]])
    groups[0].text = "There's definitely"
    groups[1].text = "a few."
    segments = list(groups)
    worker._merge_groups_after_refiner(segments, language="en")
    assert [s.text for s in segments] == ["There's definitely a few."]


def test_no_merge_when_refined_text_still_ends_the_sentence():
    # 真实例（新西兰 1436 秒）：原词 `perfect.`，润色成 `What a perfect day.`——
    # perfect 的句号没了，但润色文结尾仍是句号，话已说完，不许并到下一组
    groups = _groups([["What", "a", "perfect."], ["So", "hey", "guys."]])
    groups[0].text = "What a perfect day."
    assert len(_merge(groups)) == 2
