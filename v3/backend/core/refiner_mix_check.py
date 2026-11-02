"""润色混入检查（G12 第三批 B，A+ §15.3）。

AI 润色偶尔把前后相邻组的话搬进本组（新西兰 5:50「most days, as you'll see」同一句出现两遍）。
查到就只拒这一组的润色、保留识别原话。两条判定，任一成立就拒：
  1. 整句：润色文里有一句（≥4 词）本组原话覆盖 <60%、前或后一组原话覆盖 ≥80%。
  2. 连续 4 词：润色文和本组原话逐词比出的**纯插入**段（≥4 词；替换出来的新词不算，
     防同义改写被误拒——确认轮外审），其中有 4 个连着的词本组原话没有、邻组原话有。
实验参照：pipelines/复查-第26与第2件/a7_fix.py（foreign_sentence / foreign_run）。
"""

import difflib
import re

_WORD = re.compile(r"[a-z0-9']+")
_NGRAM = 4
_OWN_COVER_MAX = 0.6
_NEIGHBOUR_COVER_MIN = 0.8


def _words(text):
    return _WORD.findall(str(text or "").lower())


def _sentences(text):
    return [s for s in re.split(r"[.?!…]+|—|--", str(text or "")) if len(_words(s)) >= _NGRAM]


def _cover(sentence, text):
    wanted = _words(sentence)
    if not wanted:
        return 0.0
    bag = {}
    for word in _words(text):
        bag[word] = bag.get(word, 0) + 1
    hit = 0
    for word in wanted:
        if bag.get(word, 0) > 0:
            bag[word] -= 1
            hit += 1
    return hit / len(wanted)


def _grams(words):
    return {tuple(words[i:i + _NGRAM]) for i in range(len(words) - _NGRAM + 1)}


def find_mixed_in(fixed, own, prev, nxt):
    """返回 ``(规则, 混入的文字)``；没有混入返回 ``None``。"""
    for sentence in _sentences(fixed):
        if (_cover(sentence, own) < _OWN_COVER_MAX
                and max(_cover(sentence, prev), _cover(sentence, nxt)) >= _NEIGHBOUR_COVER_MIN):
            return "整句", sentence.strip()
    fixed_words, own_words = _words(fixed), _words(own)
    own_grams = _grams(own_words)
    neighbour_grams = _grams(_words(prev)) | _grams(_words(nxt))
    matcher = difflib.SequenceMatcher(None, own_words, fixed_words, autojunk=False)
    for op, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if op == "insert" and j2 - j1 >= _NGRAM:
            span = fixed_words[j1:j2]
            if any(g in neighbour_grams and g not in own_grams for g in _grams(span)):
                return "连续4词", " ".join(span)
    return None
