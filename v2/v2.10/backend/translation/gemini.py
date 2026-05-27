"""Gemini backend — Google AI API (non-OpenAI format, needs _call_api override)."""
import json, urllib.request, time
from .base import LLMBackend, TranslationResult


class GeminiBackend(LLMBackend):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self._key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return f"Gemini({self._model})"

    @property
    def _api_url(self) -> str:
        # Not used — _call_api overridden for Gemini's unique URL format
        return ""

    def _build_headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _build_body(self, prompt: str, max_tokens: int) -> bytes:
        return json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }).encode()

    def _call_api(self, prompt: str, max_tokens: int = 8192) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self._model}:generateContent?key={self._key}"
        )
        body = self._build_body(prompt, max_tokens)
        req = urllib.request.Request(url, data=body, headers=self._build_headers())
        with urllib.request.urlopen(req, timeout=120) as resp:
            return self._call_api_parse(json.loads(resp.read().decode()))

    def _call_api_parse(self, data: dict) -> str:
        return data["candidates"][0]["content"]["parts"][0]["text"]

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
