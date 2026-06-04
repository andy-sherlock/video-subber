"""Translation: Google (free) + Baidu (API key) with batch sending."""
import json, time, re, hashlib, random, urllib.request, urllib.parse

GOOGLE = "google"
BAIDU = "baidu"
SEP = "【】"  # delimiter: Chinese brackets, Baidu preserves them
# Per-platform character limits per API call
PLATFORM_LIMITS = {"google": 2000, "baidu": 2000, "deepl": 5000, "youdao": 2000, "bing": 10000}
SENTENCE_CUT = re.compile(r'[。！？.!?]')
COMMA_CUT = re.compile(r'[，,;；、]')
BAIDU_APP_ID = "20260520002616499"
BAIDU_SECRET = "NGw5pcrCkfDqe6gpygvT"


def _translate_google(text: str, timeout: int = 8) -> str:
    q = urllib.parse.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    data = json.loads(resp.read().decode())
    return "".join([s[0] for s in data[0] if s[0]])


def _translate_baidu(text: str, timeout: int = 10) -> str:
    """Baidu Translate API — requires APP ID + Key."""
    salt = str(random.randint(10000, 99999))
    sign_raw = BAIDU_APP_ID + text + salt + BAIDU_SECRET
    sign = hashlib.md5(sign_raw.encode()).hexdigest()
    params = {
        "q": text, "from": "auto", "to": "zh",
        "appid": BAIDU_APP_ID, "salt": salt, "sign": sign,
    }
    url = "https://fanyi-api.baidu.com/api/trans/vip/translate?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    data = json.loads(resp.read().decode())
    if "trans_result" in data:
        return data["trans_result"][0]["dst"]
    raise RuntimeError(f"百度翻译失败: {data}")


def _cut_at_boundary(joined: str, limit: int) -> int:
    """Find best cut position in joined text: prefer sentence end, then comma, not mid-word."""
    if len(joined) <= limit:
        return len(joined)
    chunk = joined[:limit]
    # Try sentence end
    m = list(SENTENCE_CUT.finditer(chunk))
    if m:
        return m[-1].end()
    # Try comma
    m = list(COMMA_CUT.finditer(chunk))
    if m:
        return m[-1].end()
    # Fallback: cut at last space or just before limit
    last_space = chunk.rfind(" ")
    return last_space if last_space > 0 else limit


def _batch_translate(texts: list, translate_fn, max_chars: int = 2000) -> list:
    """Stitch texts → cut at sentence boundaries → translate batches → split back."""
    joined = SEP.join(texts)
    batches = []
    pos = 0
    while pos < len(joined):
        chunk_end = min(pos + max_chars, len(joined))
        if chunk_end >= len(joined):
            cut = len(joined)
        else:
            cut = _cut_at_boundary(joined, chunk_end)
        if cut <= pos:
            cut = min(pos + max_chars, len(joined))
        batch_text = joined[pos:cut].strip().rstrip(SEP)
        if batch_text:
            batches.append(batch_text)
        pos = cut
        while pos < len(joined) and joined[pos] in "\n- ":
            pos += 1

    print(f"        共 {len(batches)} 批次", flush=True)

    all_results = []
    for i, batch in enumerate(batches):
        print(f"        批次 {i+1}/{len(batches)}…", end="", flush=True)
        for attempt in range(3):
            try:
                zh = translate_fn(batch)
                all_results.append(zh)
                print(" ✓", flush=True)
                break
            except Exception as e:
                if attempt == 2:
                    all_results.append(None)
                    print(f" ✗ {e}", flush=True)
                else:
                    time.sleep(1.5 * (attempt + 1))
        time.sleep(0.2)

    # Split results back by SEP
    final = []
    for r in all_results:
        parts = [x.strip() for x in r.split(SEP)] if r else []
        final.extend(parts)

    while len(final) < len(texts):
        final.append("[翻译失败]")
    return final[:len(texts)]


def _try_backend(texts: list, backend: str, translate_fn, limit: int, results_holder: list, flag: list):
    """Run translation in thread. Sets flag[0]=True on success."""
    try:
        r = _batch_translate(texts, translate_fn, max_chars=limit)
        if not flag[0]:
            results_holder[:] = r
            flag[0] = True
    except Exception as e:
        if not flag[0]:
            results_holder.append(e)


def translate_segments(segments: list, backend: str = "baidu", api_key: str = None) -> list:
    """Translate segments. Google ∥ Baidu — first wins."""
    import threading
    texts = [s[2].strip() if len(s) == 3 else s.text.strip() for s in segments]
    total = len(texts)
    print(f"       并行翻译 (Google ∥ Baidu): {total} 句", flush=True)

    g_limit = PLATFORM_LIMITS.get("google", 2000)
    b_limit = PLATFORM_LIMITS.get("baidu", 2000)

    results = []
    flag = [False]
    t1 = threading.Thread(target=_try_backend, args=(texts, "google", _translate_google, g_limit, results, flag), daemon=True)
    t2 = threading.Thread(target=_try_backend, args=(texts, "baidu", _translate_baidu, b_limit, results, flag), daemon=True)
    t1.start(); t2.start()
    t1.join(timeout=120); t2.join(timeout=120)

    if isinstance(results, list) and results and not isinstance(results[0], Exception):
        errors = sum(1 for r in results if not r or r == "[翻译失败]")
        print(f"       翻译: {total-errors}/{total} 成功, {errors} 失败", flush=True)
        return results

    # Both failed
    if results and isinstance(results[0], Exception):
        raise results[0]
    raise RuntimeError("所有翻译后端均不可用")
