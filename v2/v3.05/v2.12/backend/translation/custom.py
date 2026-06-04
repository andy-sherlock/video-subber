"""Custom backend — 任意 OpenAI 兼容端点。用户在前端填入 URL + 模型名即可接入。"""
from .base import LLMBackend


class CustomOpenAIBackend(LLMBackend):
    def __init__(self, api_key: str, api_endpoint: str, model: str = "gpt-4o"):
        self._key = api_key
        self._api_endpoint = api_endpoint.strip()  # 用户输入的完整请求 URL
        self._model = model

    @property
    def name(self) -> str:
        return f"自定义({self._model})"

    @property
    def _api_url(self) -> str:
        # 用户给什么就用什么，不做字符串体操
        return self._api_endpoint

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _call_api_parse(self, data: dict) -> str:
        # 标准 OpenAI 响应格式
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
