"""TranslationBackend ABC — all translation engines follow this contract."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TranslationResult:
    translated: list[str]       # Same length as input, failed items = "[翻译失败]"
    backend_name: str           # Which backend produced this result
    tokens_used: dict | None = None  # {input_tokens, output_tokens, model} for LLM
    duration_ms: int = 0


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
        """Return model context window from LiteLLM cache, or default 32K."""
        model = getattr(self, '_model', None) or ""
        try:
            from backend.model_cache import _MODEL_CONTEXT_CACHE
        except ImportError:
            return 32768
        if not _MODEL_CONTEXT_CACHE:
            return 32768
        # Exact match first
        if model in _MODEL_CONTEXT_CACHE:
            val = _MODEL_CONTEXT_CACHE[model]
            if val > 0:
                return val
        # Suffix match: e.g. "gpt-4o" matches "openai/gpt-4o"
        for key, val in _MODEL_CONTEXT_CACHE.items():
            if val > 0 and key.endswith("/" + model):
                return val
        return 32768

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

    def _build_body(self, prompt: str, max_tokens: int) -> bytes:
        """构建请求体。默认 OpenAI 兼容格式。Claude / Gemini 等需重写。"""
        import json as _json
        return _json.dumps({
            "model": getattr(self, "_model", "default"),
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

    def _call_api_raw(self, prompt: str, max_tokens: int = 8192) -> tuple:
        """Like _call_api but returns (parsed_text, raw_response_json_dict)."""
        import json as _json, urllib.request as _urllib
        body = self._build_body(prompt, max_tokens)
        req = _urllib.Request(
            self._api_url, data=body, headers=self._build_headers()
        )
        with _urllib.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read().decode())
            return self._call_api_parse(data), data

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
            content_len = len(content) if content else 0
            _tlog.debug(
                "LLM translate_raw 成功 (%s): %dms, 返回%d字, 内容前300字: %s",
                self.name, dur_ms, content_len,
                content[:300] if content else "(空)"
            )
            return TranslationResult(
                translated=[content],
                backend_name=self.name,
                tokens_used=self._extract_tokens(raw_data),
                duration_ms=dur_ms,
            )
        except Exception as e:
            dur_ms = int((_time.monotonic() - t0) * 1000)
            _tlog.warning(
                "LLM translate_raw 调用失败 (%s): %s (耗时 %dms)",
                self.name, e, dur_ms
            )
            return TranslationResult(
                translated=[""],
                backend_name=self.name,
                duration_ms=dur_ms,
            )

    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        """拼接 texts → 一次 LLM 调用 → 按 '---' 切分回填。所有 LLM 统一。"""
        import time as _time
        t0 = _time.monotonic()
        SEP = "\n\n---\n\n"

        non_empty = [(i, t.strip()) for i, t in enumerate(texts) if t.strip()]
        results = ["" if not t.strip() else "[翻译失败]" for t in texts]

        if not non_empty:
            return TranslationResult(
                translated=results, backend_name=self.name, duration_ms=0
            )

        ctx_prefix = f"语境: {context}\n\n" if context else ""
        joined = SEP.join(t for _, t in non_empty)
        prompt = (
            f"{ctx_prefix}"
            f"你是专业视频字幕翻译。将以下字幕逐段翻译成{target_lang}，"
            f"保持口语自然，每段译文用 '---' 分隔，只输出译文不加解释。\n\n"
            f"{joined}"
        )

        try:
            raw, raw_data = self._call_api_raw(prompt, max_tokens=4096)
            parts = [p.strip() for p in raw.split(SEP)]
            for target_idx, part in enumerate(parts):
                if target_idx < len(non_empty):
                    original_idx = non_empty[target_idx][0]
                    results[original_idx] = part if part else "[翻译失败]"
        except Exception:
            raw_data = None

        return TranslationResult(
            translated=results,
            backend_name=self.name,
            tokens_used=self._extract_tokens(raw_data) if raw_data else None,
            duration_ms=int((_time.monotonic() - t0) * 1000),
        )
