"""P367: free translation backends expose stable failure kinds."""

import json
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation import bing as bing_module
from backend.translation import google as google_module
from backend.translation import youdao as youdao_module
from backend.translation.bing import BingBackend
from backend.translation.base import TranslationBackend, TranslationResult, classify_translation_failure
from backend.translation.google import GoogleBackend
from backend.translation.orchestrator import _translate_free_only
from backend.translation.youdao import YoudaoBackend


class _FakeResponse:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FailedBackend(TranslationBackend):
    def __init__(self, failure_kind):
        self._failure_kind = failure_kind

    @property
    def name(self):
        return "故障测试引擎"

    def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
        return TranslationResult(
            translated=["[翻译失败]"] * len(texts),
            backend_name=self.name,
            failure_kind=self._failure_kind,
        )


def _http_error(status_code):
    return urllib.error.HTTPError(
        url="https://example.invalid",
        code=status_code,
        msg="fake HTTP failure",
        hdrs={},
        fp=None,
    )


def _failure_exception(case):
    return {
        "connection": lambda: urllib.error.URLError(
            ConnectionRefusedError("connection refused")
        ),
        "timeout": lambda: TimeoutError("timed out"),
        "rate_limit": lambda: _http_error(429),
        "service_error": lambda: _http_error(503),
        "http_unknown": lambda: _http_error(404),
        "ssl": lambda: ssl.SSLError("certificate verify failed"),
    }[case]()


def _install_fake_http(monkeypatch, engine, case):
    if engine == "bing":
        monkeypatch.setitem(bing_module._token_cache, "token", "")
        monkeypatch.setitem(bing_module._token_cache, "expires", 0.0)

    def fake_urlopen(request, timeout):
        url = request.full_url
        if url == bing_module._AUTH_URL:
            return _FakeResponse(b"fake-token")

        if case != "empty_translation":
            raise _failure_exception(case)

        if engine == "google":
            return _FakeResponse([[['', 'hello', None, None]]])
        if engine == "bing":
            return _FakeResponse([{"translations": [{"text": ""}]}])
        return _FakeResponse({"errorCode": "0", "translation": [""]})

    engine_module = {
        "google": google_module,
        "bing": bing_module,
        "youdao": youdao_module,
    }[engine]
    monkeypatch.setattr(engine_module.urllib.request, "urlopen", fake_urlopen)


@pytest.mark.parametrize(
    ("engine", "backend_type"),
    [
        pytest.param("google", GoogleBackend, id="google"),
        pytest.param("bing", BingBackend, id="bing"),
        pytest.param("youdao", YoudaoBackend, id="youdao"),
    ],
)
@pytest.mark.parametrize(
    ("case", "expected_kind"),
    [
        pytest.param("connection", "connection", id="connection"),
        pytest.param("timeout", "timeout", id="timeout"),
        pytest.param("rate_limit", "rate_limit", id="http-429"),
        pytest.param("service_error", "service_error", id="http-5xx"),
        # D-1 增 3：除 401/402/403/429 外的 4xx 单独成「请求被拒」（以前归 service_error）。
        pytest.param("http_unknown", "request_rejected", id="http-404"),
        pytest.param("ssl", "connection", id="ssl"),
        pytest.param("empty_translation", "empty_translation", id="empty-translation"),
    ],
)
def test_free_backend_reports_stable_failure_kind(
    monkeypatch, engine, backend_type, case, expected_kind
):
    _install_fake_http(monkeypatch, engine, case)

    result = backend_type().translate_batch(
        ["hello"], source_lang="en", target_lang="zh"
    )

    assert result.translated == ["[翻译失败]"]
    assert result.failure_kind == expected_kind


@pytest.mark.parametrize("mode", ["speed", "quality"])
def test_free_only_orchestrator_preserves_backend_failure_kind(monkeypatch, mode):
    monkeypatch.setattr("backend.translation.orchestrator.time.sleep", lambda _seconds: None)

    results, failed, _tokens, _variants, failure_by_index = _translate_free_only(
        ["hello"], mode, [_FailedBackend("rate_limit")]
    )

    assert results == ["[翻译失败]"]
    assert failed == [0]
    assert failure_by_index == {0: "rate_limit"}


def test_unexpected_http_4xx_is_request_rejected():
    # D-1 增 3：不会自己好的 4xx 不能再混进「服务器出错」（付费模式会原地等它）。
    assert classify_translation_failure(_http_error(411)) == "request_rejected"
