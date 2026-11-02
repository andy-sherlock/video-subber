"""Youdao Translate backend — reverse-engineered aidemo.youdao.com API."""
import json, re, time, logging, urllib.request, urllib.parse
from .base import TranslationBackend, TranslationResult, classify_translation_failure

_log = logging.getLogger("youdao")

_API_URL = "https://aidemo.youdao.com/trans"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _to_youdao_lang(lang: str) -> str:
    m = {"zh": "zh-CHS", "zh-cn": "zh-CHS", "zh-tw": "zh-CHT", "ja": "ja", "en": "en"}
    return m.get(lang.lower(), lang)


class YoudaoBackend(TranslationBackend):
    @property
    def name(self) -> str:
        return "有道"

    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        t0 = time.time()
        SEPARATOR = " ||| "

        non_empty = [(i, t.strip()) for i, t in enumerate(texts) if t.strip()]
        results = ["" if not t.strip() else "[翻译失败]" for t in texts]
        failure_kind = None

        if not non_empty:
            return TranslationResult(translated=results, backend_name=self.name, duration_ms=0)

        joined = SEPARATOR.join(t for _, t in non_empty)
        to_code = _to_youdao_lang(target_lang)

        try:
            body = urllib.parse.urlencode({
                "q": joined,
                "from": "auto",
                "to": to_code,
            }).encode()
            req = urllib.request.Request(_API_URL, data=body, headers={
                "User-Agent": _UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://ai.youdao.com/product-fanyi-text.s",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())

            if data.get("errorCode") != "0":
                raise RuntimeError(f"Youdao error: {data.get('errorCode')}")

            translated_full = data["translation"][0]
            parts = [p.strip() for p in translated_full.split(SEPARATOR)]
            if len(parts) != len(non_empty):
                parts = [p.strip() for p in re.split(r'\s*\|\|\|\s*', translated_full)]

            if len(parts) == len(non_empty):
                for target_idx, part in enumerate(parts):
                    original_idx = non_empty[target_idx][0]
                    results[original_idx] = part if part else "[翻译失败]"
            else:
                _log.warning("有道翻译分段数不匹配: got %d, expected %d", len(parts), len(non_empty))
            if any(results[i] == "[翻译失败]" for i, _ in non_empty):
                failure_kind = classify_translation_failure(empty_translation=True)
        except Exception as error:
            _log.warning("有道翻译失败: %s", error)
            failure_kind = classify_translation_failure(error)

        return TranslationResult(
            translated=results, backend_name=self.name,
            duration_ms=int((time.time() - t0) * 1000),
            failure_kind=failure_kind,
        )
