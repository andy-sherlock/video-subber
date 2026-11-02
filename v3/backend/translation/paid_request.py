"""D-1 付费请求函数：付费模式下四个 AI 步骤的每一次真实请求都经过这里。

只管「话送没送到」，不管「AI 答得好不好」：
  · 连不上 / 超时 / 服务器出错 / 太忙 / 余额不足 → 先按用户排的顺序换 Key，
    全都不通才原地等（5 / 15 / 30 秒，之后每 180 秒；平台给的 Retry-After
    更长就照平台的），等完从第一个 Key 重新试；
  · 某个 Key 被拒（401 / 403）、请求被拒（其他 4xx）、认不出的错 → 这个 Key 本次
    不再用，换下一个；全部 Key 都这样 → 抛 PaidAIUnavailable，任务停在出错
    （⑦.5 R1：门口测试按「某个 Key 通了就放行」，任务里也要按同一把尺子换 Key）；
  · AI 回复格式类问题（编号不对、空回答、质量不过）原样交回调用方处理。

装在断句、润色、长句切分、逐段翻译各自的调用处（方案 C3），不进 base.py 的
公共请求方法——上下文感知路线也走那些方法，它的行为本批一行不变（B14）。
"""

from __future__ import annotations

import logging
import re
import time

from .base import classify_translation_failure, retry_after_seconds

_log = logging.getLogger("backend.translation")

WAIT_SCHEDULE = (5, 15, 30)
WAIT_STEADY = 180

RECOVERABLE_KINDS = frozenset({
    "connection", "timeout", "service_error", "rate_limit", "insufficient_balance",
})
_KEY_REJECTED = "auth"
_STOP_NOW = frozenset({"request_rejected", "unknown"})


class PaidAIUnavailable(RuntimeError):
    """不会自己好的错：任务停在「出错」，文字直接给用户看。

    文字里不放 401 / 403 / 429 / Timeout 这类字样——worker._friendly_error
    按子串改写错误文字，带上它们会被换成别的话。
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def platform_label(backend_name: str) -> str:
    """'DeepSeek(deepseek-chat)' → 'DeepSeek'。"""
    return re.sub(r"\(.*\)\s*$", "", str(backend_name or "")).strip() or "AI 服务"


def wait_message(kind: str, backend_name: str) -> str:
    name = platform_label(backend_name)
    if kind == "insufficient_balance":
        return f"{name} 余额不足，充值后会自动继续"
    if kind == "service_error":
        return f"{name} 服务器出错，恢复后自动继续"
    if kind == "rate_limit":
        return f"{name} 太忙，稍后自动继续"
    return f"正在等待连接 {name}，恢复后自动继续"


def _unavailable(kind: str, backend_name: str) -> PaidAIUnavailable:
    name = platform_label(backend_name)
    if kind == _KEY_REJECTED:
        text = f"{name} 的 API Key 全部被拒绝，请检查 Key 是否正确、是否已过期"
    elif kind == "request_rejected":
        text = f"{name} 拒绝了这次请求，请检查设置里的模型名是否可用"
    else:
        text = f"调用 {name} 时出现无法识别的错误，详细原因已写入日志"
    return PaidAIUnavailable(kind, text)


def _outcome(backend, request):
    """发一次请求。返回 (结果, 类别, Retry-After)；类别为 None 表示交回调用方。"""
    name = getattr(backend, "name", "unknown")
    try:
        result = request(backend)
    except Exception as error:
        kind = classify_translation_failure(error)
        if kind == "unknown":
            _log.error("付费请求出现无法识别的错误 (%s): %r", name, error, exc_info=error)
        return None, kind, retry_after_seconds(error)
    kind = getattr(result, "failure_kind", None)
    if kind in RECOVERABLE_KINDS or kind == _KEY_REJECTED or kind in _STOP_NOW:
        if kind == "unknown":
            _log.error("付费请求出现无法识别的错误 (%s)，原始异常见上一条日志", name)
        return result, kind, getattr(result, "retry_after", None)
    return result, None, None


def _wait(seconds):
    time.sleep(seconds)


def call_paid(backends, request, *, on_wait, on_resume=None, sleep=None):
    """按用户排序逐个 Key 发 `request(backend)`，返回 (backend, 结果)。

    `on_wait(kind, backend_name, seconds)` 必填：每次原地等之前调用，用来更新进度文字；
    睡醒后、再发请求前再调一次（seconds=0）——主程序在等待期间没了，汇报就写不出去，
    干活程序在汇报出口里直接结束，不会先多发一次付费请求（P514）。
    `on_resume()` 在等过之后第一次成功时调用一次，把进度文字换回正常的（⑦.5 R3）。
    """
    if sleep is None:
        sleep = _wait
    backends = [backend for backend in backends if backend is not None]
    if not backends:
        raise PaidAIUnavailable("auth", "没有可用的 API Key，请在设置里添加")
    dead = {}   # 本次调用里不会自己好的 Key → (类别, 名字)，按用户排序记下
    waited = 0
    while True:
        healable = None
        longest_retry_after = 0
        for backend in backends:
            if id(backend) in dead:
                continue
            result, kind, retry_after = _outcome(backend, request)
            name = getattr(backend, "name", "unknown")
            if kind is None:
                if waited and on_resume is not None:
                    on_resume()
                return backend, result
            if kind in RECOVERABLE_KINDS:
                if healable is None:
                    healable = (kind, name)
                longest_retry_after = max(longest_retry_after, retry_after or 0)
            else:
                dead[id(backend)] = (kind, name)
            _log.warning("付费请求失败，换下一个 Key: backend=%s kind=%s", name, kind)
        if healable is None:
            kind, name = next(iter(dead.values()))
            raise _unavailable(kind, name)
        base = WAIT_SCHEDULE[waited] if waited < len(WAIT_SCHEDULE) else WAIT_STEADY
        seconds = max(base, longest_retry_after)
        kind, name = healable
        _log.warning("付费请求全部不通，原地等待 %d 秒: kind=%s backend=%s", seconds, kind, name)
        on_wait(kind, name, seconds)
        sleep(seconds)
        waited += 1
        on_wait(kind, name, 0)
