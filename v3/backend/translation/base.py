"""TranslationBackend ABC — all translation engines follow this contract."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from http.client import BadStatusLine, IncompleteRead
import re
import socket
import ssl
import urllib.error


FAILED_TRANSLATION = "[翻译失败]"
SHARED_TRANSLATION_RULES = (
    "遵循调用方要求的口语、正式或幽默风格；"
    "根据上下文处理多义词；"
    "专有名词合理保留原文或音译。"
    "clock/watch 在询问时间时通常译为“表”；"
    "只有明确指实体钟、钟楼或报时装置时，才译为“钟”或“钟楼”。"
)
# G12 第三批 B（排版方案 §16.1，决定 32）：让译文照顾排版。只有逐段翻译 `translate_batch`
# 引用；上下文感知路线实测不通过，不加（决定 33）。文字一字不改，别处不许再抄。
LAYOUT_FRIENDLY_RULES = (
    "字幕翻译完后会按下面的规则排到屏幕上：每行最多 16 个汉字，每屏最多两行，只在意思完整的地方断开。请在不改变意思的前提下，让译文容易排版：\n"
    "- 意思第一：不许漏译、不许删细节，不许把前后条的内容挪进这一条；否定、数字、人名、人物关系必须准确。\n"
    "- 长句在意思自然停顿的地方用逗号隔开，让每一小段尽量不超过 16 个汉字；超过了就再多断一处，不许为了变短删掉任何意思、细节或程度词（如\"最\"\"之一\"\"非常\"）。\n"
    "- 不要输出任何分段记号，只输出译文本身。"
)
_NUMBERED_TRANSLATION_RE = re.compile(r"^\s*\[(\d+)\]\s*(.*)$")
_BRACKET_NUMBER_RE = re.compile(r"\[\s*\d+(?:\.\d+)?\s*\]")
_NUMBER_CONTROL_ONLY_RE = re.compile(r"^(?:\[\s*\d+(?:\.\d+)?\s*\]\s*[:：,，、;；.\-—]*\s*)+$")
_PURE_PROPER_NAME_RE = re.compile(
    r"^[A-Z][A-Za-z0-9]*(?:['’.-][A-Za-z0-9]+)*"
    r"(?:\s+[A-Z][A-Za-z0-9]*(?:['’.-][A-Za-z0-9]+)*){0,3}$"
)
_COMMON_SUBTITLE_NON_NAME_WORDS = frozenset(
    {
        "i", "you", "he", "she", "it", "we", "they",
        "me", "him", "her", "us", "them",
        "what", "who", "where", "when", "why", "how",
        "yeah", "yes", "no", "ok", "okay",
        "hello", "hi", "hey", "thanks", "thank", "please", "sorry",
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
        "right", "sure", "wow", "oh", "ah", "uh", "um", "hmm", "well",
        "cool", "nice", "fine", "great", "awesome", "amazing", "really",
        "seriously", "exactly", "absolutely", "maybe", "probably",
        "stop", "wait", "look", "listen", "come", "go", "run", "move",
        "hold", "watch", "help", "leave", "stay", "sit", "stand", "turn",
        "take", "give", "get", "let",
        "man", "guys", "dude", "god", "damn", "shit", "fuck",
        "good", "morning", "afternoon", "evening", "night", "bye", "goodbye",
    }
)
_HIGH_CONFIDENCE_REVIEW_PATTERNS = (
    re.compile(r"版本\s*[AB]", re.IGNORECASE),
    re.compile(r"选\s*[AB]", re.IGNORECASE),
    re.compile(r"选择\s*[AB]", re.IGNORECASE),
    re.compile(r"字幕质量审查员"),
    # 只收「审校流程词」——这些词正常字幕几乎不会用来起头。
    #
    # 曾经试过把「原文」「点评」也加进来（P404 第四轮），已撤回：它们是普通词汇，
    # 视频里真的会有人说「原文：Hello」「点评：这很好。」，加进来等于删用户字幕。
    # 不要再往这张表里加普通词。
    #
    # 行首容忍 Markdown 强调、项目符号、序号——模型极爱加这些装饰，
    # 「**建议：**」「1. 建议：」都必须照拦。
    re.compile(
        r"(?:^|\n)\s*"
        r"(?:[-*•·]\s*|\d+[.、)]\s*)*"        # 项目符号 / 序号
        r"[*_【\[]{0,2}\s*"                    # Markdown 强调 / 方括号
        r"(?:建议|原因|解释)"
        r"\s*[*_】\]]{0,2}\s*[:：]"
    ),
)


def _has_structured_number_residue(text: str) -> bool:
    matches = list(_BRACKET_NUMBER_RE.finditer(text))
    numbers = {m.group(0).strip("[] \t") for m in matches}
    if "0" in numbers and "1" in numbers:
        return True
    return bool(matches and _NUMBER_CONTROL_ONLY_RE.match(text.strip()))


def _looks_like_review_comment(text: str) -> bool:
    return any(p.search(text) for p in _HIGH_CONFIDENCE_REVIEW_PATTERNS)


def _translation_text_failure_kind(text: str | None) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "empty_translation"
    normalized = text.strip()
    if normalized == FAILED_TRANSLATION:
        return "quality_rejected"
    if "---" in normalized:
        return "quality_rejected"
    if _has_structured_number_residue(normalized):
        return "quality_rejected"
    if _looks_like_review_comment(normalized):
        return "quality_rejected"
    return None


def is_clean_translation_text(text: str | None) -> bool:
    """Return True only for subtitle text safe enough to write back."""
    return _translation_text_failure_kind(text) is None


def _failed_results_like(texts: list[str]) -> list[str]:
    return ["" if not t.strip() else FAILED_TRANSLATION for t in texts]


def _parse_numbered_batch_with_failure(
    raw: str, expected_count: int
) -> tuple[list[str] | None, str | None]:
    if expected_count == 0 and isinstance(raw, str) and not raw.strip():
        return [], None
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty_translation"
    if "---" in raw or _looks_like_review_comment(raw):
        return None, "quality_rejected"

    entries: dict[int, str] = {}
    current_idx: int | None = None
    current_lines: list[str] = []

    def flush_current() -> str | None:
        nonlocal current_idx, current_lines
        if current_idx is None:
            return None
        if current_idx in entries:
            return "numbering_format"
        text = "\n".join(current_lines).strip()
        failure_kind = _translation_text_failure_kind(text)
        if failure_kind is not None:
            return failure_kind
        entries[current_idx] = text
        return None

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _NUMBERED_TRANSLATION_RE.match(line)
        if m:
            failure_kind = flush_current()
            if failure_kind is not None:
                return None, failure_kind
            next_idx = int(m.group(1))
            if not 0 <= next_idx < expected_count:
                return None, "numbering_format"
            current_idx = next_idx
            current_lines = [m.group(2).strip()]
            continue
        if current_idx is None:
            return None, "numbering_format"
        current_lines.append(line)

    failure_kind = flush_current()
    if failure_kind is not None:
        return None, failure_kind
    if set(entries) != set(range(expected_count)):
        return None, "numbering_format"
    return [entries[i] for i in range(expected_count)], None


def _parse_numbered_batch(raw: str, expected_count: int) -> list[str] | None:
    parts, _ = _parse_numbered_batch_with_failure(raw, expected_count)
    return parts


def _is_chinese_target(target_lang: str) -> bool:
    normalized = (target_lang or "").strip().lower()
    return normalized.startswith("zh") or "中文" in normalized or normalized in {
        "chinese",
        "简体",
        "繁体",
    }


def _is_unchanged_pure_proper_name(source: str, translated: str) -> bool:
    normalized_source = source.strip()
    words = re.findall(r"[A-Za-z]+", normalized_source.casefold())
    return (
        normalized_source == translated.strip()
        and bool(_PURE_PROPER_NAME_RE.fullmatch(normalized_source))
        and not any(word in _COMMON_SUBTITLE_NON_NAME_WORDS for word in words)
    )


def _batch_target_language_failure_kind(
    source_texts: list[str], translated_parts: list[str], target_lang: str
) -> str | None:
    """Reject a non-Chinese batch while allowing unchanged proper-name-only lines."""
    if not _is_chinese_target(target_lang):
        return None

    target_language_parts: list[str] = []
    for source, translated in zip(source_texts, translated_parts):
        if _is_unchanged_pure_proper_name(source, translated):
            continue
        if source.strip() == translated.strip():
            return "quality_rejected"
        target_language_parts.append(translated)

    if not target_language_parts:
        return None
    combined = "".join(target_language_parts)
    cjk_count = sum(1 for char in combined if "\u4e00" <= char <= "\u9fff")
    if cjk_count / max(len(combined), 1) < 0.1:
        return "quality_rejected"
    return None


def _debug_summary(value: str | None) -> str:
    if not value:
        return "(空)"
    return str(value)[:300]


def classify_translation_failure(
    error: BaseException | None = None, *, empty_translation: bool = False
) -> str:
    """Map transport/service failures to a small, stable public contract."""
    if empty_translation:
        return "empty_translation"

    response = getattr(error, "response", None)
    raw_statuses = (
        getattr(error, "code", None),
        getattr(error, "status", None),
        getattr(error, "status_code", None),
        getattr(response, "status_code", None),
    )
    status = None
    for raw_status in raw_statuses:
        try:
            status = int(raw_status)
            break
        except (TypeError, ValueError):
            continue

    if status in (401, 403):
        return "auth"
    if status == 429 and "insufficient_quota" in _error_body_text(error):
        # ⑦.5 R5：OpenAI 额度用完也回 429，但这是没钱了，不是「太忙」。
        return "insufficient_balance"
    if status == 402:
        # D-1 增 1：余额不足要单独告诉用户（充值后会自动继续），不能混进服务器出错。
        return "insufficient_balance"
    if status == 429:
        return "rate_limit"
    if status is not None and 500 <= status <= 599:
        return "service_error"
    if status is not None and 400 <= status <= 499:
        # D-1 增 3：400 / 404 / 413 这类不会自己好，原地等只会永远等下去。
        return "request_rejected"
    if status is not None:
        return "unknown"

    reason = getattr(error, "reason", None)
    if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(
        reason, (TimeoutError, socket.timeout)
    ):
        return "timeout"
    if isinstance(error, ssl.SSLError) or isinstance(reason, ssl.SSLError):
        return "connection"
    if isinstance(error, (ConnectionError, urllib.error.URLError)) or isinstance(
        reason, ConnectionError
    ):
        return "connection"
    # IncompleteRead / BadStatusLine 只挂在 HTTPException 下，不是 ConnectionError，
    # 会被漏成 unknown 而躲过熔断——可连接其实已经断流了。
    # RemoteDisconnected 因为同时继承 ConnectionResetError，上面那条就能认出。
    if isinstance(error, (IncompleteRead, BadStatusLine)) or isinstance(
        reason, (IncompleteRead, BadStatusLine)
    ):
        return "connection"

    message = str(error or "").lower()
    if "rate limit" in message or "too many requests" in message:
        return "rate_limit"
    if any(marker in message for marker in ("unauthorized", "forbidden", "authentication")):
        return "auth"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if any(
        marker in message
        for marker in ("service unavailable", "bad gateway", "internal server error")
    ):
        return "service_error"
    if any(
        marker in message
        for marker in ("connection", "network", "name resolution", "ssl", "certificate")
    ):
        return "connection"
    return "unknown"


def _error_body_text(error) -> str:
    """HTTP 错误回复的正文（只读一次，缓存在异常对象上）。"""
    cached = getattr(error, "_d1_body_text", None)
    if cached is not None:
        return cached
    text = ""
    try:
        reader = getattr(error, "read", None)
        raw = reader() if callable(reader) else b""
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    except Exception:
        text = ""
    try:
        error._d1_body_text = text
    except Exception:
        pass
    return text


def retry_after_seconds(error: BaseException | None) -> int | None:
    """平台在 Retry-After 头里给的秒数；没有或不是秒数就返回 None。"""
    headers = getattr(error, "headers", None)
    if headers is None:
        headers = getattr(getattr(error, "response", None), "headers", None)
    try:
        value = headers.get("Retry-After") if headers is not None else None
        seconds = int(str(value).strip()) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None
    return seconds if seconds is not None and seconds >= 0 else None


def _log_unknown_failure(tlog, name: str, error: BaseException, kind: str) -> None:
    # D-1 增 4：认不出的错会让付费任务停在出错，用户要能从日志里找到根因。
    if kind == "unknown":
        tlog.error("LLM 请求出现无法识别的错误 (%s): %r", name, error, exc_info=error)


@dataclass
class TranslationResult:
    translated: list[str]       # Same length as input, failed items = "[翻译失败]"
    backend_name: str           # Which backend produced this result
    tokens_used: dict | None = None  # {input_tokens, output_tokens, model} for LLM
    duration_ms: int = 0
    failure_kind: str | None = None
    # D-1 增 2：底层把异常收成 failure_kind 时，平台给的 Retry-After 一并带出来。
    retry_after: int | None = None


class TranslationBackend(ABC):
    @abstractmethod
    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        """Translate a list of texts. context is optional LLM context summary."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    @property
    def max_chars_per_batch(self) -> int:
        return 2000

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def is_llm(self) -> bool:
        return False

    @property
    def enabled(self) -> bool:
        """Whether this backend is functional. Set False for stubs."""
        return True


class LLMBackend(TranslationBackend):
    """LLM 后端基类。

    子类只需重写三个东西：_api_url, _build_headers(), _call_api_parse()。
    translate_raw / translate_batch 由基类统一提供，零重复代码。

    加一个新平台（如 Kimi）只需 ~25 行。
    """

    @property
    def is_llm(self) -> bool:
        return True

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def max_input_tokens(self) -> int:
        """Return model context window from LiteLLM cache, file cache, or default 32K."""
        model = getattr(self, '_model', None) or ""
        try:
            from backend.model_cache import get_model_context
        except ImportError:
            return 32768
        return get_model_context(model)

    # ── 子类必须重写 ──────────────────────────────────────

    @property
    def _api_url(self) -> str:
        raise NotImplementedError

    def _build_headers(self) -> dict:
        raise NotImplementedError

    def _call_api_parse(self, response_data: dict) -> str:
        """从 API response JSON 中提取文本内容。各平台 JSON 结构不同。"""
        raise NotImplementedError

    # ── 子类可选重写 ──────────────────────────────────────

    def _request_body_options(self, max_tokens: int) -> dict:
        """返回当前平台需要补进请求体的参数；默认不添加任何内容。

        DeepSeek 用它关掉「思考模式」——新模型默认会先想一段再回答，翻字幕
        用不上，只是更慢更贵。默认返回空字典，对其余平台完全无影响。
        """
        return {}

    def _build_body(self, prompt: str, max_tokens: int) -> bytes:
        """构建请求体。默认 OpenAI 兼容格式。Claude / Gemini 等需重写。"""
        import json as _json
        body = {
            "model": getattr(self, "_model", "default"),
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        body.update(self._request_body_options(max_tokens))
        return _json.dumps(body).encode()

    def _call_api_raw(self, prompt: str, max_tokens: int = 8192) -> tuple:
        """Like _call_api but returns (parsed_text, raw_response_json_dict)."""
        import json as _json, urllib.request as _urllib
        body = self._build_body(prompt, max_tokens)
        req = _urllib.Request(
            self._api_url, data=body, headers=self._build_headers()
        )
        with _urllib.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read().decode())
            return self._parse_reply(data), data

    def _parse_reply(self, data: dict) -> str:
        """⑦.5 R2：平台回了 200 但答复里缺正文（Gemini 安全拦截、Claude 空 content）
        → 当作空答复（AI 回答有问题），不当成认不出的程序错误。"""
        import logging
        try:
            return self._call_api_parse(data)
        except (KeyError, IndexError, TypeError) as error:
            logging.getLogger("backend.translation").warning(
                "平台回了成功但答复缺正文 (%s): %r", self.name, error)
            return ""

    def _call_api(self, prompt: str, max_tokens: int = 8192) -> str:
        """发送 prompt 到 LLM API，返回原始文本。子类通常不需要重写。"""
        text, _ = self._call_api_raw(prompt, max_tokens)
        return text

    def _extract_tokens(self, response_data: dict) -> dict | None:
        """Extract token usage from API response. Override for non-OpenAI formats."""
        if response_data is None:
            return None
        usage = response_data.get("usage")
        if usage:
            return {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            }
        return None

    # ── 基类统一提供（子类不需要动）──────────────────────

    def translate_raw(self, prompt: str, max_tokens: int = 8192) -> TranslationResult:
        """直接发送 prompt，返回原始文本。不做任何切分。"""
        import time as _time, logging
        _tlog = logging.getLogger("backend.translation")
        t0 = _time.monotonic()
        try:
            content, raw_data = self._call_api_raw(prompt, max_tokens)
            dur_ms = int((_time.monotonic() - t0) * 1000)
            # 必须先判类型再取长度。`if content` 只挡得住 None/""，
            # 真值非字符串（123、True、object()）会在 len() 上抛 TypeError，
            # 被外层 except 归成 unknown —— 那就绕过了「连续两次空返回就停手」。
            content_len = len(content) if isinstance(content, str) else 0
            failure_kind = (
                "empty_translation"
                if not isinstance(content, str) or not content.strip()
                else None
            )
            _tlog.debug(
                "LLM translate_raw 完成 (%s): 类别=%s, %dms, 返回%d字, "
                "内容前300字: %s",
                self.name,
                failure_kind or "success",
                dur_ms,
                content_len,
                _debug_summary(content),
            )
            return TranslationResult(
                # `translated` 的契约是 list[str]。接口交回非字符串（None 等）时
                # 上面已判成 empty_translation，这里必须规范成空串再交出去——
                # 否则下游任何一个 .strip() 都会把整个翻译任务打崩。
                translated=[content if isinstance(content, str) else ""],
                backend_name=self.name,
                tokens_used=self._extract_tokens(raw_data),
                duration_ms=dur_ms,
                failure_kind=failure_kind,
            )
        except Exception as error:
            dur_ms = int((_time.monotonic() - t0) * 1000)
            failure_kind = classify_translation_failure(error)
            _tlog.debug(
                "LLM translate_raw 完成 (%s): 类别=%s, %dms",
                self.name,
                failure_kind,
                dur_ms,
            )
            _log_unknown_failure(_tlog, self.name, error, failure_kind)
            return TranslationResult(
                translated=[""],
                backend_name=self.name,
                duration_ms=dur_ms,
                failure_kind=failure_kind,
                retry_after=retry_after_seconds(error),
            )

    def translate_raw_json(self, prompt: str, system_prompt: str = None) -> str:
        """发送 prompt + system_prompt，开启 JSON mode，返回 LLM 原始文本。

        独立构建 request body（不经过 _build_body），直接发 HTTP 请求。
        返回原始字符串供 json.loads() 解析。
        """
        import json as _json, urllib.request as _urllib, logging
        _tlog = logging.getLogger("backend.translation")
        t0 = __import__('time').monotonic()

        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})

        body_data = {
            "model": self._model,
            "messages": msgs,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        body_data.update(self._request_body_options(8192))
        body = _json.dumps(body_data).encode()

        try:
            req = _urllib.Request(self._api_url, data=body, headers=self._build_headers())
            with _urllib.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read().decode())
            raw = data["choices"][0]["message"]["content"]
            dur_ms = int((__import__('time').monotonic() - t0) * 1000)
            _tlog.debug("LLM translate_raw_json 成功 (%s): %dms, 返回%d字",
                        self.name, dur_ms, len(raw) if raw else 0)
            return raw
        except Exception as e:
            dur_ms = int((__import__('time').monotonic() - t0) * 1000)
            _tlog.warning("LLM translate_raw_json 失败 (%s): %s (耗时 %dms)",
                          self.name, e, dur_ms)
            raise

    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        """拼接 texts → 一次 LLM 调用 → 按编号切分回填。所有 LLM 统一。"""
        import time as _time, logging
        _tlog = logging.getLogger("backend.translation")
        t0 = _time.monotonic()

        non_empty = [(i, t.strip()) for i, t in enumerate(texts) if t.strip()]
        results = _failed_results_like(texts)

        if not non_empty:
            return TranslationResult(
                translated=results, backend_name=self.name, duration_ms=0
            )

        ctx_prefix = f"语境: {context}\n\n" if context else ""
        joined = "\n".join(f"[{i}] {t}" for i, (_, t) in enumerate(non_empty))
        prompt = (
            f"{ctx_prefix}"
            f"你是专业视频字幕翻译。将以下字幕逐段翻译成{target_lang}。\n"
            f"{SHARED_TRANSLATION_RULES}\n"
            f"{LAYOUT_FRIENDLY_RULES}\n"
            f"仅按 [编号] 译文 格式输出。除编号和译文外，"
            f"不要输出解释、分隔符或 Markdown。\n"
            # P404：条数对不上是整批作废的头号原因，模型最爱把相邻两条并成一条。
            f"必须返回和输入完全相同的条数，不许合并、不许拆分，"
            f"哪怕两条本来是一句话。\n\n"
            f"{joined}"
        )

        raw = None
        raw_data = None
        failure_kind = None
        retry_after = None
        try:
            raw, raw_data = self._call_api_raw(prompt, max_tokens=4096)
            parts, failure_kind = _parse_numbered_batch_with_failure(
                raw, len(non_empty)
            )
            if parts is not None:
                failure_kind = _batch_target_language_failure_kind(
                    [text for _, text in non_empty], parts, target_lang
                )
                if failure_kind is None:
                    for target_idx, part in enumerate(parts):
                        original_idx = non_empty[target_idx][0]
                        results[original_idx] = part
        except Exception as error:
            failure_kind = classify_translation_failure(error)
            retry_after = retry_after_seconds(error)
            _log_unknown_failure(_tlog, self.name, error, failure_kind)

        _tlog.debug(
            "LLM translate_batch 完成 (%s): 类别=%s, %dms, 原始返回前300字: %s",
            self.name,
            failure_kind or "success",
            int((_time.monotonic() - t0) * 1000),
            _debug_summary(raw),
        )

        return TranslationResult(
            translated=results,
            backend_name=self.name,
            tokens_used=self._extract_tokens(raw_data) if raw_data else None,
            duration_ms=int((_time.monotonic() - t0) * 1000),
            failure_kind=failure_kind,
            retry_after=retry_after,
        )
