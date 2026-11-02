"""G12 第三批 B · A-12 / A-14：润色混入检查（A+ §15.3、§15.5）。

一组的润色文里不许出现从前后相邻组搬来的一段话；查到就只拒这一组的润色、保留识别原话，
同批其他组照常采用。检查对象是每轮润色开头拍下的整篇原话快照（不受窗口边界影响）。
所有用例都走生产路径 `_run_refiner` → `_apply_refiner_corrections`；AI 用固定替身。
"""

import json

from backend.core import sentence_groups as sg


# 外审第二轮反例用的两组真实原话（30 分钟片 1451 / 1461 秒）
PREV = ("Another one, definitely with gymnastics. Getting better gymnastics, "
        "I overcame a lot there.")
OWN = ("Um, not like badly, but I'm the kind of person that I have to want to do it, "
       "and if I don't want to do it, I'm not going to try to do it.")
COPIED = PREV.split(".")[0] + "."


def _groups(texts):
    words, breaks, clock = [], [], 0.0
    for text in texts:
        for token in text.split():
            words.append({"word": token, "text": token,
                          "start": round(clock, 3), "end": round(clock + 0.3, 3)})
            clock += 0.4
        breaks.append(len(words) - 1)
    full = " ".join(w["text"] for w in words)
    return sg.group_qwen_raw_from_break_indices(
        [], full, words, break_after_indices=breaks[:-1], language="en")


class _FixedRefiner:
    """每次都对指定组返回同一条改动。"""

    def __init__(self, fixes):
        self.fixes = fixes  # {组下标: fixed_text}

    def bind(self, groups):
        self.by_id = {groups[i].id: text for i, text in self.fixes.items()}
        return self

    def translate_raw_json(self, user_prompt, system_prompt=None):
        # 全部改动都返回；不在本窗口的编号由生产代码自己忽略（只读上下文不可改）
        return json.dumps({"corrections": [
            {"seg_id": sid, "fixed_text": text} for sid, text in self.by_id.items()]})


def _run(groups, fixes, *, window_size=1):
    from backend import worker

    for g in groups:
        g.uncertain = True
    llm = _FixedRefiner(fixes).bind(groups)
    worker._run_refiner(groups, llm, receipt=None, window_size=window_size, context_pad=0)
    return groups


def test_comma_glued_copy_of_neighbour_is_rejected_even_across_windows(caplog):
    # 前一组不在本窗口（window_size=1），仍要靠整篇快照看见它
    groups = _groups([PREV, OWN])
    with caplog.at_level("WARNING"):
        _run(groups, {1: OWN.rstrip(".") + ", " + COPIED})
    assert groups[1].text == OWN
    assert groups[1].uncertain is True
    assert "连续4词" in caplog.text


def test_whole_neighbour_sentence_appended_is_rejected(caplog):
    groups = _groups([PREV, OWN])
    with caplog.at_level("WARNING"):
        _run(groups, {1: OWN + " " + COPIED})
    assert groups[1].text == OWN
    assert "整句" in caplog.text


def test_synonym_rewrite_is_not_rejected():
    # 确认轮外审受控反例：was delighted → could not be happier，邻组恰好有这几个词
    a = "I could not be happier with my new job."
    b = ("She was delighted with the book we bought yesterday, "
         "and she read it all before dinner.")
    groups = _groups([a, b])
    fixed = ("She could not be happier with the book we bought yesterday, "
             "and she read it all before dinner.")
    _run(groups, {1: fixed})
    assert groups[1].text == fixed


def test_normal_small_edit_is_accepted():
    groups = _groups([PREV, OWN])
    fixed = OWN.replace("Um, not like badly", "Um, not badly")
    _run(groups, {1: fixed})
    assert groups[1].text == fixed


def test_only_the_mixed_group_is_rejected_in_same_window():
    groups = _groups([PREV, OWN])
    fixed_prev = PREV.replace("Another one,", "Another one:")
    _run(groups, {0: fixed_prev, 1: OWN + " " + COPIED}, window_size=10)
    assert groups[0].text == fixed_prev
    assert groups[1].text == OWN


# ── A-14（A+ §15.5）固定测试：坏润色 → 被拒 → 原话 → 翻译替身 → 最终屏 ──

NZ_PREV = ("Okay, this is one thing about trekking in the middle of New Zealand: "
           "you are at the mercy of its ever-changing weather.")
NZ_OWN = "By and large, we got extremely lucky."
NZ_BAD = "By and large, we got extremely lucky most days, as you'll see."
NZ_NEXT = "Most days, as you'll see, but some days like this one, you just have to submit."
NZ_ZH = {
    NZ_PREV: "好吧，在新西兰中部徒步有一点，你得看它多变的天气的脸色。",
    NZ_OWN: "总的来说，我们运气非常好。",
    NZ_BAD: "总的来说，我们运气非常好，大多数日子都是，你会看到的。",
    NZ_NEXT: "大多数日子，你会看到的，但有些日子比如今天，你只能认命。",
}


def test_rejected_refinement_never_reaches_final_screens(tmp_path):
    from backend import worker
    from backend.core.display_timeline import build_display_timeline

    groups = _groups([NZ_PREV, NZ_OWN, NZ_NEXT])
    fixed_prev = NZ_PREV.replace("Okay,", "Okay:")
    NZ_ZH[fixed_prev] = NZ_ZH[NZ_PREV]
    llm = _FixedRefiner({0: fixed_prev, 1: NZ_BAD}).bind(groups)

    worker._run_transcript_refiner(
        [{"platform": "stub"}], groups, receipt=None,
        refined_path=tmp_path / "refined_transcript.json", llm_backend=llm)

    assert groups[0].text == fixed_prev          # 同批其他组照常采用
    assert groups[1].text == NZ_OWN              # 被拒的组送去翻译的是原话
    for g in groups:
        g.zh = NZ_ZH[g.text]                     # 翻译固定替身，不调 AI
    screens = "".join(e.text for e in build_display_timeline(groups))
    assert screens.count("大多数日子") == 1
