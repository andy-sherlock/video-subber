"""V3 Translation orchestrator.

T1: Tier system removed — LLM access gated by whether user provides API keys.
T3: Sliding window with overlap + context summary + seam review.
T5: Terminology auto-extraction before translation.
"""
import threading, time, re, logging
from .base import TranslationBackend, TranslationResult
from .google import GoogleBackend
from .bing import BingBackend
from .youdao import YoudaoBackend

_log = logging.getLogger(__name__)

_ALL_FREE = {
    "google": GoogleBackend(),
    "bing": BingBackend(),
    "youdao": YoudaoBackend(),
}
FREE_BACKENDS: dict[str, TranslationBackend] = {
    k: v for k, v in _ALL_FREE.items() if v.enabled
}


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
    if not new_tokens or not isinstance(new_tokens, dict):
        return base_tokens if base_tokens else None
    base = dict(base_tokens) if base_tokens else {}
    for key, val in new_tokens.items():
        if isinstance(val, (int, float)):
            base[key] = base.get(key, 0) + val
    return base


def _build_llm_backends(api_keys: list[dict]) -> list[TranslationBackend]:
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
    best_container = [None]
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


# ── T5: Terminology extraction ──

def extract_terminology(
    texts: list[str],
    llm: TranslationBackend,
) -> dict[str, str]:
    """Extract proper nouns and terminology from source text.

    Returns {original_term: locked_translation} dict.
    Called before translation to ensure consistent term handling across batches.
    """
    sample = texts[:500]
    joined = "\n".join(t for t in sample if t.strip())
    if not joined:
        return {}

    prompt = (
        "你是术语提取专家。从以下视频字幕原文中提取所有专有名词（人名、地名、"
        "作品名、技术术语、组织名称等），给出统一的中文翻译。\n"
        "输出格式（每行一条）：原文 → 中文翻译\n"
        "只输出术语表，不加任何解释。如果没有术语，输出「无」。\n\n"
        + joined
    )

    try:
        if hasattr(llm, 'translate_raw'):
            _t0 = time.monotonic()
            result = llm.translate_raw(prompt, max_tokens=2048)
            _dur = int((time.monotonic() - _t0) * 1000)
            raw = result.translated[0] if result.translated else ""
        else:
            return {}
    except Exception:
        _log.warning("术语提取失败，跳过")
        return {}

    if raw:
        _log.debug("术语提取原始返回 (%d字, %dms): %s", len(raw), _dur, raw[:500])
    else:
        _log.warning("术语提取返回为空 (LLM: %s)", getattr(llm, 'name', 'unknown'))

    terms = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line == "无":
            continue
        m = re.match(r'(.+?)\s*[-→>]+\s*(.+)', line)
        if m:
            orig = m.group(1).strip()
            zh = m.group(2).strip()
            if orig and zh:
                terms[orig] = zh

    if terms:
        _log.info("提取到 %d 条术语: %s", len(terms),
                   ", ".join(f"{k}→{v}" for k, v in list(terms.items())[:5]))
    return terms


# ── T3: Sliding window translation with context summary + seam review ──

_SUBTITLE_PROMPT = (
    "你是专业视频字幕翻译。翻译规则：\n"
    "- 阅读【内容摘要】，根据内容类型自动调整翻译策略：\n"
    "  脱口秀/搞笑 → 保留双关和幽默，直译不好笑时用意译\n"
    "  新闻/正式 → 保持准确性，语气正式但不僵硬\n"
    "  日常对话 → 使用生活化口语，像朋友聊天\n"
    "- 严格按 [编号] 译文 格式逐行输出，保持编号与原文一一对应\n"
    "- 每行对应一条字幕，独立翻译，不合并不拆分行\n"
    "- 专有名词、人名保留原文或使用通用译法\n"
    "- 不加任何解释、注释或 markdown 标记\n\n"
)

_CHUNK_SIZE = 1200
_OVERLAP_RATIO = 0.2
_OVERLAP_SIZE = int(_CHUNK_SIZE * _OVERLAP_RATIO)
_SEAM_CONTEXT = 50


def _generate_context_summary(
    texts: list[str],
    llm: TranslationBackend,
    terminology: dict[str, str],
) -> str:
    """Generate a brief context summary from the first batch of text."""
    sample = texts[:300]
    joined = "\n".join(t for t in sample if t.strip())
    if not joined:
        _log.warning("上下文摘要: 输入为空，跳过 (LLM: %s)", getattr(llm, 'name', 'unknown'))
        return ""

    term_section = ""
    if terminology:
        term_lines = "\n".join(f"- {k} → {v}" for k, v in list(terminology.items())[:20])
        term_section = f"\n已知术语：\n{term_lines}\n"

    prompt = (
        "阅读以下视频字幕原文前300段，写一份简短的翻译参考摘要（中文，100字内）。\n"
        "包含：① 内容主题 ② 主要人物/角色 ③ 语气风格（正式/口语/搞笑等）\n"
        "不要翻译原文，只写摘要。\n"
        + term_section + "\n" + joined
    )

    try:
        if hasattr(llm, 'translate_raw'):
            _log.warning("上下文摘要: 开始调用 translate_raw (LLM: %s, prompt长度=%d)",
                         getattr(llm, 'name', 'unknown'), len(prompt))
            result = llm.translate_raw(prompt, max_tokens=512)
            summary = (result.translated[0] if result.translated else "").strip()
            if summary:
                _log.info("上下文摘要已生成: %s", summary[:120])
            else:
                _log.warning("上下文摘要为空 (LLM: %s)", getattr(llm, 'name', 'unknown'))
            return summary
    except Exception as e:
        _log.warning("上下文摘要生成失败 (LLM: %s): %s", getattr(llm, 'name', 'unknown'), e)
        return ""


def _review_seam(
    translated: list[str],
    overlap_start: int,
    overlap_end: int,
    version_a: dict[int, str],
    version_b: dict[int, str],
    terminology: dict[str, str],
    llm: TranslationBackend,
) -> dict | None:
    """Review the overlap zone between two chunks and pick the best version.

    Returns {idx: best_translation} for the overlap zone, or None on failure.
    """
    before_lines = []
    for i in range(max(0, overlap_start - _SEAM_CONTEXT), overlap_start):
        zh = translated[i]
        if zh and zh != "[翻译失败]":
            before_lines.append(f"[{i}] {zh}")

    after_lines = []
    for i in range(overlap_end, min(len(translated), overlap_end + _SEAM_CONTEXT)):
        zh = translated[i]
        if zh and zh != "[翻译失败]":
            after_lines.append(f"[{i}] {zh}")

    a_lines = []
    b_lines = []
    for i in range(overlap_start, overlap_end):
        a_zh = version_a.get(i, "[翻译失败]")
        b_zh = version_b.get(i, "[翻译失败]")
        a_lines.append(f"[{i}] {a_zh}")
        b_lines.append(f"[{i}] {b_zh}")

    term_section = ""
    if terminology:
        term_section = "【术语表】\n" + "\n".join(
            f"- {k} → {v}" for k, v in list(terminology.items())[:20]
        ) + "\n\n"

    prompt = (
        "你是字幕质量审查员。以下是两段翻译在重叠区域的对比。\n"
        "请逐行选择更好的版本，或写出第三版。\n"
        "输出格式：[编号] 最终译文\n\n"
        + term_section
        + "【前文】\n" + "\n".join(before_lines[-20:]) + "\n\n"
        + "【版本A】\n" + "\n".join(a_lines) + "\n\n"
        + "【版本B】\n" + "\n".join(b_lines) + "\n\n"
        + "【后文】\n" + "\n".join(after_lines[:20]) + "\n"
    )

    try:
        if hasattr(llm, 'translate_raw'):
            result = llm.translate_raw(prompt, max_tokens=4096)
            raw = result.translated[0] if result.translated else ""
        else:
            return None
    except Exception:
        return None

    pattern = re.compile(r"\[(\d+)\]\s*(.+)")
    reviewed = {}
    for line in raw.split("\n"):
        m = pattern.match(line.strip())
        if m:
            idx = int(m.group(1))
            zh = m.group(2).strip()
            if overlap_start <= idx < overlap_end and zh:
                reviewed[idx] = zh

    return reviewed if reviewed else None


def _translate_whole_with_markers(
    texts: list[str],
    llm: TranslationBackend,
    terminology: dict[str, str] | None = None,
    context_summary: str = "",
    progress_callback=None,
) -> tuple[list[str], dict | None]:
    """V3 sliding window translation with overlap + context + terminology.

    Chunks texts with 20% overlap. Each chunk receives terminology table +
    context summary. After all chunks, seam review resolves overlap zones.
    """
    if progress_callback:
        progress_callback(0, "LLM 整篇上下文翻译中…")

    _log.warning("翻译步骤开始 (LLM: %s, 文本%d段, 术语%d条, 摘要%s)",
                 getattr(llm, 'name', 'unknown'), len(texts),
                 len(terminology) if terminology else 0,
                 f"'{context_summary[:50]}...'" if context_summary else "空")

    translated = ["[翻译失败]"] * len(texts)
    non_empty = []
    for i, t in enumerate(texts):
        if not t.strip():
            translated[i] = ""
        else:
            non_empty.append((i, t.strip()))

    if not non_empty:
        return translated, None

    # Build chunks with overlap
    step = _CHUNK_SIZE - _OVERLAP_SIZE
    chunks = []
    for s in range(0, len(non_empty), step):
        chunk = non_empty[s:s + _CHUNK_SIZE]
        if chunk:
            chunks.append(chunk)
        if s + _CHUNK_SIZE >= len(non_empty):
            break

    total_chunks = len(chunks)
    token_usage_total = None
    pattern = re.compile(r"\[(\d+)\]\s*(.+)")

    # Track overlap versions for seam review
    chunk_results: list[dict[int, str]] = []

    # Build prompt sections
    term_section = ""
    if terminology:
        term_lines = "\n".join(f"- {k} → {v}" for k, v in list(terminology.items())[:30])
        term_section = f"【术语表（翻译时必须使用）】\n{term_lines}\n\n"

    summary_section = ""
    if context_summary:
        summary_section = f"【内容摘要】\n{context_summary}\n\n"
        _log.info("翻译策略已启用（内容摘要 %d 字）", len(context_summary))

    prev_tail = ""

    for chunk_idx, chunk in enumerate(chunks):
        context_section = ""
        if prev_tail:
            context_section = f"【前文参考，勿翻译】\n{prev_tail}\n\n"

        lines = "\n".join(f"[{i}] {t}" for i, t in chunk)
        prompt = (
            _SUBTITLE_PROMPT + term_section + summary_section
            + context_section + "【待翻译字幕】\n" + lines
        )

        try:
            if hasattr(llm, "translate_raw"):
                result = llm.translate_raw(prompt, max_tokens=16384)
            else:
                result = llm.translate_batch([lines], context="")
            raw = result.translated[0] if result.translated else ""
            token_usage_total = _merge_tokens(token_usage_total, result.tokens_used)
        except Exception:
            raw = ""

        if raw:
            _log.debug("LLM 原始返回 (chunk %d/%d, %d字): %s",
                       chunk_idx + 1, total_chunks, len(raw), raw[:500])
        else:
            _log.warning("LLM 返回为空 (chunk %d/%d)", chunk_idx + 1, total_chunks)

        chunk_result = {}
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
                    chunk_result[idx] = zh

        chunk_results.append(chunk_result)

        # Build context tail for next chunk
        tail_lines = []
        for orig_idx, _ in chunk[-5:]:
            zh = translated[orig_idx]
            if zh and zh != "[翻译失败]":
                tail_lines.append(f"[{orig_idx}] {zh}")
        prev_tail = "\n".join(tail_lines)

        if progress_callback:
            pct = int((chunk_idx + 1) / total_chunks * 80)
            label = (f"LLM 翻译第 {chunk_idx + 1}/{total_chunks} 段…"
                     if total_chunks > 1 else "LLM 整篇翻译中…")
            progress_callback(pct, label)

    # Seam review for overlap zones (only when multiple chunks exist)
    if total_chunks > 1 and terminology is not None:
        if progress_callback:
            progress_callback(82, "接缝审查中…")

        for ci in range(1, len(chunks)):
            prev_chunk = chunks[ci - 1]
            curr_chunk = chunks[ci]

            prev_indices = {idx for idx, _ in prev_chunk}
            curr_indices = {idx for idx, _ in curr_chunk}
            overlap_indices = sorted(prev_indices & curr_indices)

            if len(overlap_indices) < 3:
                continue

            overlap_start = overlap_indices[0]
            overlap_end = overlap_indices[-1] + 1

            version_a = chunk_results[ci - 1]
            version_b = chunk_results[ci]

            reviewed = _review_seam(
                translated, overlap_start, overlap_end,
                version_a, version_b, terminology or {}, llm,
            )
            if reviewed:
                for idx, zh in reviewed.items():
                    translated[idx] = zh
                token_usage_total = _merge_tokens(
                    token_usage_total, {"seam_reviews": 1}
                )

        if progress_callback:
            progress_callback(90, "接缝审查完成")

    ok_count = sum(1 for i, t in enumerate(translated) if t != "[翻译失败]" or not texts[i].strip())
    if ok_count == 0:
        raise RuntimeError("LLM 整篇翻译失败：完全无法解析返回格式")

    if progress_callback:
        progress_callback(95, f"LLM 翻译完成 ({ok_count}/{len(texts)} 条)")
    _log.warning("翻译步骤结束 (LLM: %s): %d/%d 条成功",
                 getattr(llm, 'name', 'unknown'), ok_count, len(texts))
    return translated, token_usage_total


# ── Main entry point (T1: tier removed) ──

def translate_with_orchestrator(
    texts: list[str],
    mode: str = "speed",
    priority: list[str] | None = None,
    api_keys: list[dict] | None = None,
    context_aware: bool = False,
    progress_callback=None,
) -> tuple[list[str], list[int], dict | None]:
    """Main entry point.

    Args:
        texts: Source texts to translate.
        mode: "speed" or "quality".
        priority: Ordered list of backend names (e.g. ["google","bing","youdao"]).
        api_keys: List of LLM key configs.
        context_aware: If True and LLM keys provided, use full-context translation
                       with sliding window + terminology + seam review.
        progress_callback: fn(pct: int, message: str).

    Returns:
        (translated_texts, failed_indices, token_usage_dict_or_none)
    """
    if priority is None:
        priority = ["google", "bing", "youdao"]

    ordered_backends = []
    for name in priority:
        if name.lower() in FREE_BACKENDS:
            ordered_backends.append(FREE_BACKENDS[name.lower()])

    llm_backends = _build_llm_backends(api_keys or [])

    # ── LLM path: user provided API keys ──
    if llm_backends:
        if context_aware:
            translated = None
            token_usage = None
            last_llm = None
            _llm_order = [getattr(b, 'name', '?') for b in llm_backends]
            _log.warning("LLM 降级链启动: %d 个后端 (%s)", len(llm_backends), ", ".join(_llm_order))
            _chain_t0 = time.monotonic()
            for llm in llm_backends:
                _attempt_t0 = time.monotonic()
                _llm_name = getattr(llm, 'name', 'LLM')
                _llm_idx = llm_backends.index(llm) + 1
                try:
                    last_llm = llm
                    # T5: terminology extraction
                    if progress_callback:
                        progress_callback(0, f"正在用 {getattr(llm, 'name', 'LLM')} 提取术语…")
                    terminology = extract_terminology(texts, llm)

                    # T3: context summary
                    if progress_callback:
                        progress_callback(2, "正在分析内容…")
                    context_summary = _generate_context_summary(texts, llm, terminology)

                    # T3: sliding window translation
                    translated, token_usage = _translate_whole_with_markers(
                        texts, llm,
                        terminology=terminology,
                        context_summary=context_summary,
                        progress_callback=progress_callback,
                    )
                    failed_indices = [i for i, t in enumerate(translated) if t == "[翻译失败]"]
                    if len(failed_indices) < len(texts):
                        # This backend produced at least some valid translations — use it
                        _dur = int((time.monotonic() - _attempt_t0) * 1000)
                        _log.warning("LLM %s 翻译成功 (%d/%d 条, 耗时 %dms), 降级链结束",
                                     _llm_name, len(texts) - len(failed_indices), len(texts), _dur)
                        break
                    _log.warning("LLM %s 全部翻译失败，尝试下一个 (耗时 %dms)",
                                 _llm_name, int((time.monotonic() - _attempt_t0) * 1000))
                except Exception as _e:
                    _log.warning("LLM %s 异常 (%s: %s, 耗时 %dms), 尝试下一个",
                                 _llm_name, type(_e).__name__, _e,
                                 int((time.monotonic() - _attempt_t0) * 1000))
                    continue

            llm_produced = translated is not None
            _chain_dur = int((time.monotonic() - _chain_t0) * 1000)
            if not llm_produced:
                translated = ["[翻译失败]"] * len(texts)
                failed_indices = list(range(len(texts)))
                _log.warning("LLM 降级链结束: 全部失败, 总耗时 %dms, 切换免费引擎", _chain_dur)
            else:
                failed_indices = [i for i, t in enumerate(translated) if t == "[翻译失败]"]
                _log.warning("LLM 降级链结束: %s 成功, 总耗时 %dms",
                             getattr(last_llm, 'name', 'LLM'), _chain_dur)

            # Fill gaps with free backends
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
            free_names = [b.name for b in ordered_backends[:3]] if ordered_backends else []
            if llm_produced:
                llm_name = getattr(last_llm, 'name', 'LLM')
                if final_failed:
                    _log.info("LLM 上下文感知翻译: %s 完成 %d/%d 条, 剩余 %d 条由免费引擎(%s)补全",
                              llm_name, len(texts) - len(final_failed), len(texts),
                              len(final_failed), ", ".join(free_names))
                else:
                    _log.info("LLM 上下文感知翻译完成: %d 条 (%s)", len(texts), llm_name)
            else:
                _log.info("所有 LLM 均失败, 免费引擎(%s)完成翻译: %d/%d 条",
                          ", ".join(free_names), len(texts) - len(final_failed), len(texts))
            return translated, final_failed, token_usage
        else:
            # Per-batch LLM translation (no full context)
            batches = _split_into_batches(texts)
            all_results = []
            token_usage_total = None

            for bi, batch in enumerate(batches):
                if progress_callback:
                    pct = int((bi + 1) / len(batches) * 95)
                    progress_callback(pct, f"LLM 逐段翻译… (批 {bi+1}/{len(batches)})")

                batch_results, _, _, batch_tokens = _run_quality_mode_batch(batch, llm_backends)

                all_results.extend(batch_results)
                token_usage_total = _merge_tokens(token_usage_total, batch_tokens)

                if bi < len(batches) - 1:
                    time.sleep(1.5)

            final_failed = [i for i, r in enumerate(all_results) if r == "[翻译失败]"]
            llm_names = [b.name for b in llm_backends]
            _log.info("LLM 逐段翻译完成: %d/%d 条 (%s)", len(texts) - len(final_failed), len(texts), ", ".join(llm_names))
            return all_results, final_failed, token_usage_total

    # ── Free backends only ──
    all_backends = ordered_backends
    batches = _split_into_batches(texts)
    all_results = []
    all_failed = []
    token_usage_total = None
    used_backends = set()

    for bi, batch in enumerate(batches):
        if progress_callback:
            pct = int((bi + 1) / len(batches) * 95)
            progress_callback(pct, f"翻译中… (批 {bi + 1}/{len(batches)})")

        if mode == "speed":
            batch_results, backend, _ = _run_speed_mode_batch(batch, all_backends)
            if backend:
                used_backends.add(backend.name)
        else:
            batch_results, backend, failed_indices, _ = _run_quality_mode_batch(
                batch, all_backends, context=""
            )
            if backend:
                used_backends.add(backend.name)
            for fi in failed_indices:
                all_failed.append(fi + len(all_results))

        all_results.extend(batch_results)
        time.sleep(1)

    final_failed = [
        i for i, r in enumerate(all_results)
        if r == "[翻译失败]" or r is None or r == ""
    ]
    actual_names = list(used_backends) if used_backends else [b.name for b in all_backends]
    _log.info("免费引擎翻译完成: %d/%d 条 (实际使用: %s)", len(texts) - len(final_failed), len(texts), ", ".join(actual_names))

    return all_results, final_failed, token_usage_total
