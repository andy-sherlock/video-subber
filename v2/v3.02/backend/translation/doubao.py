"""Doubao (豆包) backend — ByteDance Ark API (OpenAI-compatible)."""
from .base import LLMBackend


class DoubaoBackend(LLMBackend):
    def __init__(self, api_key: str, model: str = "doubao-pro-32k"):
        self._key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return f"豆包({self._model})"

    @property
    def _api_url(self) -> str:
        return "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
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
