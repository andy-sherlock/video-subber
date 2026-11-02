"""G12 第三批 B · L-16：第 22 件「翻译后 AI 标断点」定版（排版方案 §10、§13.1；决定 19/21/36）。

- 送 AI 的门槛 20 → 16 字；
- 提示词换新规则、示例一行一段无箭头（§10 真实病根：示例教出「→」，新西兰批 0/14 被拒）；
- 分批约 80 条（§10 付费实测：整片一次发 200/216 条，回答写到第 111/125 条就被 4096 上限截断）；
- 决定 36：某一批出错只这一批不拆；某一批被截断只截断点之后不拆；
- 一行普通日志：发出 / 采用 / 各原因拒绝 / 出错批数 / 截断批数。
AI 全用替身，不联网。
"""

import logging
import re

from backend.core import display_timeline as dt
from backend.core import sentence_splitter as ss
from backend.transcriber_v2 import Segment
from backend.translation.base import TranslationResult

ZH = "我们很乐意说明一下这件事情，您预计的出发时间是十点二十五分"   # 新西兰片真实长句的形状


def _seg(i, zh):
    s = Segment(id=i, start=float(i), end=float(i) + 3.0, text="we are happy to tell you")
    s.zh = zh
    s.speaker_change = False
    s.words = [{"word": "we", "start": float(i), "end": float(i) + 3.0}]
    return s


def _answer(prompt, only=None):
    """把提示词里每条长句在逗号处拆成两段，按新格式一行一段回答。"""
    out = []
    for m in re.finditer(r"^\[(\d+)\] \([\d.]+s\) (.+)$", prompt, re.M):
        idx, zh = int(m.group(1)), m.group(2)
        if only is not None and idx not in only:
            continue
        a, b = zh.split("，", 1)
        out += [f"[{idx}.1] {a}，", f"[{idx}.2] {b}"]
    return "\n".join(out)


class _Backend:
    name = "stub"

    def __init__(self, plan=None):
        self.plan = plan or {}      # {第几次调用: "error" | "truncate"}
        self.prompts = []

    def translate_raw(self, prompt, max_tokens=4096):
        call = len(self.prompts)
        self.prompts.append(prompt)
        kind = self.plan.get(call)
        if kind == "error":
            # D-1 A4（合同变更）：断网现由付费请求函数原地等、重发，不再让这一批不拆；
            # 「这一批不拆」只剩回答不可用（空回答）这一种来源。
            return TranslationResult(translated=[""], backend_name=self.name,
                                     failure_kind="empty_translation")
        if kind == "truncate":
            n = len(re.findall(r"^\[\d+\] ", prompt, re.M))
            return TranslationResult(translated=[_answer(prompt, only=set(range(n // 2)))],
                                     backend_name=self.name,
                                     tokens_used={"output_tokens": max_tokens})
        return TranslationResult(translated=[_answer(prompt)], backend_name=self.name,
                                 tokens_used={"output_tokens": 500})


def _split(n, backend):
    segs = [_seg(i, ZH) for i in range(n)]
    return ss.llm_split_segments(segs, llm_backend=backend, on_wait=lambda *a: None)


def _was_split(seg):
    return dt.SPLIT_MARK in seg.zh


# ── 门槛 20 → 16 ──

def test_threshold_is_sixteen_chars():
    assert dt.MAX_ZH_CHARS == 16
    assert ss._is_long_zh_segment(_seg(0, "一" * 17))
    assert not ss._is_long_zh_segment(_seg(0, "一" * 16))


# ── 提示词 ──

def test_prompt_uses_new_rules_and_line_per_part_example():
    backend = _Backend()
    _split(1, backend)
    prompt = backend.prompts[0]
    # 插入④施工B（P529/P532，用户 2026-10-24 同意换答案）：只数汉字、附英文原文、需要几刀给几刀、不许跳过
    # 插入④ 施工 B（D2/D11/D12，用户 2026-10-26 定稿）：开头改成「大多超过」（短句有长停顿也送）；
    # 例子换成带「‖ 刀前词 ｜ 刀后词」的自编句子
    assert "大多超过16个汉字（不算标点）" in prompt
    assert "需要几刀就给几刀" in prompt and "一定要给切点" in prompt
    assert "直接跳过" not in prompt
    assert "不要把「把、在、从、给、对、和、或、跟、的、了」这类字留在一段的末尾" in prompt
    assert "不要拆开一个词、人名或地名" in prompt
    assert "→" not in prompt
    assert "  [0.1] 我觉得有时候最好的旅行决定 ‖ decision ｜ is\n  [0.2] 就是多做你已经热爱的事\n" in prompt


# ── 分批 ──

def test_long_sentences_are_sent_in_batches_of_eighty():
    assert ss._SPLIT_BATCH == 80
    backend = _Backend()
    out = _split(170, backend)
    # 插入④ 施工 B D7：替身回答没报词，所有批之后整片回问一次补词（第 4 个提示词）
    assert [len(re.findall(r"^\[\d+\] ", p, re.M)) for p in backend.prompts[:3]] == [80, 80, 10]
    assert len(backend.prompts) == 4 and "第二部分" in backend.prompts[3]
    assert all(_was_split(s) for s in out)
    assert len(out) == 170


# ── 决定 36 ──

def test_one_batch_error_only_leaves_that_batch_unsplit():
    out = _split(170, _Backend({1: "error"}))
    assert all(_was_split(s) for s in out[:80])
    assert not any(_was_split(s) for s in out[80:160])
    assert all(_was_split(s) for s in out[160:])


def test_truncated_batch_only_loses_sentences_after_cut_point():
    out = _split(170, _Backend({0: "truncate"}))
    assert all(_was_split(s) for s in out[:40])
    assert not any(_was_split(s) for s in out[40:80])
    assert all(_was_split(s) for s in out[80:])


# ── 统计日志 ──

def test_summary_log_counts_sent_adopted_errors_and_truncation(caplog):
    with caplog.at_level(logging.INFO, logger="backend.core.sentence_splitter"):
        _split(170, _Backend({0: "truncate", 2: "error"}))
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("AI 拆句：发出"))
    assert "发出 170 条" in line and "采用 120 条" in line
    assert "出错 1 批" in line and "截断 1 批" in line
    assert "没回答 40" in line and "调用出错 10" in line
