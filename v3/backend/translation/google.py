"""Google Translate backend — free web API, per-segment only."""
import json, re, time, urllib.request, urllib.parse
from .base import TranslationBackend, TranslationResult, classify_translation_failure


class GoogleBackend(TranslationBackend):
    @property
    def name(self) -> str:
        return "Google"

    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        t0 = time.time()
        SEPARATOR = " ||| "

        # 记录非空原文的原始索引，用于翻译完成后按位置回填
        non_empty = [(i, t.strip()) for i, t in enumerate(texts) if t.strip()]
        results = ["" if not t.strip() else "[翻译失败]" for t in texts]
        failure_kind = None

        if not non_empty:
            return TranslationResult(
                translated=[""] * len(texts), backend_name=self.name, duration_ms=0
            )

        joined = SEPARATOR.join(t for _, t in non_empty)
        q = urllib.parse.quote(joined)
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={source_lang}&tl={target_lang}&dt=t&q={q}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            translated_full = "".join([s[0] for s in data[0] if s[0]])
            parts = [p.strip() for p in translated_full.split(SEPARATOR)]
            if len(parts) != len(non_empty):
                parts = [p.strip() for p in re.split(r'\s*\|\|\|\s*', translated_full)]
            if len(parts) != len(non_empty):
                return TranslationResult(
                    translated=["[翻译失败]"] * len(texts), backend_name=self.name,
                    duration_ms=int((time.time() - t0) * 1000),
                    failure_kind=classify_translation_failure(empty_translation=True),
                )
            for target_idx, part in enumerate(parts):
                original_idx = non_empty[target_idx][0]
                results[original_idx] = part if part else "[翻译失败]"
                if not part:
                    failure_kind = classify_translation_failure(empty_translation=True)
        except Exception as error:
            failure_kind = classify_translation_failure(error)

        return TranslationResult(
            translated=results, backend_name=self.name,
            duration_ms=int((time.time() - t0) * 1000),
            failure_kind=failure_kind,
        )
