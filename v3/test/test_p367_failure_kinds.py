"""P367：翻译失败原因的稳定分类（付费 AI 的重试与「部分完成」弹窗都靠它）。

D-6 P503 删免费引擎后，原来经由三家免费引擎间接覆盖的分类改为直接测 classify_translation_failure。
"""

import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import classify_translation_failure


def _http_error(status_code):
    return urllib.error.HTTPError(
        url="https://example.invalid",
        code=status_code,
        msg="fake HTTP failure",
        hdrs={},
        fp=None,
    )


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        pytest.param(urllib.error.URLError(ConnectionRefusedError("connection refused")),
                     "connection", id="connection"),
        pytest.param(TimeoutError("timed out"), "timeout", id="timeout"),
        pytest.param(_http_error(429), "rate_limit", id="http-429"),
        pytest.param(_http_error(503), "service_error", id="http-5xx"),
        # D-1 增 3：除 401/402/403/429 外的 4xx 单独成「请求被拒」（以前归 service_error）。
        pytest.param(_http_error(404), "request_rejected", id="http-404"),
        pytest.param(ssl.SSLError("certificate verify failed"), "connection", id="ssl"),
    ],
)
def test_failure_kind_is_stable(error, expected_kind):
    assert classify_translation_failure(error) == expected_kind


def test_unexpected_http_4xx_is_request_rejected():
    # D-1 增 3：不会自己好的 4xx 不能再混进「服务器出错」（付费模式会原地等它）。
    assert classify_translation_failure(_http_error(411)) == "request_rejected"
