"""Claude backend — Anthropic API."""
import json
from .base import LLMBackend


class ClaudeBackend(LLMBackend):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return f"Claude({self._model})"

    @property
    def _api_url(self) -> str:
        return "https://api.anthropic.com/v1/messages"

    def _build_headers(self) -> dict:
        return {
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _build_body(self, prompt: str, max_tokens: int) -> bytes:
        return json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

    def _call_api_parse(self, data: dict) -> str:
        return data["content"][0]["text"]

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
