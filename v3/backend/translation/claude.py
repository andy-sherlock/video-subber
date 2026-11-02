"""Claude backend — Anthropic API."""
import json
import re
from .base import LLMBackend

# P519：断句/润色要 JSON。Claude 没有通用的 JSON 开关（预填在新模型上被禁用、
# 结构化输出不支持 Sonnet 4.6），只能在正文里要求；它偶尔仍会包一层代码块。
_JSON_INSTRUCTION = "只输出一个 JSON 对象，不要输出任何其他文字。"
_CODE_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.S)


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
        # 新模型思考默认开启，第一块是思考内容；取第一块正文（P519）
        # 没有正文块 → IndexError，由 _parse_reply 当作空回答（⑦.5 R2）
        return [block["text"] for block in data["content"] if block.get("type") == "text"][0]

    def translate_raw_json(self, prompt: str, system_prompt: str = None) -> str:
        """P519：断句/润色走 Claude 自己的格式。背景说明并进正文；不带 temperature
        （Opus 4.7 以后、Sonnet 5 / 5.5 收到会拒绝请求）。"""
        import urllib.request
        text = "\n\n".join(part for part in (system_prompt, prompt, _JSON_INSTRUCTION) if part)
        body = json.dumps({
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": text}],
        }).encode()
        req = urllib.request.Request(self._api_url, data=body, headers=self._build_headers())
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = self._parse_reply(json.loads(resp.read().decode())).strip()
        fenced = _CODE_FENCE.match(raw)
        return fenced.group(1).strip() if fenced else raw

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
