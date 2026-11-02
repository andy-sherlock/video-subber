"""源头检查：把识别标错时间的英文词挪回原位（G12 第三批 B）。

依据：pipelines/方案-源头检查-2026-09-27.md §3、§11、§12；实验参照
pipelines/真实测试-全方案-2026-09-27/全流程.py `source_check`。

识别模型给英文词标时间，遇到停顿或背景音乐时常把停顿后第一个词的开头往前拉
（新西兰片 `All` 被标成 637.72–650.44，用户听到约 650 秒才说）。这里只改词的
开始 / 结束时间，不改文字、不增删、不换顺序；返回**新的一份**，输入原样不动——
识别收据里的原始时间只作复查（§12：修好的时间只生成一次，下游共用这一份）。

对整部片从后往前逐词处理（改完的词成为它前面那个词的「下一个词」）：
  · 规则 A（词长 > 2 秒）：词尾带句末标点 → 保开头、结尾收到「开头 + L」；
    否则 → 保结尾（后面还隔着 ≥2 秒空白时结尾改贴下一个词的开头），开头收到「结尾 − L」。
  · 规则 B（孤词）：词长 ≤ 2 秒、后面隔着 ≥2 秒空白、词尾不带句末标点、同一句里连着说
    （词间空隙 < 1 秒）的词不超过 2 个 → 整个词贴到下一个词前面。
  · 人声否决（只管 B）：原位置的人声比例比新位置高 0.2 以上 → 不挪。
  · §11：挪完新开头早于前一个词的开头、或晚于下一个词的开头 → 不挪（会让词序倒退），记下来。
  · L = 字母数 × 0.06 秒，夹在 0.2–0.6 秒；新开头不早于前一个词的结尾。
"""

import copy
import re

_SENTENCE_END = re.compile(r"[.?!…][\"'”’)\]]*$")
_LONG_WORD_S = 2.0
_GAP_S = 2.0
_RUN_GAP_S = 1.0
_MAX_LONELY_RUN = 2
_VETO_MARGIN = 0.2
_MIN_VETO_SPAN_S = 0.2
_MIN_CHANGE_S = 0.05


def _normal_length(text: str) -> float:
    letters = len(re.sub(r"[^A-Za-z]", "", str(text)))
    return min(0.6, max(0.2, 0.06 * letters))


def _voiced_ratio(voice, start: float, end: float) -> float:
    end = max(end, start + _MIN_VETO_SPAN_S)
    voiced = sum(max(0.0, min(end, b) - max(start, a))
                 for a, b in voice if b > start and a < end)
    return voiced / (end - start)


def check_word_times(words, voice_intervals):
    """返回 ``(修好的词, 改动记录, 因词序倒退跳过的词)``。

    ``voice_intervals``：人声区间（秒）列表 ``[(开始, 结束), ...]``，来自生产人声地图。
    """
    fixed = copy.deepcopy(list(words))
    voice = [(float(a), float(b)) for a, b in voice_intervals]
    changes, skipped = [], []

    def lonely_run(i):
        count, k = 1, i
        while (k > 0 and not _SENTENCE_END.search(str(fixed[k - 1]["text"]))
               and float(fixed[k]["start"]) - float(fixed[k - 1]["end"]) < _RUN_GAP_S):
            count += 1
            k -= 1
        return count

    for i in range(len(fixed) - 1, -1, -1):
        word = fixed[i]
        start, end = float(word["start"]), float(word["end"])
        nxt = fixed[i + 1] if i + 1 < len(fixed) else None
        prev_end = float(fixed[i - 1]["end"]) if i > 0 else 0.0
        prev_start = float(fixed[i - 1]["start"]) if i > 0 else 0.0
        gap = float(nxt["start"]) - end if nxt else 0.0
        ends_sentence = bool(_SENTENCE_END.search(str(word["text"])))
        length = _normal_length(word["text"])

        rule = None
        if end - start > _LONG_WORD_S:
            if ends_sentence:
                new_start, new_end, rule = start, start + length, "A-句末保开头"
            else:
                new_end = float(nxt["start"]) if (nxt and gap >= _GAP_S) else end
                new_start, rule = max(prev_end, new_end - length), "A-保结尾"
        elif nxt and gap >= _GAP_S and not ends_sentence and lonely_run(i) <= _MAX_LONELY_RUN:
            new_end = float(nxt["start"])
            new_start, rule = max(prev_end, new_end - length), "B-孤词贴后"

        if rule and rule.startswith("B") and (
                _voiced_ratio(voice, start, end)
                > _voiced_ratio(voice, new_start, new_end) + _VETO_MARGIN):
            rule = None
        if rule and ((nxt and new_start > float(nxt["start"])) or new_start < prev_start):
            skipped.append({"index": i, "word": word["text"], "rule": rule})
            rule = None
        if rule and (abs(new_start - start) > _MIN_CHANGE_S or abs(new_end - end) > _MIN_CHANGE_S):
            word["start"], word["end"] = round(new_start, 3), round(new_end, 3)
            changes.append({"index": i, "word": word["text"], "rule": rule,
                            "old": [start, end], "new": [word["start"], word["end"]]})
    return fixed, changes, skipped
