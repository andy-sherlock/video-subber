"""Baidu Translate backend — \n batch API with up to 2000 chars/batch."""
import os, json, time, hashlib, random, logging, urllib.request, urllib.parse
from .base import TranslationBackend, TranslationResult

_log = logging.getLogger("baidu")


def _get_baidu_creds():
    """Get Baidu API credentials from env vars or user config."""
    return os.environ.get("BAIDU_APP_ID", ""), os.environ.get("BAIDU_SECRET", "")


class BaiduBackend(TranslationBackend):
    @property
    def name(self) -> str:
        return "百度"

    @property
    def max_chars_per_batch(self) -> int:
        return 2000

    def translate_batch(self, texts: list[str], source_lang: str = "auto",
                        target_lang: str = "zh", context: str = "") -> TranslationResult:
        t0 = time.time()
        texts = [t.strip() for t in texts]
        query = "\n".join(texts)
        if not query.strip():
            return TranslationResult(
                translated=["" for _ in texts], backend_name=self.name,
                duration_ms=0,
            )

        app_id, secret = _get_baidu_creds()
        if not app_id or not secret:
            _log.warning("百度翻译未配置，跳过")
            return TranslationResult(
                translated=["[翻译失败]"] * len(texts),
                backend_name=self.name,
                duration_ms=int((time.time() - t0) * 1000),
            )

        salt = str(random.randint(10000, 99999))
        sign_raw = app_id + query + salt + secret
        sign = hashlib.md5(sign_raw.encode()).hexdigest()
        params = {
            "q": query, "from": "auto", "to": "zh",
            "appid": app_id, "salt": salt, "sign": sign,
        }
        url = (
            "https://fanyi-api.baidu.com/api/trans/vip/translate?"
            + urllib.parse.urlencode(params)
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            if "trans_result" in data:
                results = [item["dst"] for item in data["trans_result"]]
                while len(results) < len(texts):
                    results.append("[翻译失败]")
                return TranslationResult(
                    translated=results[:len(texts)], backend_name=self.name,
                    duration_ms=int((time.time() - t0) * 1000),
                )
            raise RuntimeError(f"百度: {data}")
        except Exception as e:
            _log.warning("百度翻译失败: %s", e)
            return TranslationResult(
                translated=["[翻译失败]"] * len(texts),
                backend_name=self.name,
                duration_ms=int((time.time() - t0) * 1000),
            )
