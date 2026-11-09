"""P500 施工 ③：逐段翻译函数加可选参数 `reference`，不传或传 None 时一字不变。

方案 `pipelines/方案-P500-上下文感知改小批-2026-11-07.md` 第七节 ③、5.2：
逐段翻译（用户默认走的路）在加参数前后，同一输入、同一假 AI，
发给 AI 的每一句请求文字和最后交出的结果都必须一字不变。

标准答案 `p500_per_batch_golden.json` 是用**改动之前**的代码（提交 a07b277）录下来的。
录法：`P500_REGEN_GOLDEN=1 .venv/bin/python -m pytest test/test_p500_step3_per_batch_reference.py`
——只在改动前录过一次，之后不许重录（重录 = 拿新代码给自己出答案）。

共享规则常量（SHARED_TRANSLATION_RULES、LAYOUT_FRIENDLY_RULES）在比对前替换成占位符，
这样以后合法地改规则文字不会误伤本测试；本测试只管「逐段的拼法有没有变」。
"""

import json
import os
from pathlib import Path

import pytest

from backend.translation import base as translation_base
from backend.translation import orchestrator as orch

GOLDEN = Path(__file__).parent / "p500_per_batch_golden.json"

_BATCH_MARKER = "仅按 [编号] 译文 格式输出"


class _ScriptedLLM(translation_base.LLMBackend):
    """假 AI：按「第几次被调用」交出预先写好的回答，并记下收到的完整请求文字。"""

    def __init__(self, answers):
        self._answers = list(answers)
        self.prompts = []

    @property
    def name(self):
        return "scripted-llm"

    @property
    def _api_url(self):
        return "https://example.invalid/v1/chat/completions"

    def _build_headers(self):
        return {"Content-Type": "application/json"}

    def _call_api_parse(self, response_data):
        return ""

    def _call_api_raw(self, prompt, max_tokens=8192):
        self.prompts.append(prompt)
        answer = self._answers[len(self.prompts) - 1]
        return answer, {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _numbered(lines):
    return "\n".join(f"[{i}] {t}" for i, t in enumerate(lines))


TEXTS = [
    "Hello everyone, welcome to Queenstown.",
    "Today we are going bungee jumping.",
    "My friend Sarah is very nervous.",
    "Bonjour. Bonjour.",
]

# 三种场景，覆盖逐段函数里所有会发请求的地方：首次整批、整批纠正、半批纠正、逐句重试。
SCENARIOS = {
    # ① 一次就全对
    "全对": [
        _numbered(["大家好，欢迎来到皇后镇。", "今天我们去蹦极。", "我的朋友莎拉很紧张。", "你好。你好。"]),
    ],
    # ② 有一句抄回原文（中文检查拦下）→ 只对这一句逐句重试
    "一句没翻": [
        _numbered(["大家好，欢迎来到皇后镇。", "今天我们去蹦极。", "我的朋友莎拉很紧张。", "Bonjour。Bonjour。"]),
        "你好。你好。",
    ],
    # ③ 条数不对 → 整批纠正（仍不对）→ 前半批对、后半批不对 → 后半批逐句重试
    "条数不对": [
        "[0] 大家好，欢迎来到皇后镇。今天我们去蹦极。\n[1] 我的朋友莎拉很紧张。你好。",
        "[0] 大家好，欢迎来到皇后镇。今天我们去蹦极。",
        _numbered(["大家好，欢迎来到皇后镇。", "今天我们去蹦极。"]),
        "[0] 我的朋友莎拉很紧张。你好。",
        "我的朋友莎拉很紧张。",
        "你好。你好。",
    ],
}


def _normalize(prompt):
    prompt = prompt.replace(orch.SHARED_TRANSLATION_RULES, "<SHARED_TRANSLATION_RULES>")
    layout = getattr(translation_base, "LAYOUT_FRIENDLY_RULES", None)
    if layout:
        prompt = prompt.replace(layout, "<LAYOUT_FRIENDLY_RULES>")
    return prompt


def _run(scenario, monkeypatch, **extra):
    monkeypatch.setattr(orch.time, "sleep", lambda *_a: None)
    backend = _ScriptedLLM(SCENARIOS[scenario])
    translated, failed, tokens, splits, failures = orch._translate_llm_per_batch(
        list(TEXTS), [backend], progress_callback=lambda *a: None, **extra,
    )
    return {
        "prompts": [_normalize(p) for p in backend.prompts],
        "translated": translated,
        "failed": failed,
        "tokens": tokens,
        "splits": {str(k): v for k, v in splits.items()},
        "failures": {str(k): v for k, v in failures.items()},
    }


def _golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.skipif(not os.environ.get("P500_REGEN_GOLDEN"), reason="只在改动前录一次标准答案")
def test_record_golden(monkeypatch):
    data = {"录制代码版本": "a07b277（P500 施工 ③ 改动前）"}
    for name in SCENARIOS:
        data[name] = _run(name, monkeypatch)
    GOLDEN.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def test_golden_covers_every_request_site():
    """标准答案本身要真的走到四个发请求的地方，否则「一字不变」证明不了什么。"""
    g = _golden()
    assert len(g["全对"]["prompts"]) == 1
    assert len(g["一句没翻"]["prompts"]) == 2
    prompts = g["条数不对"]["prompts"]
    assert len(prompts) == 6, "首发 + 整批纠正 + 两个半批 + 两次逐句重试"
    assert orch._PAID_BATCH_RETRY_CORRECTION in prompts[1]
    assert orch._PAID_BATCH_RETRY_CORRECTION in prompts[2]
    assert orch._PAID_BATCH_RETRY_CORRECTION in prompts[3]
    assert _BATCH_MARKER not in prompts[4], "第 5 次是逐句重试"
    assert g["条数不对"]["translated"][2:] == ["我的朋友莎拉很紧张。", "你好。你好。"]


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_per_batch_without_reference_is_unchanged(scenario, monkeypatch):
    """不传 reference（现在所有调用点都这样）：请求文字与结果和改动前一字不变。"""
    assert _run(scenario, monkeypatch) == _golden()[scenario]


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_per_batch_reference_none_is_unchanged(scenario, monkeypatch):
    """显式传 reference=None：同样一字不变。"""
    assert _run(scenario, monkeypatch, reference=None) == _golden()[scenario]

