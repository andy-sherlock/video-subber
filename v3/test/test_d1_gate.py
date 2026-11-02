"""D-1 C1 / C12：门口测试接口 POST /api/llm-test。

  · 与任务同一构造函数（_build_llm_backends），按用户排序逐个 Key 测；
  · 先测断句润色那条 JSON 请求路，通了即放行；
  · 某 Key JSON 路不通（结构性：请求被拒 / 认不出的错）但翻译路通 →「只能翻译」；
  · 偶发一次网络类失败不得误报「只能翻译」——同一 Key 再测一次 JSON 路；
  · 都不通 → 返回真实原因类别；不回传、不记录 Key。
"""

from email.message import Message
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation import orchestrator
from backend.translation.base import TranslationResult


def _http_error(code):
    return urllib.error.HTTPError("https://api.example.test", code, "x", Message(), None)


class Probe:
    def __init__(self, name, json_script=(), raw_script=()):
        self.name = name
        self.json_script = list(json_script)
        self.raw_script = list(raw_script)
        self.json_calls = 0
        self.raw_calls = 0

    def translate_raw_json(self, prompt, system_prompt=None):
        self.json_calls += 1
        step = self.json_script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def translate_raw(self, prompt, max_tokens=8192):
        self.raw_calls += 1
        step = self.raw_script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _ok_raw():
    return TranslationResult(translated=["你好"], backend_name="x")


def _post(monkeypatch, backends, keys=None):
    from fastapi.testclient import TestClient
    from backend.main import app

    seen = {}

    def build(entries):
        seen["entries"] = entries
        return list(backends)

    monkeypatch.setattr(orchestrator, "_build_llm_backends", build)
    keys = keys or [{"platform": "deepseek", "key": "SECRET-KEY"}]
    response = TestClient(app).post("/api/llm-test", json={"apiKeys": keys})
    assert response.status_code == 200
    return response.json(), seen


def test_json_route_ok_passes(monkeypatch):
    p = Probe("DeepSeek(x)", json_script=['{"ok": true}'])
    body, seen = _post(monkeypatch, [p])
    assert body["ok"] is True and body["translate_only"] is False
    assert seen["entries"][0]["key"] == "SECRET-KEY"   # 与任务同一构造函数、同一份 Key
    assert p.raw_calls == 0
    assert "SECRET-KEY" not in str(body)


def test_first_key_bad_second_key_ok_passes(monkeypatch):
    a = Probe("A", json_script=[_http_error(401)])
    b = Probe("B", json_script=['{"ok": true}'])
    body, _ = _post(monkeypatch, [a, b])
    assert body["ok"] is True


def test_claude_like_json_route_broken_translate_ok_is_translate_only(monkeypatch):
    p = Probe("Claude(x)", json_script=[_http_error(400)], raw_script=[_ok_raw()])
    body, _ = _post(monkeypatch, [p])
    assert body["ok"] is False and body["translate_only"] is True
    assert body["platform"] == "Claude"


def test_gemini_like_empty_url_is_translate_only(monkeypatch):
    p = Probe("Gemini(x)", json_script=[ValueError("unknown url type: ''")], raw_script=[_ok_raw()])
    body, _ = _post(monkeypatch, [p])
    assert body["translate_only"] is True


def test_one_off_network_failure_is_retried_not_translate_only(monkeypatch):
    p = Probe("DeepSeek(x)", json_script=[ConnectionError("blip"), '{"ok": true}'], raw_script=[_ok_raw()])
    body, _ = _post(monkeypatch, [p])
    assert body["ok"] is True and body["translate_only"] is False
    assert p.raw_calls == 0


def test_both_routes_down_reports_real_reason_not_translate_only(monkeypatch):
    p = Probe(
        "DeepSeek(x)",
        json_script=[ConnectionError("down"), ConnectionError("down")],
        raw_script=[TranslationResult(translated=[""], backend_name="x", failure_kind="connection")],
    )
    body, _ = _post(monkeypatch, [p])
    assert body["ok"] is False and body["translate_only"] is False
    assert body["kind"] == "connection"


def test_wrong_key_reports_auth(monkeypatch):
    p = Probe("DeepSeek(x)", json_script=[_http_error(401)],
              raw_script=[TranslationResult(translated=[""], backend_name="x", failure_kind="auth")])
    body, _ = _post(monkeypatch, [p])
    assert body == {"ok": False, "translate_only": False, "kind": "auth", "platform": "DeepSeek"}


def test_no_usable_key(monkeypatch):
    body, _ = _post(monkeypatch, [], keys=[{"platform": "deepseek", "key": ""}])
    assert body["ok"] is False and body["kind"] == "no_key"


def test_endpoint_uses_same_constructor_as_task():
    """C1：门口测试与任务用同一个构造函数（不是另写一套平台判断）。"""
    import inspect
    from backend import main

    # P512：门口入口与抽屉入口共用 _probe_api_keys
    source = inspect.getsource(main._probe_api_keys)
    assert "_build_llm_backends" in source
