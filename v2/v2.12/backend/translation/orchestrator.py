"""Speed mode (parallel race) / Quality mode (serial fallback) orchestration."""
import threading, time, re
import os as _os
import json as _json
from .base import TranslationBackend, TranslationResult
from .google import GoogleBackend
from .baidu import BaiduBackend

_ALL_FREE = {
    "google": GoogleBackend(),
    "baidu": BaiduBackend(),
}
FREE_BACKENDS: dict[str, TranslationBackend] = {
    k: v for k, v in _ALL_FREE.items() if v.enabled
}

# SVIP tier platform-provided LLM keys (injected automatically for svip users).
# Priority: VSUB_PLATFORM_KEYS env var (dev) → remote GitHub fetch (production).

def _get_platform_keys() -> list[dict]:
    """获取平台 Key。环境变量优先（开发调试），否则从主进程远程拉取缓存获取。"""
    raw = _os.environ.get("VSUB_PLATFORM_KEYS", "")
    if raw:
        try:
            return _json.loads(raw)
        except Exception:
            pass
    try:
        from backend.main import get_platform_keys
        return get_platform_keys()
    except ImportError:
        return []


def _split_into_batches(texts: list[str], limit: int = 2000) -> list[list[str]]:
    batches = []
    cur = []
    cur_len = 0
    for t in texts:
        add = len(t) + (1 if cur else 0)
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


def _merge_tokens(base_tokens: dict | None, new_tokens: dict | None) -> dict | None:
    """Safely merge two token-usage dicts. Returns None only if both are empty/falsy."""
    if not new_tokens or not isinstance(new_tokens, dict):
        return base_tokens if base_tokens else None
    base = dict(base_tokens) if base_tokens else {}
    for key, val in new_tokens.items():
        if isinstance(val, (int, float)):
            base[key] = base.get(key, 0) + val
    return base


def _build_llm_backends(api_keys: list[dict]) -> list[TranslationBackend]:
    """Dynamically instantiate LLM backends from user-provided API keys."""
    backends = []
    if not api_keys:
        return backends
    for entry in api_keys:
        platform = entry.get("platform", "").lower()
        key = entry.get("key", "")
        if not key:
            continue
        try:
            if platform == "claude":
                from .claude import ClaudeBackend
                backends.append(ClaudeBackend(api_key=key))
            elif platform == "gpt":
                from .gpt import GPTBackend
                backends.append(GPTBackend(api_key=key))
            elif platform == "gemini":
                from .gemini import GeminiBackend
                backends.append(GeminiBackend(api_key=key))
            elif platform == "deepseek":
                from .deepseek import DeepSeekBackend
                backends.append(DeepSeekBackend(api_key=key))
            elif platform == "doubao" or platform == "豆包":
                from .doubao import DoubaoBackend
                backends.append(DoubaoBackend(api_key=key))
            elif platform == "glm":
                from .glm import GLMBackend
                backends.append(GLMBackend(api_key=key))
            elif platform == "custom":
                from .custom import CustomOpenAIBackend
                api_endpoint = entry.get("api_endpoint", "")
                model = entry.get("model", "gpt-4o")
                if api_endpoint:
                    backends.append(CustomOpenAIBackend(
                        api_key=key, api_endpoint=api_endpoint, model=model
                    ))
        except ImportError:
            pass
        except Exception:
            pass
    return backends


def _run_speed_mode_batch(
    batch: list[str],
    backends: list[TranslationBackend],
    context: str = "",
) -> tuple[list[str], TranslationBackend | None, dict | None]:
    """Fire all backends in parallel. Best result wins (most valid entries).
    First fully-valid result short-circuits remaining backends.

    Returns (translated_texts, winning_backend_or_None, tokens_used_or_None).
    """
    best_container = [None]  # (translated, backend, tokens, valid_count)
    best_lock = threading.Lock()
    done_event = threading.Event()

    def _try(backend: TranslationBackend):
        if done_event.is_set():
            return
        try:
            r = backend.translate_batch(batch, context=context)
            if done_event.is_set() or len(r.translated) != len(batch):
                return
            valid_count = sum(
                1 for x in r.translated if x not in (None, "", "[翻译失败]")
            )
            with best_lock:
                if best_container[0] is None or valid_count > best_container[0][3]:
                    best_container[0] = (r.translated, backend, r.tokens_used, valid_count)
            if valid_count == len(batch):
                done_event.set()
        except Exception:
            pass

    threads = []
    for b in backends:
        t = threading.Thread(target=_try, args=(b,), daemon=True)
        t.start()
        threads.append(t)

    # LLM backends can take 60-120s for large batches. Use a 120s deadline
    # instead of a flat 30s timeout. Stop early if a fully-valid result arrives.
    deadline = time.time() + 120
    for t in threads:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        t.join(timeout=remaining)
        if done_event.is_set():
            break

    if best_container[0] is not None:
        translated, backend, tokens, _ = best_container[0]
        while len(translated) < len(batch):
            translated.append("[翻译失败]")
        return translated[:len(batch)], backend, tokens
    return ["[翻译失败]"] * len(batch), None, None


def _run_quality_mode_batch(
    batch: list[str],
    backends: list[TranslationBackend],
    context: str = "",
) -> tuple[list[str], TranslationBackend | None, list[int], dict | None]:
    """Try backends in priority order. Failed segments fall through to next backend.

    Returns (results, primary_backend_or_None, failed_indices, tokens_used_or_None).
    """
    n = len(batch)
    results = ["[翻译失败]"] * n
    used_backend = None
    failed_indices = set(range(n))
    batch_tokens = None

    for backend in backends:
        if not failed_indices:
            break
        try:
            r = backend.translate_batch(batch, context=context)
            for i in list(failed_indices):
                if i < len(r.translated) and r.translated[i] not in ("", "[翻译失败]", None):
                    results[i] = r.translated[i]
                    failed_indices.discard(i)
            if used_backend is None:
                used_backend = backend
            batch_tokens = _merge_tokens(batch_tokens, r.tokens_used)
        except Exception:
            continue

    return results, used_backend, list(failed_indices), batch_tokens


_SUBTITLE_PROMPT = (
    "你是专业视频字幕翻译。翻译规则：\n"
    "- 保持口语自然，不书面化\n"
    "- 每行对应一条字幕，独立翻译，不合并不拆分行\n"
    "- 专有名词、人名保留原文或使用通用译法\n"
    "- 只输出译文，不加任何解释或注释\n\n"
)
_CHUNK_SIZE = 1200   # segments per chunk；~1200 * 10 tokens ≈ 12000 < 16384 max
_CONTEXT_CARRY = 5   # 上一块最后 N 条译文传入下一块，维持上下文衔接


def _translate_whole_with_markers(
    texts: list[str],
    llm: TranslationBackend,
    progress_callback=None,
) -> tuple[list[str], dict | None]:
    """Translate all texts with [N] markers. Adds subtitle system prompt.
    Auto-chunks when segment count exceeds _CHUNK_SIZE; passes tail context
    between chunks to maintain continuity.
    """
    if progress_callback:
        progress_callback(0, "LLM 整篇上下文翻译中…")

    translated = ["[翻译失败]"] * len(texts)
    non_empty = []
    for i, t in enumerate(texts):
        if not t.strip():
            translated[i] = ""
        else:
            non_empty.append((i, t.strip()))

    if not non_empty:
        return translated, None

    chunks = [non_empty[s:s + _CHUNK_SIZE] for s in range(0, len(non_empty), _CHUNK_SIZE)]
    total_chunks = len(chunks)
    token_usage_total = None
    prev_tail = ""  # last _CONTEXT_CARRY translated lines from previous chunk
    pattern = re.compile(r"\[(\d+)\]\s*(.+)")

    for chunk_idx, chunk in enumerate(chunks):
        context_section = (
            f"【前文参考，勿翻译】\n{prev_tail}\n\n【待翻译字幕】\n"
            if prev_tail else ""
        )
        lines = "\n".join(f"[{i}] {t}" for i, t in chunk)
        prompt = _SUBTITLE_PROMPT + context_section + lines

        try:
            if hasattr(llm, "translate_raw"):
                result = llm.translate_raw(prompt, max_tokens=16384)
            else:
                result = llm.translate_batch([lines], context="")
            raw = result.translated[0] if result.translated else ""
            token_usage_total = _merge_tokens(token_usage_total, result.tokens_used)
        except Exception:
            raw = ""

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = pattern.search(line)
            if m:
                idx = int(m.group(1))
                zh = m.group(2).strip()
                if 0 <= idx < len(texts) and zh:
                    translated[idx] = zh

        # Build context tail for next chunk
        tail_lines = []
        for orig_idx, _ in chunk[-_CONTEXT_CARRY:]:
            zh = translated[orig_idx]
            if zh and zh != "[翻译失败]":
                tail_lines.append(f"[{orig_idx}] {zh}")
        prev_tail = "\n".join(tail_lines)

        if progress_callback:
            pct = int((chunk_idx + 1) / total_chunks * 90)
            label = (f"LLM 翻译第 {chunk_idx + 1}/{total_chunks} 段…"
                     if total_chunks > 1 else "LLM 整篇翻译中…")
            progress_callback(pct, label)

    ok_count = sum(1 for i, t in enumerate(translated) if t != "[翻译失败]" or not texts[i].strip())
    if ok_count == 0:
        raise RuntimeError("LLM 整篇翻译失败：完全无法解析返回格式")

    if progress_callback:
        progress_callback(95, f"LLM 翻译完成 ({ok_count}/{len(texts)} 条)")
    return translated, token_usage_total


def translate_with_orchestrator(
    texts: list[str],
    mode: str = "speed",
    priority: list[str] | None = None,
    api_keys: list[dict] | None = None,
    context_aware: bool = False,
    tier: str = "free",
    progress_callback=None,
) -> tuple[list[str], list[int], dict | None]:
    """Main entry point.

    Args:
        texts: Source texts to translate.
        mode: "speed" or "quality".
        priority: Ordered list of backend names (e.g. ["google","bing","baidu","youdao"]).
        api_keys: List of LLM key configs: [{"platform":"claude","name":"my key","key":"sk-..."}].
        context_aware: If True, LLM reads full context first (paid only).
        tier: "free" / "vip" / "svip". svip injects platform keys automatically.
        progress_callback: fn(pct: int, message: str).

    Returns:
        (translated_texts, failed_indices, token_usage_dict_or_none)
    """
    if priority is None:
        priority = ["google", "baidu"]

    # Inject platform keys for ¥3 tier
    all_api_keys = list(api_keys or [])
    if tier == "svip":
        all_api_keys.extend(_get_platform_keys())

    # Build backend list in priority order
    ordered_backends = []
    for name in priority:
        if name.lower() in FREE_BACKENDS:
            ordered_backends.append(FREE_BACKENDS[name.lower()])

    # LLM backends
    llm_backends = _build_llm_backends(all_api_keys)

    # ── Paid user: LLM-powered translation ──
    if tier in ("vip", "svip") and llm_backends:
        if context_aware:
            # ── 第 3 档：上下文感知（整篇翻译，一次 LLM 调用）──
            try:
                translated, token_usage = _translate_whole_with_markers(
                    texts, llm_backends[0], progress_callback=progress_callback
                )
                failed_indices = [i for i, t in enumerate(translated) if t == "[翻译失败]"]

                if failed_indices and ordered_backends:
                    if progress_callback:
                        progress_callback(96, f"补全 {len(failed_indices)} 条遗漏字幕…")
                    texts_to_retry = [texts[i] for i in failed_indices]
                    retry_batches = _split_into_batches(texts_to_retry)
                    retry_results = []
                    for batch in retry_batches:
                        batch_res, _, _ = _run_speed_mode_batch(batch, ordered_backends)
                        retry_results.extend(batch_res)
                    for idx, retry_text in zip(failed_indices, retry_results):
                        translated[idx] = retry_text

                final_failed = [i for i, t in enumerate(translated) if t == "[翻译失败]"]
                return translated, final_failed, token_usage
            except Exception:
                if progress_callback:
                    progress_callback(0, "LLM 整篇翻译失败，使用免费后端…")
                # Fall through to free batch path
        else:
            # ── 第 2 档：逐段翻译（分批 LLM 调用，不传上下文）──
            batches = _split_into_batches(texts)
            all_results = []
            token_usage_total = None

            for bi, batch in enumerate(batches):
                if progress_callback:
                    pct = int((bi + 1) / len(batches) * 95)
                    progress_callback(pct, f"LLM 逐段翻译… (批 {bi+1}/{len(batches)})")

                if mode == "speed":
                    batch_results, _, batch_tokens = _run_speed_mode_batch(batch, llm_backends)
                else:
                    batch_results, _, _, batch_tokens = _run_quality_mode_batch(batch, llm_backends)

                all_results.extend(batch_results)
                token_usage_total = _merge_tokens(token_usage_total, batch_tokens)

                if bi < len(batches) - 1:
                    time.sleep(1.5)

            final_failed = [i for i, r in enumerate(all_results) if r == "[翻译失败]"]
            return all_results, final_failed, token_usage_total

        # ── Fallback: LLM failed, use free backends ──
        all_backends = ordered_backends
    else:
        all_backends = ordered_backends + llm_backends

    # ── 第 1 档：免费引擎批量翻译 ──
    batches = _split_into_batches(texts)
    all_results = []
    all_failed = []
    token_usage_total = None

    for bi, batch in enumerate(batches):
        if progress_callback:
            offset = 5 if context_aware else 0
            pct = int(offset + (bi + 1) / len(batches) * (95 - offset))
            progress_callback(pct, f"翻译中… (批 {bi + 1}/{len(batches)})")

        ctx = ""

        if mode == "speed":
            batch_results, backend, _ = _run_speed_mode_batch(batch, all_backends, context=ctx)
        else:
            batch_results, backend, failed_indices, _ = _run_quality_mode_batch(
                batch, all_backends, context=ctx
            )
            for fi in failed_indices:
                all_failed.append(fi + len(all_results))

        all_results.extend(batch_results)

        # Rate limiting between batches
        time.sleep(1)

    # Recompute failed indices from final results
    final_failed = [
        i for i, r in enumerate(all_results)
        if r == "[翻译失败]" or r is None or r == ""
    ]

    return all_results, final_failed, token_usage_total
