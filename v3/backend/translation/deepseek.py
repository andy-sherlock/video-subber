"""DeepSeek backend — OpenAI-compatible API."""
from .base import LLMBackend


class DeepSeekBackend(LLMBackend):
    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        self._key = api_key
        requested_model = str(model or "").strip()
        self._model = (
            "deepseek-v4-flash"
            if requested_model in {"", "deepseek-chat"}
            else requested_model
        )

    @property
    def name(self) -> str:
        return f"DeepSeek({self._model})"

    @property
    def _api_url(self) -> str:
        return "https://api.deepseek.com/v1/chat/completions"

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _request_body_options(self, max_tokens: int) -> dict:
        return {
            "thinking": {"type": "disabled"},
            "max_tokens": max_tokens,
        }

    def _call_api_parse(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]

    def understand_context(self, full_text: str) -> str:
        prompt = (
            "Read the following complete video transcript. Understand the topic, "
            "characters, tone, and key terminology. Do NOT translate. "
            "Write a brief context summary (in Chinese).\n\n"
            f"{full_text}"
        )
        try:
            return self._call_api(prompt, max_tokens=1024)
        except Exception:
            return ""
