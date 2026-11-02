"""原词 ↔ 润色文逐词对齐（G12 第三批 B）。**全项目只此一份。**

用途两处，都只「读」润色文、不改原词：
  · 润色后并组（A+ §13）：组尾词原词带句号、润色文里对应的词没有 → 这一刀切在假句号上。
  · 排版改动 3 / §15.1：排版要知道润色删掉了哪些句号、在哪里是句末。

规则（来自方案与实验 `faithful.only_remove`、`r1_align.prepare`）：去标点、转小写后
用 difflib 逐词对齐；整组相似度 < 0.6 视为对不齐，一个词也不认；只认一一对应的词
（相等块，或长度相等的替换块）。
"""

import difflib
import re

MIN_ALIGN_RATIO = 0.6


def _norm(token: str) -> str:
    return re.sub(r"[^\w']", "", str(token)).lower()


def align_opcodes(raw_tokens, refined_tokens):
    """逐词对齐的操作列表（difflib opcodes）；整组相似度 < 0.6 返回 ``None``（对不齐，一个词也不认）。"""
    if not raw_tokens or not refined_tokens:
        return None
    matcher = difflib.SequenceMatcher(
        a=[_norm(t) for t in raw_tokens], b=[_norm(t) for t in refined_tokens], autojunk=False)
    if matcher.ratio() < MIN_ALIGN_RATIO:
        return None
    return matcher.get_opcodes()


def one_to_one_refined_tokens(raw_tokens, refined_text) -> list:
    """返回与 ``raw_tokens`` 等长的列表：每个原词一一对应的润色词，对不上的是 ``None``。"""
    refined = str(refined_text or "").split()
    result = [None] * len(raw_tokens)
    opcodes = align_opcodes(raw_tokens, refined)
    if opcodes is None:
        return result
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for i, j in zip(range(i1, i2), range(j1, j2)):
                result[i] = refined[j]
    return result
