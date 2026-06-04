"""GLM backend — 智谱 AI (ZhipuAI) OpenAI-compatible API."""
from .base import LLMBackend


class GLMBackend(LLMBackend):
    def __init__(self, api_key: str, model: str = "glm-4-flash"):
        self._key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return f"GLM({self._model})"

    @property
    def _api_url(self) -> str:
        return "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _call_api_parse(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]

    def understand_context(self, full_text: str) -> str:
        prompt = (
            "阅读以下完整视频转录文本。理解主题、人物、语气和关键术语。不要翻译。"
            "用中文写一段简要的语境概要。\n\n"
            f"{full_text}"
        )
        try:
            return self._call_api(prompt, max_tokens=1024)
        except Exception:
            return ""
