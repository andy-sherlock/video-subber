"""D-1 A2 / A3 / A4 / A7：断句、润色、长句切分接上付费请求函数。

  · 请求出错（会自己好）→ 原地等、恢复后接着用 AI，不退规则、不跳过；
  · 第一个 Key 坏 → 用第二个 Key（A7：不再只取第一个）；
  · 不会自己好的错（全部 Key 被拒）→ 一路抛到任务出错，任何一层不放行；
  · 格式类问题照旧：断句两次不可用 → 规则；润色解析失败 → 整轮不落盘；
    切分空回答 → 这一批不拆。
"""

import json
from pathlib import Path
import sys
import urllib.error
from email.message import Message

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation import paid_request
from backend.translation.base import TranslationResult
from backend.translation.paid_request import PaidAIUnavailable


def _http_error(code):
    return urllib.error.HTTPError("https://api.example.test", code, "x", Message(), None)


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(paid_request, "_wait", recorded.append)
    return recorded


class JsonLLM:
    """translate_raw_json 按剧本：异常就抛，字符串就返回。"""

    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.calls = 0

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls += 1
        step = self.script.pop(0) if self.script else self.script_default
        if isinstance(step, BaseException):
            raise step
        return step

    script_default = '{"corrections": []}'


# ── 断句（A2） ─────────────────────────────

def _w(text, start, end):
    return {"word": text, "text": text, "start": start, "end": end}


WORDS = [_w("One.", 0.0, 0.3), _w("two", 0.5, 0.8), _w("three.", 1.0, 1.3)]
TEXT = " ".join(w["text"] for w in WORDS)
GOOD_BREAKS = json.dumps({"break_after_indices": [0]})


def test_breaks_network_error_waits_then_uses_ai(waits):
    from backend import worker

    llm = JsonLLM("DeepSeek(x)", [ConnectionError("down"), GOOD_BREAKS])
    breaks, reason = worker._collect_llm_primary_breaks(TEXT, WORDS, "en", [llm])
    assert reason is None and breaks is not None
    assert list(waits) == [5] and llm.calls == 2


def test_breaks_first_key_down_uses_second_key(waits):
    from backend import worker

    a = JsonLLM("A", [ConnectionError("down")])
    b = JsonLLM("B", [GOOD_BREAKS])
    breaks, reason = worker._collect_llm_primary_breaks(TEXT, WORDS, "en", [a, b])
    assert reason is None and b.calls == 1 and list(waits) == []


def test_breaks_all_keys_rejected_raises_not_rule_fallback(waits):
    from backend import worker

    a = JsonLLM("A", [_http_error(401)])
    with pytest.raises(PaidAIUnavailable):
        worker._collect_llm_primary_breaks(TEXT, WORDS, "en", [a])


def test_breaks_bad_format_twice_still_falls_back_to_rules(waits):
    """C8 例外 ①：AI 回复格式两次不可用 → 规则断句（并由调用方写说明）。"""
    from backend import worker

    llm = JsonLLM("A", ["not json", "still not json"])
    breaks, reason = worker._collect_llm_primary_breaks(TEXT, WORDS, "en", [llm])
    assert breaks is None and reason == "AI 答案未通过校验"
    assert list(waits) == []


def test_breaks_single_backend_still_accepted(waits):
    """旧调用方（测试注入的 llm_boundary_backend）传单个后端，照常工作。"""
    from backend import worker

    llm = JsonLLM("A", [GOOD_BREAKS])
    _breaks, reason = worker._collect_llm_primary_breaks(TEXT, WORDS, "en", llm)
    assert reason is None


# ── 润色（A3） ─────────────────────────────

class Seg:
    def __init__(self, i, text):
        self.id = i
        self.text = text
        self.start = float(i)
        self.end = float(i) + 1.0
        self.l2_status = None
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.sensevoice_tag = None
        self.bgm_override = False
        self.whisper_text = ""


def _segs(n=3):
    return [Seg(i, f"sentence number {i}") for i in range(n)]


def test_refiner_window_network_error_waits_and_no_window_fails(waits):
    from backend.worker import _run_refiner

    llm = JsonLLM("DeepSeek(x)", [ConnectionError("down")])
    stats = _run_refiner(_segs(), [llm], window_size=10, context_pad=0)
    assert stats["failed"] == 0
    assert list(waits) == [5]


def test_refiner_all_keys_rejected_raises(waits):
    from backend.worker import _run_refiner

    llm = JsonLLM("A", [_http_error(403)])
    with pytest.raises(PaidAIUnavailable):
        _run_refiner(_segs(), [llm], window_size=10, context_pad=0)


def test_transcript_refiner_outer_layer_does_not_release_unavailable(waits, tmp_path):
    """A3 外层（原 :2973-2975）：不会自己好的错穿过两层 → 抛出，不得「放行原始 segments」。"""
    from backend import worker

    llm = JsonLLM("A", [_http_error(401)])
    with pytest.raises(PaidAIUnavailable):
        worker._run_transcript_refiner(
            [{"platform": "deepseek", "key": "k"}], _segs(),
            refined_path=tmp_path / "refined.json", llm_backend=[llm],
            window_size=10, context_pad=0,
        )


def test_transcript_refiner_reports_format_failure(waits, tmp_path):
    """C8 例外 ①：润色回复格式坏 → 整轮不落盘，并把「格式有误」报给调用方写说明。"""
    from backend import worker

    llm = JsonLLM("A", ["这不是 JSON"])
    status = worker._run_transcript_refiner(
        [{"platform": "deepseek", "key": "k"}], _segs(),
        refined_path=tmp_path / "refined.json", llm_backend=[llm],
        window_size=10, context_pad=0,
    )
    assert status == "format_failed"
    assert not (tmp_path / "refined.json").exists()


def test_transcript_refiner_success_reports_nothing(waits, tmp_path):
    from backend import worker

    llm = JsonLLM("A", ['{"corrections": []}'])
    status = worker._run_transcript_refiner(
        [{"platform": "deepseek", "key": "k"}], _segs(),
        refined_path=tmp_path / "refined.json", llm_backend=[llm],
        window_size=10, context_pad=0,
    )
    assert status is None
    assert (tmp_path / "refined.json").exists()


# ── 长句切分（A4） ─────────────────────────────

class SplitBackend:
    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.calls = 0

    def translate_raw(self, prompt, max_tokens=4096):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(prompt)
        return step


def _long_segs(n=2):
    from backend.transcriber_v2 import Segment

    out = []
    for i in range(n):
        s = Segment(id=i, start=float(i), end=float(i) + 3.0, text="we are happy")
        s.zh = "我们很乐意说明一下这件事情，您预计的出发时间是十点二十五分"
        s.speaker_change = False
        s.words = []
        out.append(s)
    return out


def _empty_answer(_prompt):
    return TranslationResult(translated=[""], backend_name="x")


def test_splitter_network_error_waits_and_asks_again(waits):
    from backend.core import sentence_splitter as ss

    backend = SplitBackend("DeepSeek(x)", [ConnectionError("down"), _empty_answer])
    ss.llm_split_segments(_long_segs(), llm_backend=[backend], on_wait=lambda *a: None)
    assert backend.calls == 2 and list(waits) == [5]


def test_splitter_first_key_down_uses_second_key(waits):
    from backend.core import sentence_splitter as ss

    a = SplitBackend("A", [ConnectionError("down")])
    b = SplitBackend("B", [_empty_answer])
    ss.llm_split_segments(_long_segs(), llm_backend=[a, b], on_wait=lambda *a: None)
    assert (a.calls, b.calls) == (1, 1) and list(waits) == []


def test_splitter_all_keys_rejected_raises(waits):
    from backend.core import sentence_splitter as ss

    backend = SplitBackend("A", [_http_error(401)])
    with pytest.raises(PaidAIUnavailable):
        ss.llm_split_segments(_long_segs(), llm_backend=[backend], on_wait=lambda *a: None)


# ── A7：三处不再只取第一个 Key ─────────────────────────────

def test_worker_no_longer_takes_only_first_backend():
    """A7：断句 / 润色 / 切分建后端时传整组 Key（看生产源码里没有 `[0]` 取第一个）。"""
    source = (Path(__file__).resolve().parent.parent / "backend" / "worker.py").read_text(encoding="utf-8")
    assert "backends[0] if backends else None" not in source
    assert "_backends[0] if _backends else None" not in source
    assert "llm_backend = llm_list[0]" not in source
