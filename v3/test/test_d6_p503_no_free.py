"""D-6 P503：删免费翻译（合同见 pipelines/bug-pipeline-2026-11-04-D6-P503.md ⑤.5 K1-K12）。

用户流程：AI 自动重试后仍翻不出的句子 → 「部分完成」弹窗（手动重试 / 导出）。
中间不再夹任何免费引擎；没 Key 的人在门口只看到「去拿 Key」。
"""
from pathlib import Path
import inspect
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import main
from backend.translation import orchestrator
from backend.translation.base import TranslationResult

TEXTS = ["hello", "how are you", "good night"]


@pytest.fixture
def no_network(monkeypatch):
    """记录任何对外网络请求。免费引擎都走 urllib；付费假后端不走网络。"""
    calls = []

    def fake_urlopen(*args, **kwargs):
        calls.append(args)
        raise ConnectionError("测试里不许联网")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _s: None)
    return calls


# ── K1：上下文感知路线 AI 翻不出的句子不经免费引擎 ──
# P500 施工 ⑤ / 方案第六节 U1（用户 2026-11-07 定）：上下文感知改走逐段同一个小批函数后，
# 仍翻不出的句子按逐段做法保留原文、任务照常完成，不再留「[翻译失败]」进弹窗。
# 本条要守的核心仍是 D-6 那一半：中间绝不夹免费引擎。


class _CopyBackLLM:
    """付费 AI 能连上，但每次都把英文原样抄回来（摘要也写不出中文）。"""

    name = "copy-back"

    def translate_raw(self, prompt, max_tokens=8192):
        return TranslationResult(translated=["still english"], backend_name=self.name)

    def translate_batch(self, texts, **kwargs):
        return TranslationResult(translated=list(texts), backend_name=self.name)


def test_context_aware_ai_failure_keeps_source_without_free_engine(
    monkeypatch, no_network
):
    monkeypatch.setattr(orchestrator, "_build_llm_backends", lambda _keys: [_CopyBackLLM()])

    result = orchestrator.translate_with_orchestrator(
        texts=list(TEXTS), api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=True, progress_callback=lambda *a: None,
    )
    translated, failed, _tokens, _splits, _failure_by_index = result

    assert no_network == [], "AI 翻不出的句子不得再交给任何免费引擎"
    assert translated == TEXTS, "U1：保留原文"
    assert failed == []


def test_context_aware_route_has_no_free_fill_left():
    assert not hasattr(orchestrator, "_fill_gaps_with_free")
    assert not hasattr(orchestrator, "FREE_FILL_PREFIX")
    params = inspect.signature(orchestrator._translate_llm_context_aware).parameters
    assert "ordered_backends" not in params


# ── K2：弹窗「再次重试」不再读免费引擎顺序 ──

def test_partial_retry_does_not_read_free_engine_order():
    assert "translationPriority" not in inspect.getsource(main.partial_action)
