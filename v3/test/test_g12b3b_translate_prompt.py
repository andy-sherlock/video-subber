"""G12 第三批 B · T-1～T-3：翻译提示词照顾切割（新2版，排版方案 §16）。

- T-1：新段落只有一份（base.py 常量），只加进逐段翻译 `translate_batch`，紧跟共享规则那一行；
  上下文感知路线不加（决定 33）——P500 施工 ⑤ 起上下文感知改走同一个 translate_batch，
  这条前提消失：两条路都带这段规则（方案第二节 P500：新规则要在上下文感知上也起作用）。
- T-2（§16.9 红线落成测试）：AI 在新提示词下把一句拆成两行、多回一行 → 判条数不对 →
  同一付费 AI 整批重发 → 最终逐条对齐、不顺延。
- T-3：条数不对重发时有一行普通日志，验收能数出次数。
AI 全用受控替身，不联网。
"""

import logging

from backend.translation import base as translation_base
from backend.translation import orchestrator as orch
from test.test_p392_translation_protocol import _CannedLLM, _numbered

# 排版方案 §16.1 原文（实验 `底子/_实验跑.py` NEW_BLOCK_V2），一字不改
BLOCK = (
    "字幕翻译完后会按下面的规则排到屏幕上：每行最多 16 个汉字，每屏最多两行，只在意思完整的地方断开。请在不改变意思的前提下，让译文容易排版：\n"
    "- 意思第一：不许漏译、不许删细节，不许把前后条的内容挪进这一条；否定、数字、人名、人物关系必须准确。\n"
    "- 长句在意思自然停顿的地方用逗号隔开，让每一小段尽量不超过 16 个汉字；超过了就再多断一处，不许为了变短删掉任何意思、细节或程度词（如\"最\"\"之一\"\"非常\"）。\n"
    "- 不要输出任何分段记号，只输出译文本身。"
)


def test_t1_block_is_sent_right_after_shared_rules_in_per_segment_prompt():
    llm = _CannedLLM(_numbered(["一壶", "茶"]))
    llm.translate_batch(["a pot", "of tea."])
    prompt = llm.prompts[0]
    assert translation_base.LAYOUT_FRIENDLY_RULES == BLOCK
    assert f"{translation_base.SHARED_TRANSLATION_RULES}\n{BLOCK}\n" in prompt
    assert prompt.count(BLOCK) == 1


def _run(monkeypatch, paid_raw):
    texts = [f"source {index}" for index in range(40)]
    paid = _CannedLLM(paid_raw)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda _keys: [paid])
    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts, api_keys=[{"platform": "deepseek", "key": "x"}], context_aware=False)
    return paid, translated, failed


def test_t2_extra_split_line_triggers_whole_batch_resend_without_shifting(monkeypatch, caplog):
    calls = []

    def _paid_raw(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            # 第 17 条被 AI 从逗号处拆成两行，后面每条往后挪一格：40 条回来 41 行
            lines = [f"译文-{i}" for i in range(17)] + ["译文-17前半，", "译文-17后半"]
            lines += [f"译文-{i}" for i in range(18, 40)]
            return _numbered(lines)
        return _numbered([f"译文-{i}" for i in range(40)])

    with caplog.at_level(logging.INFO, logger="backend.translation.orchestrator"):
        paid, translated, failed = _run(monkeypatch, _paid_raw)

    assert BLOCK in paid.prompts[0]
    assert len(paid.prompts) == 2                       # 整批重发一次
    assert orch._PAID_BATCH_RETRY_CORRECTION in paid.prompts[1]
    assert translated == [f"译文-{i}" for i in range(40)]   # 逐条对齐，没有顺延
    assert failed == []
    # T-3：一行普通日志，验收时能数出「因条数不对重发」的次数
    assert sum("条数不对，整批重发" in r.getMessage() for r in caplog.records
               if r.levelno >= logging.INFO) == 1
