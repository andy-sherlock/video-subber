"""Translation: batch via Baidu (\n = native multi-line) ∥ Google fallback."""
import json, time, hashlib, random, urllib.request, urllib.parse, threading, logging

_log = logging.getLogger("translator")

BAIDU_APP_ID = "20260520002616499"
BAIDU_SECRET = "NGw5pcrCkfDqe6gpygvT"
BATCH_CHAR_LIMIT = 2000


def _baidu_batch(texts: list[str]) -> list[str]:
    """Baidu batch translate: join with \n, parse all trans_result items."""
    query = "\n".join(texts)
    salt = str(random.randint(10000, 99999))
    sign_raw = BAIDU_APP_ID + query + salt + BAIDU_SECRET
    sign = hashlib.md5(sign_raw.encode()).hexdigest()
    params = {
        "q": query, "from": "auto", "to": "zh",
        "appid": BAIDU_APP_ID, "salt": salt, "sign": sign,
    }
    url = "https://fanyi-api.baidu.com/api/trans/vip/translate?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read().decode())
    if "trans_result" in data:
        return [item["dst"] for item in data["trans_result"]]
    raise RuntimeError(f"百度: {data}")


def _google_fallback(texts: list[str]) -> list[str]:
    """Google per-segment fallback (no batch support, single segment only)."""
    text = texts[0]
    q = urllib.parse.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=8)
    data = json.loads(resp.read().decode())
    return ["".join([s[0] for s in data[0] if s[0]])]


def _dual_batch(texts: list[str]) -> list[str]:
    """Google ∥ Baidu batch; first to return valid result wins."""
    result = [None]
    lock = threading.Lock()

    def _try_baidu():
        try:
            r = _baidu_batch(texts)
            if len(r) == len(texts):
                with lock:
                    if result[0] is None:
                        result[0] = r
        except Exception:
            pass

    def _try_google():
        try:
            r = []
            for t in texts:
                r.extend(_google_fallback([t]))
            if len(r) == len(texts):
                with lock:
                    if result[0] is None:
                        result[0] = r
        except Exception:
            pass

    t1 = threading.Thread(target=_try_baidu, daemon=True)
    t2 = threading.Thread(target=_try_google, daemon=True)
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)

    if result[0] is not None:
        return result[0]
    raise RuntimeError("Google ∥ Baidu 均失败")


def _split_into_batches(texts: list[str], limit: int = BATCH_CHAR_LIMIT) -> list[list[str]]:
    """Split texts into batches, each batch's joined text ≤ limit chars."""
    batches = []
    cur = []
    cur_len = 0
    for t in texts:
        add = len(t) + (1 if cur else 0)  # +1 for \n
        if cur and cur_len + add > limit:
            batches.append(cur)
            cur = []
            cur_len = 0
            add = len(t)
        cur.append(t)
        cur_len += add
    if cur:
        batches.append(cur)
    return batches


def translate_segments(segments: list, backend: str = "baidu", api_key: str = None) -> list:
    """Batch translate via Baidu \n native multi-line ∥ Google, first to return wins."""
    texts = [s[2].strip() if len(s) == 3 else s.text.strip() for s in segments]
    total = len(texts)

    batches = _split_into_batches(texts)
    _log.info("翻译开始: %d 句 → %d 批 (每批上限 %d 字符)", total, len(batches), BATCH_CHAR_LIMIT)
    print(f"       批量翻译 (Google ∥ Baidu): {total} 句 → {len(batches)} 批", flush=True)

    all_results = []
    failed = 0
    for i, batch in enumerate(batches):
        _log.debug("批次 %d/%d: %d 句, 字符数=%d",
                   i + 1, len(batches), len(batch), len("\n".join(batch)))
        ok = False
        for attempt in range(3):
            try:
                r = _dual_batch(batch)
                while len(r) < len(batch):
                    r.append("[翻译失败]")
                r = r[:len(batch)]
                all_results.extend(r)
                n_fail = sum(1 for x in r if x == "[翻译失败]")
                failed += n_fail
                ok = True
                print(f"       批次 {i+1}/{len(batches)} ✓ "
                      f"({len(batch) - n_fail}/{len(batch)} 成功)", flush=True)
                _log.debug("批次 %d/%d: %d/%d 成功 (第 %d 次尝试)",
                           i + 1, len(batches), len(batch) - n_fail, len(batch), attempt + 1)
                break
            except Exception as e:
                _log.debug("批次 %d/%d 第 %d 次尝试失败: %s",
                           i + 1, len(batches), attempt + 1, str(e)[:120])
                if attempt == 2:
                    all_results.extend(["[翻译失败]"] * len(batch))
                    failed += len(batch)
                    print(f"       批次 {i+1}/{len(batches)} ✗ 全部失败", flush=True)
                else:
                    time.sleep(1.5 * (attempt + 1))
        if i < len(batches) - 1:
            time.sleep(1)  # Baidu QPS limit

    _log.info("翻译结束: %d/%d 成功, %d 失败", total - failed, total, failed)
    print(f"       翻译: {total - failed}/{total} 成功, {failed} 失败", flush=True)
    return all_results
