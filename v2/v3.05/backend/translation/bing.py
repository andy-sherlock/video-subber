"""Bing Translate backend — reverse-engineered Edge browser translation API."""
import json, time, logging, urllib.request
from .base import TranslationBackend, TranslationResult

_log = logging.getLogger("bing")

_AUTH_URL = "https://edge.microsoft.com/translate/auth"
_TRANSLATE_URL = "https://api-edge.cognitive.microsofttranslator.com/translate"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

_token_cache: dict = {"token": "", "expires": 0.0}


def _get_auth_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]
    req = urllib.request.Request(_AUTH_URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = resp.read().decode().strip()
    _token_cache["token"] = token
    _token_cache["expires"] = now + 480
    return token


def _to_ms_lang(lang: str) -> str:
    m = {"zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-tw": "zh-Hant", "cht": "zh-Hant"}
    return m.get(lang.lower(), lang)


class BingBackend(TranslationBackend):
    @property
    def name(self) -> str:
        return "Bing"

    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        t0 = time.time()
        non_empty = [(i, t.strip()) for i, t in enumerate(texts) if t.strip()]
        results = ["" if not t.strip() else "[翻译失败]" for t in texts]

        if not non_empty:
            return TranslationResult(translated=results, backend_name=self.name, duration_ms=0)

        try:
            token = _get_auth_token()
            to_code = _to_ms_lang(target_lang)
            url = f"{_TRANSLATE_URL}?from=&to={to_code}&api-version=3.0"
            body = json.dumps([{"Text": t} for _, t in non_empty]).encode()
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "User-Agent": _UA,
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())

            for idx, item in enumerate(data):
                if idx < len(non_empty):
                    translated_text = item["translations"][0]["text"].strip()
                    original_idx = non_empty[idx][0]
                    results[original_idx] = translated_text if translated_text else "[翻译失败]"
        except Exception as e:
            _log.warning("Bing 翻译失败: %s", e)

        return TranslationResult(
            translated=results, backend_name=self.name,
            duration_ms=int((time.time() - t0) * 1000),
        )
