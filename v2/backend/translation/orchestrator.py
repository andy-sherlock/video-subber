"""V3 Translation orchestrator.

T1: Tier system removed — LLM access gated by whether user provides API keys.
T3: Sliding window with overlap + context summary + seam review.
T5: Terminology auto-extraction before translation.
"""
import threading, time, re, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        except ImportError as e:
            _log.warning("LLM backend %s skipped: %s", platform, e)
        except Exception as e:
            _log.warning("LLM backend %s skipped: %s", platform, e)
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


def _extract_terminology_and_summary(
    texts: list[str],
    llm,
) -> tuple[dict[str, str], str]:
    """Single LLM call: extract terminology AND generate context summary.

    Both tasks share the same input text — merging saves one network round-trip.
    On partial failure: returns what it can, caller handles fallback.
    """
    sample = texts[:500]
    joined = "\n".join(t for t in sample if t.strip())
    if not joined:
        return ({}, "")

    prompt = (
        "你是视频字幕预处理助手。完成以下两项任务，用 Markdown 标题分隔：\n\n"
        "## 术语表\n"
        "从以上字幕原文中提取所有专有名词（人名、地名、作品名、技术术语、组织名称等），"
        "给出统一的中文翻译。\n"
        "输出格式（每行一条）：原文 → 中文翻译\n"
        "只输出术语表，不加任何解释。如果没有术语，输出「无」。\n\n"
        "## 内容摘要\n"
        "阅读以上字幕原文，写一份简短的翻译参考摘要（中文，100字内）。\n"
        "包含：① 内容主题 ② 主要人物/角色 ③ 语气风格（正式/口语/搞笑等）\n"
        "不要翻译原文，只写摘要。\n\n"
        + joined
    )

    try:
        if hasattr(llm, 'translate_raw'):
            _t0 = time.monotonic()
            result = llm.translate_raw(prompt, max_tokens=4096)
            _dur = int((time.monotonic() - _t0) * 1000)
            raw = result.translated[0] if result.translated else ""
        else:
            return ({}, "")
    except Exception:
        _log.warning("合并术语+摘要提取失败 (LLM: %s)",
                      getattr(llm, 'name', 'unknown'))
        return ({}, "")

    if raw:
        _log.debug("合并提取原始返回 (%d字, %dms): %s",
                    len(raw), _dur, raw[:300])

    # Parse: split by ## headings
    terms_match = re.search(
        r'##\s*术语表\s*\n(.*?)(?=##\s|\Z)', raw, re.DOTALL)
    summary_match = re.search(
        r'##\s*内容摘要\s*\n(.*?)(?=##\s|\Z)', raw, re.DOTALL)

    terms_section = terms_match.group(1).strip() if terms_match else ""
    summary_section = summary_match.group(1).strip() if summary_match else ""

    # Parse terms using existing regex
    terms = {}
    for line in (terms_section or "").split("\n"):
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
        _log.info("合并提取: %d 条术语 + 摘要(%d字)", len(terms),
                   len(summary_section))
    elif summary_section:
        _log.info("合并提取: 无术语 + 摘要(%d字)", len(summary_section))

    return (terms, summary_section)


# ── T3: Sliding window translation with context summary + seam review ──

_TARGET_LANGUAGE = "简体中文"

_SUBTITLE_PROMPT = (
    f"你是专业视频字幕翻译，所有翻译的目标语言为{_TARGET_LANGUAGE}。翻译规则：\n"
    "- 阅读【内容摘要】，根据内容类型自动调整翻译策略：\n"
    "  脱口秀/搞笑 → 保留双关和幽默，直译不好笑时用意译\n"
    "  新闻/正式 → 保持准确性，语气正式但不僵硬\n"
    "  日常对话 → 使用生活化口语，像朋友聊天\n"
    "- 严格按 [编号] 译文 格式逐行输出，保持编号与原文一一对应\n"
    "- 专有名词、人名保留原文或使用通用译法\n"
    "- 不加任何解释、注释或 markdown 标记\n\n"
)

_OVERLAP_RATIO = 0.2


def _compute_chunk_size(llm) -> int:
    """Return chunk size (line count) based on the model's fatigue limit (P253).

    LLMs suffer from "Lost in the Middle": attention degrades once input
    crosses ~30% of the context window. Instead of (ctx - 18000)//8 which
    fills all remaining space up to the ceiling, use a fatigue-aware cap:
    ctx * 30% → subtract prompt overhead (2000 tokens) → convert to lines.

    Old formula: (32768 - 18000)//8 = 1750 lines/batch ≈ 14000 tokens (44%).
    New formula: (32768*0.30 - 2000)//8 ≈ 950 lines/batch (30%). 64K+ models
    capped at 1200 (was 3000).
    """
    ctx = getattr(llm, 'max_input_tokens', 32768) or 32768
    fatigue = int(ctx * 0.30)
    available = fatigue - 2000  # prompt overhead
    raw = available // 8
    return max(200, min(raw, 1200))

_SEAM_CONTEXT = 50
_INDEXED_LINE_RE = re.compile(r"\[(\d+)\]\s*(.+)")


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

    reviewed = {}
    for line in raw.split("\n"):
        m = _INDEXED_LINE_RE.match(line.strip())
        if m:
            idx = int(m.group(1))
            zh = m.group(2).strip()
            if overlap_start <= idx < overlap_end and zh:
                reviewed[idx] = zh

    return reviewed if reviewed else None


_TRANSLATION_CJK_MIN_RATIO = 0.1  # 10% CJK characters — below this, treat as untranslated
_SHORT_LINE_MAX_CHARS = 15  # P230: lines shorter than this get relaxed CJK ratio
_SHORT_LINE_CJK_MIN_RATIO = 0.05  # P230: 5% for short lines (often contain English names)

# P230: multiple format patterns for loose matching
_INDEXED_LINE_PATTERNS = [
    re.compile(r"\[(\d+)\]\s*(.+)"),       # [50] content
    re.compile(r"\[\s*(\d+)\s*\]\s*(.+)"), # [ 50 ] content (extra spaces)
    re.compile(r"【(\d+)】\s*(.+)"),         # 【50】content (Chinese brackets)
    re.compile(r"(\d+)[\.\)、\s]\s*(.+)"),  # 50. content or 50) content
]



def _is_valid_translation(zh: str) -> bool:
    """Return True if *zh* looks like a real Chinese translation (P156 gate).

    P230: Short lines (<15 chars) use relaxed 5% CJK ratio.
    Longer lines use standard 10%.
    """
    if not zh or not zh.strip():
        return False
    cjk = sum(1 for c in zh if '一' <= c <= '鿿')
    total = max(len(zh), 1)
    ratio = cjk / total
    if total <= _SHORT_LINE_MAX_CHARS:
        return ratio >= _SHORT_LINE_CJK_MIN_RATIO
    return ratio >= _TRANSLATION_CJK_MIN_RATIO


def _translate_one_chunk(
    chunk: list[tuple[int, str]],
    prompt_prefix: str,
    llm: TranslationBackend,
    ci: int,
    total: int,
) -> tuple[int, dict[int, str], dict | None]:
    """Translate a single chunk. Returns (chunk_idx, {idx: translated}, tokens_used)."""
    lines = "\n".join(f"[{i}] {t}" for i, t in chunk)
    prompt = prompt_prefix + "【待翻译字幕】\n" + lines

    if not hasattr(llm, "translate_raw"):
        raise RuntimeError(f"LLM 后端 {getattr(llm, 'name', 'unknown')} 不支持 translate_raw")

    result = llm.translate_raw(prompt, max_tokens=16384)
    raw = result.translated[0] if result.translated else ""

    if raw:
        _log.debug("chunk %d/%d: 原始返回 %d 字，内容前 500 字: %s",
                   ci + 1, total, len(raw), raw[:500])
    else:
        _log.warning("LLM 返回为空 (chunk %d/%d)", ci + 1, total)

    chunk_result: dict[int, str] = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # P230: try multiple relaxed patterns in order
        idx = None
        zh = None
        for pat in _INDEXED_LINE_PATTERNS:
            m = pat.search(line)
            if m:
                idx = int(m.group(1))
                zh = m.group(2).strip()
                break
        if idx is not None and zh:
            if not _is_valid_translation(zh):
                _log.warning("翻译内容校验失败 (CJK): idx=%d text=%r",
                             idx, zh[:60])
                continue
            chunk_result[idx] = zh

    return ci, chunk_result, result.tokens_used


def _translate_whole_with_markers(
    texts: list[str],
    llm: TranslationBackend,
    terminology: dict[str, str] | None = None,
    context_summary: str = "",
    progress_callback=None,
) -> tuple[list[str], dict | None, dict[int, list[str]]]:
    """V3 sliding window translation with overlap + context + terminology.

    Chunks texts with 20% overlap. Each chunk receives terminology table +
    context summary. After all chunks, seam review resolves overlap zones.

    Returns:
        (translated, token_usage, sub_splits)
        sub_splits: {original_idx: [split_part_1, split_part_2, ...]}
        for segments the LLM split using [0.1] [0.2] notation. Empty dict if
        no splitting was done.
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
        return translated, None, {}

    # Build chunks with overlap
    chunk_size = _compute_chunk_size(llm)
    overlap_size = int(chunk_size * _OVERLAP_RATIO)
    step = chunk_size - overlap_size
    chunks = []
    for s in range(0, len(non_empty), step):
        chunk = non_empty[s:s + chunk_size]
        if chunk:
            chunks.append(chunk)
        if s + chunk_size >= len(non_empty):
            break

    total_chunks = len(chunks)
    token_usage_total = None
    sub_splits: dict[int, list[str]] = {}

    # Build prompt sections
    term_section = ""
    if terminology:
        term_lines = "\n".join(f"- {k} → {v}" for k, v in list(terminology.items())[:30])
        term_section = f"【术语表（翻译时必须使用）】\n{term_lines}\n\n"

    summary_section = ""
    if context_summary:
        summary_section = f"【内容摘要】\n{context_summary}\n\n"
        _log.info("翻译策略已启用（内容摘要 %d 字）", len(context_summary))

    prompt_prefix = _SUBTITLE_PROMPT + term_section + summary_section
    chunk_results = [{} for _ in range(total_chunks)]
    translated_lock = threading.Lock()

    completed = 0
    with ThreadPoolExecutor(max_workers=min(total_chunks, 5)) as executor:
        futures = {
            executor.submit(_translate_one_chunk, chunks[ci], prompt_prefix, llm, ci, total_chunks): ci
            for ci in range(total_chunks)
        }
        for future in as_completed(futures):
            ci, chunk_result, chunk_tokens = future.result()
            chunk_results[ci] = chunk_result
            with translated_lock:
                for idx, zh in chunk_result.items():
                    if 0 <= idx < len(texts) and zh:
                        translated[idx] = zh
            token_usage_total = _merge_tokens(token_usage_total, chunk_tokens)
            completed += 1
            if progress_callback and total_chunks > 1:
                pct = int(completed / total_chunks * 80)
                progress_callback(pct, f"LLM 翻译第 {completed}/{total_chunks} 段…")

    if progress_callback:
        progress_callback(80, f"LLM 翻译完成 ({total_chunks}/{total_chunks} 段)")

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

    # Phase 1C: finalize sub_splits — join parts into translated list,
    # keep only multi-part splits for downstream use
    final_sub_splits: dict[int, list[str]] = {}
    for idx, parts in sorted(sub_splits.items()):
        if len(parts) >= 2:
            translated[idx] = " ".join(parts)
            final_sub_splits[idx] = parts
        elif len(parts) == 1:
            # Single sub-part — treat as regular translation (override if needed)
            if translated[idx] == "[翻译失败]":
                translated[idx] = parts[0]
    if final_sub_splits:
        _log.info("LLM 已预拆分段: %d 条 (%d 个子句)",
                  len(final_sub_splits), sum(len(p) for p in final_sub_splits.values()))

    ok_count = sum(1 for i, t in enumerate(translated) if t != "[翻译失败]" or not texts[i].strip())
    if ok_count == 0:
        raise RuntimeError("LLM 整篇翻译失败：完全无法解析返回格式")

    if progress_callback:
        progress_callback(95, f"LLM 翻译完成 ({ok_count}/{len(texts)} 条)")
    _log.warning("翻译步骤结束 (LLM: %s): %d/%d 条成功",
                 getattr(llm, 'name', 'unknown'), ok_count, len(texts))
    return translated, token_usage_total, final_sub_splits


# ── Main entry point (T1: tier removed) ──

def translate_with_orchestrator(
    texts: list[str],
    mode: str = "speed",
    priority: list[str] | None = None,
    api_keys: list[dict] | None = None,
    context_aware: bool = False,
    progress_callback=None,
) -> tuple[list[str], list[int], dict | None, dict[int, list[str]]]:
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
        (translated_texts, failed_indices, token_usage_or_none, sub_splits)
        sub_splits: {original_idx: [split_part_1, ...]} from LLM auto-splitting,
        or empty dict if no LLM or no splitting was done.
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
            sub_splits = {}
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
                    # T5+T3 merged: terminology + context summary in one call
                    if progress_callback:
                        progress_callback(0, f"正在用 {getattr(llm, 'name', 'LLM')} 分析内容…")
                    terminology, context_summary = _extract_terminology_and_summary(texts, llm)
                    # Fallback: if either part missing, call old function individually
                    if not terminology:
                        if progress_callback:
                            progress_callback(1, f"正在用 {getattr(llm, 'name', 'LLM')} 提取术语…")
                        terminology = extract_terminology(texts, llm)
                    if not context_summary:
                        if progress_callback:
                            progress_callback(2, "正在分析内容…")
                        context_summary = _generate_context_summary(texts, llm, terminology)

                    # P165: P1 smoke test — summary must contain CJK
                    if context_summary and not _is_valid_translation(context_summary):
                        _log.warning("P165 内容闸: %s 摘要无中文 (CJK<%.0f%%), 判定不可靠, 跳过 → %r",
                                     _llm_name, _TRANSLATION_CJK_MIN_RATIO * 100,
                                     context_summary[:80])
                        continue

                    # T3: sliding window translation
                    translated, token_usage, sub_splits = _translate_whole_with_markers(
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
                sub_splits = {}
                _log.warning("LLM 降级链结束: 全部失败, 总耗时 %dms, 切换免费引擎", _chain_dur)
            else:
                failed_indices = [i for i, t in enumerate(translated) if t == "[翻译失败]"]
                _log.warning("LLM 降级链结束: %s 成功, 总耗时 %dms",
                             getattr(last_llm, 'name', 'LLM'), _chain_dur)

            # P230: retry individual failed segments with minimal prompt
            # (no format to get wrong). Only when ≤10 segments failed.
            if failed_indices and last_llm and len(failed_indices) <= 10:
                _log.info("P230 单独重试 %d 条遗漏字幕 (LLM: %s)",
                          len(failed_indices), getattr(last_llm, 'name', 'LLM'))
                for fi in list(failed_indices):
                    try:
                        simple_prompt = (
                            f"将以下内容翻译成{_TARGET_LANGUAGE}。只输出译文，不要解释。\n\n{texts[fi]}"
                        )
                        retry = last_llm.translate_raw(simple_prompt, max_tokens=512)
                        retry_text = (retry.translated[0] if retry.translated else "").strip()
                        if retry_text and _is_valid_translation(retry_text):
                            translated[fi] = retry_text
                            failed_indices.remove(fi)
                    except Exception:
                        pass  # fall through to free backend retry below

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
            return translated, final_failed, token_usage, sub_splits
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
            return all_results, final_failed, token_usage_total, {}

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

    return all_results, final_failed, token_usage_total, {}
