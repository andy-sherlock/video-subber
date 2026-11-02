"""V3 Translation orchestrator.

T1: Tier system removed — LLM access gated by whether user provides API keys.
T3: Sliding window with overlap + context summary + seam review.
T5: Terminology auto-extraction before translation.
"""
import threading, time, re, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base import (
    TranslationBackend,
    TranslationResult,
    FAILED_TRANSLATION,
    SHARED_TRANSLATION_RULES,
    _is_unchanged_pure_proper_name,
    classify_translation_failure,
    is_clean_translation_text,
)
from .paid_request import call_paid, wait_message
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

_IMMEDIATE_LLM_FUSE_FAILURES = {
    "auth",
    "connection",
    "timeout",
    "rate_limit",
    "service_error",
    # D-1 增 1 / 增 3：这两类以前都归 service_error。上下文感知路线（:1650 免费补洞）
    # 还在用这张名单，加进来让它的行为一点不变。
    "insufficient_balance",
    "request_rejected",
}

# P404：编号协议失败后，在同一付费后端上做整批 → 两半 → 逐条的分级恢复。
# 只有 numbering_format 会触发；连接/鉴权/限流等在上面的熔断名单里先被拦掉，
# 不能让恢复链对着一条已经断掉的连接连打几十次。
_PAID_BATCH_RETRY_CORRECTION = (
    "上一次返回条数与输入不符，很可能合并了相邻两条。"
    "这一次必须严格一条对一条，即使两条原文本是一句话。"
)
_MAX_PROTOCOL_SINGLE_RETRIES = 40


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
                # 用户在界面上选了哪个 DeepSeek 型号就用哪个；传空字符串或已停用的
                # deepseek-chat 时，DeepSeekBackend 内部会迁移到 deepseek-v4-flash。
                backends.append(DeepSeekBackend(
                    api_key=key,
                    model=entry.get("model", ""),
                ))
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
) -> tuple[list[str], TranslationBackend | None, dict | None, dict[int, str]]:
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
            cleaned = [
                x.strip() if is_clean_translation_text(x) else FAILED_TRANSLATION
                for x in r.translated
            ]
            failure_by_index = {
                i: r.failure_kind or "quality_rejected"
                for i, value in enumerate(cleaned)
                if value == FAILED_TRANSLATION
            }
            valid_count = sum(1 for x in cleaned if x != FAILED_TRANSLATION)
            with best_lock:
                if best_container[0] is None or valid_count > best_container[0][3]:
                    best_container[0] = (
                        cleaned,
                        backend,
                        r.tokens_used,
                        valid_count,
                        failure_by_index,
                    )
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
        translated, backend, tokens, _, failure_by_index = best_container[0]
        while len(translated) < len(batch):
            translated.append("[翻译失败]")
        for i, value in enumerate(translated[:len(batch)]):
            if value == FAILED_TRANSLATION:
                failure_by_index.setdefault(i, "unknown")
        return translated[:len(batch)], backend, tokens, failure_by_index
    return ["[翻译失败]"] * len(batch), None, None, {
        i: "unknown" for i in range(len(batch))
    }


def _run_quality_mode_batch(
    batch: list[str],
    backends: list[TranslationBackend],
    context: str = "",
) -> tuple[list[str], TranslationBackend | None, list[int], dict | None, dict[int, str]]:
    n = len(batch)
    results = ["[翻译失败]"] * n
    used_backend = None
    failed_indices = set(range(n))
    batch_tokens = None
    failure_by_index: dict[int, str] = {}

    for backend in backends:
        if not failed_indices:
            break
        try:
            r = backend.translate_batch(batch, context=context)
            accepted_any = False
            for i in list(failed_indices):
                if i < len(r.translated) and is_clean_translation_text(r.translated[i]):
                    results[i] = r.translated[i].strip()
                    failed_indices.discard(i)
                    failure_by_index.pop(i, None)
                    accepted_any = True
                else:
                    failure_by_index[i] = r.failure_kind or "quality_rejected"
            if accepted_any and used_backend is None:
                used_backend = backend
            batch_tokens = _merge_tokens(batch_tokens, r.tokens_used)
        except Exception as error:
            kind = classify_translation_failure(error)
            for i in failed_indices:
                failure_by_index[i] = kind
            continue

    return results, used_backend, list(failed_indices), batch_tokens, failure_by_index


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
    "- 多义词根据场景选最自然的中文表达（如旅行vlog中clock→手表/表而非钟，\n"
    "  station→车站而非局），避免生硬字面直译\n"
    "- 严格按 [编号] 译文 格式逐行输出，保持编号与原文一一对应\n"
    "- 专有名词、人名保留原文或使用通用译法\n"
    "- 不加任何解释、注释或 markdown 标记\n\n"
)

_OVERLAP_RATIO = 0.2


def _compute_chunk_size(llm) -> int:
    """Return chunk size (line count) based on the model's fatigue limit (P253).

    P408: delegates to shared compute_fatigue_chunk in model_cache.py.
    Translation parameters: overhead=2000, per_unit=8, floor=200, ceiling=1200.
    """
    from backend.model_cache import compute_fatigue_chunk
    ctx = getattr(llm, 'max_input_tokens', 32768) or 32768
    return compute_fatigue_chunk(ctx, overhead=2000, per_unit=8, floor=200, ceiling=1200)

_SEAM_CONTEXT = 50
_INDEXED_LINE_RE = re.compile(r"\[(\d+)\]\s*(.+)")
_SEAM_REVIEW_COMMENT_MARKERS = (
    "版本A", "版本B", "选A", "选B", "选择A", "选择B",
    "更自然", "更符合", "建议", "原因", "解释", "因为",
)


def _looks_like_seam_review_comment(text: str) -> bool:
    """Return True when an LLM returned review notes instead of subtitle text."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    marker_hits = sum(1 for marker in _SEAM_REVIEW_COMMENT_MARKERS if marker in normalized)
    return marker_hits >= 2


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
    source_texts: list[str] | None = None,
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
    seen: set[int] = set()
    for line in raw.split("\n"):
        m = _INDEXED_LINE_RE.match(line.strip())
        if m:
            idx = int(m.group(1))
            zh = m.group(2).strip()
            if idx in seen:
                _log.debug("接缝建议拒绝: idx=%d kind=duplicate_number", idx)
                return None
            seen.add(idx)
            if not overlap_start <= idx < overlap_end:
                _log.debug("接缝建议拒绝: idx=%d kind=outside_overlap", idx)
                continue
            if not zh or not is_clean_translation_text(zh):
                _log.debug("接缝建议拒绝: idx=%d kind=quality_rejected", idx)
                continue
            if _looks_like_seam_review_comment(zh):
                _log.debug("接缝建议拒绝: idx=%d kind=review_comment", idx)
                continue
            source = (
                source_texts[idx]
                if source_texts is not None and 0 <= idx < len(source_texts)
                else ""
            )
            if not _is_valid_translation_for_source(source, zh):
                _log.debug("接缝建议拒绝: idx=%d kind=target_language", idx)
                continue
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


def _is_valid_translation_for_source(source: str, translated: str) -> bool:
    """Apply the Chinese gate, with the approved unchanged-name exception.

    P404 第五轮试过在这里加一条「译文整段包含源文本 = 回抄原文」的检测，
    用来抓「原文：second source」那种脏内容。**已撤回**——它会误伤真实场景：
    译文合理保留源文本是常态（`Lizard Village` → `利泽德村（Lizard Village）`），
    实测直接打红 7 条既有测试。为挡一个构造案例引入一个真实误伤，不划算。
    该形状的漏拦已在 P404 记录中明确接受，见管道文件「明确接受的残留」。
    """
    return _is_valid_translation(translated) or _is_unchanged_pure_proper_name(
        source, translated
    )


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
    source_by_idx = dict(chunk)
    expected_ids = set(source_by_idx)
    seen_ids: set[int] = set()
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
            if idx in seen_ids:
                _log.debug("R9 块结果拒绝: idx=%d kind=duplicate_number", idx)
                return ci, {}, result.tokens_used
            seen_ids.add(idx)
            if idx not in expected_ids:
                _log.debug("R9 块结果拒绝: idx=%d kind=outside_chunk", idx)
                continue
            source = source_by_idx.get(idx, "")
            if (
                not is_clean_translation_text(zh)
                or not _is_valid_translation_for_source(source, zh)
            ):
                _log.warning("翻译内容校验失败 (CJK): idx=%d text=%r",
                             idx, zh[:60])
                continue
            chunk_result[idx] = zh

    return ci, chunk_result, result.tokens_used


def _initialize_whole_translation(texts: list[str]) -> tuple[list[str], list[tuple[int, str]]]:
    translated = ["[翻译失败]"] * len(texts)
    non_empty = []
    for i, t in enumerate(texts):
        if not t.strip():
            translated[i] = ""
        else:
            non_empty.append((i, t.strip()))
    return translated, non_empty


def _build_overlapping_chunks(
    non_empty: list[tuple[int, str]],
    llm: TranslationBackend,
) -> list[list[tuple[int, str]]]:
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
    return chunks


def _build_whole_translation_prompt_prefix(
    terminology: dict[str, str] | None,
    context_summary: str,
) -> str:
    term_section = ""
    if terminology:
        term_lines = "\n".join(f"- {k} → {v}" for k, v in list(terminology.items())[:30])
        term_section = f"【术语表（翻译时必须使用）】\n{term_lines}\n\n"

    summary_section = ""
    if context_summary:
        summary_section = f"【内容摘要】\n{context_summary}\n\n"
        _log.info("翻译策略已启用（内容摘要 %d 字）", len(context_summary))

    return _SUBTITLE_PROMPT + term_section + summary_section


def _translate_chunks_with_prompt(
    chunks: list[list[tuple[int, str]]],
    texts: list[str],
    translated: list[str],
    prompt_prefix: str,
    llm: TranslationBackend,
    progress_callback=None,
) -> tuple[list[dict[int, str]], dict | None]:
    total_chunks = len(chunks)
    chunk_results = [{} for _ in range(total_chunks)]
    translated_lock = threading.Lock()
    token_usage_total = None

    completed = 0
    with ThreadPoolExecutor(max_workers=min(total_chunks, 5)) as executor:
        futures = {
            executor.submit(_translate_one_chunk, chunks[ci], prompt_prefix, llm, ci, total_chunks): ci
            for ci in range(total_chunks)
        }
        for future in as_completed(futures):
            ci, chunk_result, chunk_tokens = future.result()
            chunk_results[ci] = chunk_result
            expected_ids = {idx for idx, _ in chunks[ci]}
            with translated_lock:
                for idx, zh in chunk_result.items():
                    if idx in expected_ids and 0 <= idx < len(texts) and zh:
                        translated[idx] = zh
            token_usage_total = _merge_tokens(token_usage_total, chunk_tokens)
            completed += 1
            if progress_callback and total_chunks > 1:
                pct = int(completed / total_chunks * 80)
                progress_callback(pct, f"LLM 翻译第 {completed}/{total_chunks} 段…")

    return chunk_results, token_usage_total


def _review_translation_seams(
    chunks: list[list[tuple[int, str]]],
    chunk_results: list[dict[int, str]],
    translated: list[str],
    terminology: dict[str, str],
    llm: TranslationBackend,
    token_usage_total: dict | None,
    progress_callback=None,
    source_texts: list[str] | None = None,
) -> dict | None:
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
            version_a, version_b, terminology, llm,
            source_texts=source_texts,
        )
        if reviewed:
            for idx, zh in reviewed.items():
                if idx in overlap_indices:
                    translated[idx] = zh
                else:
                    _log.debug(
                        "接缝写回拒绝: idx=%d kind=outside_overlap", idx
                    )
            token_usage_total = _merge_tokens(
                token_usage_total, {"seam_reviews": 1}
            )

    if progress_callback:
        progress_callback(90, "接缝审查完成")

    return token_usage_total


def _finalize_whole_translation(
    texts: list[str],
    translated: list[str],
    sub_splits: dict[int, list[str]],
) -> tuple[dict[int, list[str]], int]:
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
    return final_sub_splits, ok_count


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

    translated, non_empty = _initialize_whole_translation(texts)

    if not non_empty:
        return translated, None, {}

    chunks = _build_overlapping_chunks(non_empty, llm)
    total_chunks = len(chunks)
    sub_splits: dict[int, list[str]] = {}

    prompt_prefix = _build_whole_translation_prompt_prefix(terminology, context_summary)
    chunk_results, token_usage_total = _translate_chunks_with_prompt(
        chunks, texts, translated, prompt_prefix, llm, progress_callback
    )

    if progress_callback:
        progress_callback(80, f"LLM 翻译完成 ({total_chunks}/{total_chunks} 段)")

    # Seam review for overlap zones (only when multiple chunks exist)
    if total_chunks > 1 and terminology is not None:
        token_usage_total = _review_translation_seams(
            chunks,
            chunk_results,
            translated,
            terminology,
            llm,
            token_usage_total,
            progress_callback,
            source_texts=texts,
        )

    # Phase 1C: finalize sub_splits — join parts into translated list,
    # keep only multi-part splits for downstream use
    final_sub_splits, ok_count = _finalize_whole_translation(texts, translated, sub_splits)
    if ok_count == 0:
        raise RuntimeError("LLM 整篇翻译失败：完全无法解析返回格式")

    if progress_callback:
        progress_callback(95, f"LLM 翻译完成 ({ok_count}/{len(texts)} 条)")
    _log.warning("翻译步骤结束 (LLM: %s): %d/%d 条成功",
                 getattr(llm, 'name', 'unknown'), ok_count, len(texts))
    return translated, token_usage_total, final_sub_splits


def _accept_recovered_batch(
    batch, results, failed_local, failure_local, translated, *, offset=0
):
    """把一次恢复调用的结果按原始位置回填。

    每条都必须过与主路径完全相同的两道强检查（P352）：恢复旁路绝不允许降低标准，
    否则 `---`、编号残留、点评文字、英文原文会从这条路漏进字幕。
    """
    for local_index, text in enumerate(translated):
        index = offset + local_index
        if index not in failed_local:
            continue
        candidate = (text or "").strip()
        if (
            is_clean_translation_text(candidate)
            and _is_valid_translation_for_source(batch[index], candidate)
        ):
            results[index] = candidate
            failed_local.discard(index)
            failure_local.pop(index, None)
        elif candidate:
            failure_local[index] = "quality_rejected"


def _recover_paid_batch_protocol_failure(
    batch, backend, results, failed_local, failure_local, *, paid
):
    """P404：编号协议失败后，在同一付费后端上做整批纠正与半批纠正。

        整批纠正 ──仍有失败──> 前半批 / 后半批 ──仍有失败──> (交回调用方逐条恢复)
             │                      │
           全好                    全好 ──按 offset 回填原位置
             │
          直接返回

    半批只在「该范围内还有失败位置」时才发：已经补齐的那一半再调一次不只是
    白花钱，万一那次无关调用断连，`fused` 会把真正待恢复的另一半也带走。

    只处理批量层级；仍失败的位置交回调用方走逐条恢复（上限
    `_MAX_PROTOCOL_SINGLE_RETRIES`）。就地修改 results / failed_local / failure_local。

    返回本轮用掉的 tokens。D-1：每一次重发都经过付费请求函数——连不上 / 余额不足
    在那里原地等、换 Key，不会走到这里；不会自己好的错直接抛出，任务停在出错。
    这里只剩 AI 回复格式类的失败。本批不得切换到第二个付费厂商。
    """
    tokens = None

    def _call(sub_batch, *, offset=0):
        """发一次纠正重发。返回可用结果或 None。

        失败原因**只记本次覆盖的位置**，否则后半批的失败会盖掉前半批已经
        查明的原因，用户看到的失败提示就会指向错的病因。
        """
        nonlocal tokens
        scope = {
            index for index in failed_local
            if offset <= index < offset + len(sub_batch)
        }
        _used, result = paid(
            lambda b: b.translate_batch(sub_batch, context=_PAID_BATCH_RETRY_CORRECTION)
        )
        tokens = _merge_tokens(tokens, result.tokens_used)
        kind = getattr(result, "failure_kind", None)
        if kind:
            for index in scope:
                failure_local[index] = kind
        return result if kind is None else None

    whole = _call(batch)
    if whole is not None:
        _accept_recovered_batch(
            batch, results, failed_local, failure_local, whole.translated
        )
    if not failed_local:
        return tokens

    midpoint = (len(batch) + 1) // 2
    for start, end in ((0, midpoint), (midpoint, len(batch))):
        if start >= end or not failed_local:
            continue
        # 这半批已经全部恢复了：再调一次是白花钱。
        if not any(start <= index < end for index in failed_local):
            continue
        half = _call(batch[start:end], offset=start)
        if half is not None:
            _accept_recovered_batch(
                batch,
                results,
                failed_local,
                failure_local,
                half.translated,
                offset=start,
            )
    return tokens


def _paid_requester(llm_backends, first, progress_callback, pct, resume_message=None):
    """D-1 C3：从 `first` 开始、按用户排序换 Key 的付费请求；等待时更新进度文字，
    恢复后换回 `resume_message`（⑦.5 R3）。"""
    if progress_callback is None:
        # P514：没有汇报函数，原地等时就没法发现主程序没了——宁可调用时就报错。
        raise ValueError("付费请求必须带进度汇报函数")
    order = [first, *[b for b in llm_backends if b is not first]]

    def on_wait(kind, name, _seconds):
        progress_callback(pct, wait_message(kind, name))

    def on_resume():
        if resume_message:
            progress_callback(pct, resume_message)

    def run(request):
        return call_paid(order, request, on_wait=on_wait, on_resume=on_resume)

    return run


def _translate_llm_per_batch(
    texts: list[str],
    llm_backends: list,
    ordered_backends: list | None = None,
    progress_callback=None,
    batch_progress_callback=None,
    engine_status_callback=None,
    item_result_callback=None,
    translation_stats_callback=None,
) -> tuple[
    list[str],
    list[int],
    dict | None,
    dict[int, list[str]],
    dict[int, str],
]:
    """Per-batch LLM translation (no full context)."""
    batches = _split_into_batches(texts)
    all_results = []
    token_usage_total = None
    failure_by_index: dict[int, str] = {}
    api_total_count = 0
    api_success_count = 0
    # D-1 A5：格式修复后仍失败、保留原文的句子数（跨批累计）。
    kept_original_count = 0

    for bi, batch in enumerate(batches):
        batch_offset = len(all_results)
        if engine_status_callback:
            engine_status_callback({"engine_category": "api", "phase": "trying_api", "failure_kind": None})
        pct = int((bi + 1) / len(batches) * 95)
        batch_message = f"LLM 逐段翻译… (批 {bi+1}/{len(batches)})"
        if progress_callback:
            progress_callback(pct, batch_message)
        # ⑦.5 R7：这一批已经整批发过的 Key 不再整批重发（换 Key 后不重复花钱）。
        tried_backends = []

        batch_results = [FAILED_TRANSLATION] * len(batch)
        failed_local = set(range(len(batch)))
        failure_local = {idx: "unknown" for idx in failed_local}
        batch_tokens = None

        for backend in llm_backends:
            if not failed_local:
                break
            if backend in tried_backends:
                continue
            empty_streak = 0
            # D-1 C3：连不上 / 余额不足由付费请求函数换 Key、原地等；回来的只剩
            # 成功或 AI 回复格式类失败。不会自己好的错直接抛出。
            untried = [b for b in llm_backends if b not in tried_backends]
            paid = _paid_requester(untried, backend, progress_callback, pct, batch_message)
            backend, initial = paid(lambda b: b.translate_batch(batch))
            tried_backends.append(backend)
            # 合同增量 5：之后的格式修复与逐句重试只用这一家——断网也只原地等它，
            # 不换 Key（P404：同一批不得混进第二个付费厂商的译文）。
            paid = _paid_requester([backend], backend, progress_callback, pct, batch_message)

            batch_tokens = _merge_tokens(batch_tokens, initial.tokens_used)
            failure_kind = getattr(initial, "failure_kind", None)
            if failure_kind:
                for local_idx in failed_local:
                    failure_local[local_idx] = failure_kind
            if failure_kind == "empty_translation":
                empty_streak = 1

            # P404：只有编号协议失败才进分级恢复。进来之后这一批就锁定在当前
            # 付费后端上（下面用 break 而不是 continue），不再试第二个厂商。
            protocol_recovery = False
            if failure_kind == "numbering_format":
                protocol_recovery = True
                # G12 第三批 B（T-3）：验收要数「因条数不对重发」的次数
                _log.info("逐段翻译：这一批 %d 条条数不对，整批重发", len(batch))
                recover_tokens = _recover_paid_batch_protocol_failure(
                    batch, backend, batch_results, failed_local, failure_local,
                    paid=paid,
                )
                batch_tokens = _merge_tokens(batch_tokens, recover_tokens)

            if failure_kind is None:
                for local_idx in list(failed_local):
                    if local_idx >= len(initial.translated):
                        continue
                    candidate = (initial.translated[local_idx] or "").strip()
                    if (
                        is_clean_translation_text(candidate)
                        and _is_valid_translation_for_source(batch[local_idx], candidate)
                    ):
                        batch_results[local_idx] = candidate
                        failed_local.discard(local_idx)
                        failure_local.pop(local_idx, None)
                    elif candidate:
                        failure_local[local_idx] = "quality_rejected"

            if not failed_local:
                break

            context_lines = "\n".join(
                f"[{idx}] {source}" for idx, source in enumerate(batch)
            )
            fused = False
            # P404：走过分级恢复的批次，逐条阶段有硬上限，超出的交给免费引擎。
            # 没走恢复链的失败（quality_rejected、empty_translation 等）仍然逐条全补。
            pending = sorted(failed_local)
            if protocol_recovery:
                pending = pending[:_MAX_PROTOCOL_SINGLE_RETRIES]
            for local_idx in pending:
                prompt = (
                    "以下是本批字幕原文，用于理解语境：\n"
                    f"{context_lines}\n\n"
                    f"{SHARED_TRANSLATION_RULES}\n"
                    f"只翻译第 {local_idx} 条，只输出这一条的译文，"
                    "不要编号、不要分隔符、不要解释。\n"
                    f"原文：{batch[local_idx]}"
                )
                _used, retry = paid(
                    lambda b, _prompt=prompt: b.translate_raw(_prompt, max_tokens=512)
                )
                batch_tokens = _merge_tokens(batch_tokens, retry.tokens_used)

                # 后端理论上该守 list[str] 的契约，但它自己就破过（见 base.py
                # translate_raw）。这里再兜一道：非字符串一律当空，绝不让 .strip()
                # 把整个翻译任务打崩、连免费引擎兜底都跑不到。
                raw_candidate = retry.translated[0] if retry.translated else ""
                candidate = (
                    raw_candidate.strip() if isinstance(raw_candidate, str) else ""
                )
                retry_kind = getattr(retry, "failure_kind", None)
                if not candidate:
                    retry_kind = retry_kind or "empty_translation"
                if retry_kind == "empty_translation":
                    failure_local[local_idx] = retry_kind
                    empty_streak += 1
                    if empty_streak >= 2:
                        _log.debug(
                            "R4 连续空返回熔断: backend=%s idx=%d",
                            getattr(backend, "name", "unknown"),
                            local_idx,
                        )
                        fused = True
                        break
                    continue

                empty_streak = 0

                if (
                    retry_kind is None
                    and is_clean_translation_text(candidate)
                    and _is_valid_translation_for_source(batch[local_idx], candidate)
                ):
                    batch_results[local_idx] = candidate
                    failed_local.discard(local_idx)
                    failure_local.pop(local_idx, None)
                else:
                    failure_local[local_idx] = retry_kind or "quality_rejected"
                    _log.debug(
                        "R3 单条结果拒绝: backend=%s idx=%d kind=%s",
                        getattr(backend, "name", "unknown"),
                        local_idx,
                        retry_kind or "quality_rejected",
                    )

            # P404：一旦进过分级恢复，无论结果如何都锁定当前付费后端 ——
            # break 走出循环（剩下的句子保留原文），绝不 continue 到第二个付费厂商。
            if protocol_recovery:
                break
            if fused:
                continue

        api_total_count += len(batch)
        api_success_indexes = sorted(set(range(len(batch))) - failed_local)
        api_success_count += len(api_success_indexes)
        # D-1 A5：付费路线不再交给免费引擎。格式修复后仍失败的句子保留原文，
        # 以普通结果回报（第 4 个参数 False）——不是「免费补全」，任务也不进「部分完成」。
        kept_local = sorted(failed_local)
        for local_idx in kept_local:
            batch_results[local_idx] = batch[local_idx]
        kept_original_count += len(kept_local)
        if item_result_callback:
            for local_idx in sorted(api_success_indexes + kept_local):
                item_result_callback(
                    batch_offset + local_idx,
                    batch_results[local_idx],
                    True,
                    False,
                )
        if translation_stats_callback:
            translation_stats_callback({
                "api_success_count": api_success_count,
                "api_total_count": api_total_count,
                "kept_original_count": kept_original_count,
            })

        all_results.extend(batch_results)
        token_usage_total = _merge_tokens(token_usage_total, batch_tokens)
        for local_idx in failed_local:
            failure_by_index[batch_offset + local_idx] = failure_local.get(
                local_idx, "unknown"
            )
        if batch_progress_callback:
            batch_progress_callback(
                len(all_results),
                sum(1 for value in all_results if value != FAILED_TRANSLATION),
            )

        if bi < len(batches) - 1:
            time.sleep(1.5)

    final_failed = [i for i, r in enumerate(all_results) if r == "[翻译失败]"]
    llm_names = [b.name for b in llm_backends]
    # D-1 C7：付费成功与保留原文分开数；逐段付费路线免费翻译恒为 0。
    _log.info(
        "LLM 逐段翻译完成: 共 %d 条，付费成功 %d 条，保留原文 %d 条，免费翻译 0 条 (%s)",
        len(texts), api_success_count, kept_original_count, ", ".join(llm_names),
    )
    return all_results, final_failed, token_usage_total, {}, failure_by_index


def _translate_free_only(
    texts: list[str],
    mode: str,
    ordered_backends: list,
    progress_callback=None,
    batch_progress_callback=None,
    item_result_callback=None,
) -> tuple[list[str], list[int], dict | None, dict[int, list[str]], dict[int, str]]:
    """Free backends only (no LLM keys)."""
    all_backends = ordered_backends
    batches = _split_into_batches(texts)
    all_results = []
    all_failed = []
    token_usage_total = None
    used_backends = set()
    failure_by_index: dict[int, str] = {}

    for bi, batch in enumerate(batches):
        if progress_callback:
            pct = int((bi + 1) / len(batches) * 95)
            progress_callback(pct, f"翻译中… (批 {bi + 1}/{len(batches)})")

        if mode == "speed":
            batch_results, backend, _, batch_failures = _run_speed_mode_batch(
                batch, all_backends
            )
            if backend:
                used_backends.add(backend.name)
        else:
            batch_results, backend, failed_indices, _, batch_failures = _run_quality_mode_batch(
                batch, all_backends, context=""
            )
            if backend:
                used_backends.add(backend.name)
            for fi in failed_indices:
                all_failed.append(fi + len(all_results))

        batch_offset = len(all_results)
        for local_idx, failure_kind in batch_failures.items():
            failure_by_index[batch_offset + local_idx] = failure_kind

        all_results.extend(batch_results)
        if item_result_callback:
            for local_idx, value in enumerate(batch_results):
                success = bool(value and value != FAILED_TRANSLATION)
                item_result_callback(
                    batch_offset + local_idx,
                    value if success else None,
                    success,
                    False,
                )
        if batch_progress_callback:
            batch_progress_callback(
                len(all_results),
                sum(1 for value in all_results if value != FAILED_TRANSLATION),
            )
        time.sleep(1)

    final_failed = [
        i for i, r in enumerate(all_results)
        if r == "[翻译失败]" or r is None or r == ""
    ]
    actual_names = list(used_backends) if used_backends else [b.name for b in all_backends]
    _log.info("免费引擎翻译完成: %d/%d 条 (实际使用: %s)", len(texts) - len(final_failed), len(texts), ", ".join(actual_names))

    return (
        all_results,
        final_failed,
        token_usage_total,
        {},
        {idx: failure_by_index.get(idx, "unknown") for idx in final_failed},
    )


def _run_llm_fallback_chain(
    texts: list[str],
    llm_backends: list,
    progress_callback=None,
):
    """LLM fallback chain: try each backend until one produces a result.

    Returns (translated, failed_indices, token_usage, sub_splits, last_llm,
    llm_produced). On total failure, translated is the all-"[翻译失败]" list.
    """
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
    return translated, failed_indices, token_usage, sub_splits, last_llm, llm_produced


def _retry_failed_with_llm(texts, translated, failed_indices, last_llm):
    """P230: retry individual failed segments with a minimal prompt.

    Only when ≤10 segments failed. Mutates translated and failed_indices in place.
    """
    # P230: retry individual failed segments with minimal prompt
    # (no format to get wrong). Only when ≤10 segments failed.
    if failed_indices and last_llm and len(failed_indices) <= 10:
        _log.info("P230 单独重试 %d 条遗漏字幕 (LLM: %s)",
                  len(failed_indices), getattr(last_llm, 'name', 'LLM'))
        for fi in list(failed_indices):
            try:
                simple_prompt = (
                    f"将以下内容翻译成{_TARGET_LANGUAGE}。{SHARED_TRANSLATION_RULES}"
                    f"只输出译文，不要解释。\n\n{texts[fi]}"
                )
                retry = last_llm.translate_raw(simple_prompt, max_tokens=512)
                retry_text = (retry.translated[0] if retry.translated else "").strip()
                if (
                    retry_text
                    and is_clean_translation_text(retry_text)
                    and _is_valid_translation_for_source(texts[fi], retry_text)
                ):
                    translated[fi] = retry_text
                    failed_indices.remove(fi)
                else:
                    _log.debug(
                        "P230 单条结果拒绝: idx=%d kind=%s",
                        fi,
                        "quality_rejected",
                    )
            except Exception:
                pass  # fall through to free backend retry below


# G8：免费补全进度。前缀是**结构化标记**，不是给用户看的文字——worker 靠它认出
# 「这条是免费补全进度」，然后换上自己那条路的标签（自动兜底 / 人工重试）。
# orchestrator 不知道也不需要知道自己在哪条路上。
FREE_FILL_PREFIX = "免费补全｜"


def _free_fill_text(total, processed, success, failed, *, waiting):
    """G8/C4：五数字文案的**唯一作者**。两条路共用，改一次两条路一起变。

    口径 = 「本次运行的全部句子」，不是「免费兜底那批」：
      总数 = len(texts)；已处理 = API 成功数 + 免费已结算数；剩余 = 总数 - 已处理。
    恒等式 `已处理 == 成功 + 失败` 由调用方的累加保证，
    `总数 == 已处理 + 剩余` 在这里由 remaining 的定义保证——不可能算错。

    合同 A16：只输出数字和固定词，绝不带字幕原文或译文。
    """
    remaining = total - processed
    tail = f"成功 {success} 句，失败 {failed} 句，还剩 {remaining} 句。"
    if waiting:
        return (f"{FREE_FILL_PREFIX}正在等待免费翻译返回第 {processed + 1}/{total} 句。"
                f"已完成 {processed} 句，{tail}")
    return f"{FREE_FILL_PREFIX}已处理 {processed}/{total} 句，{tail}"


def _make_free_fill_reporter(progress_callback, pct_of):
    """G8/A13：把「发一条免费补全进度」包成一个不会掀桌子的函数。

    浏览器关掉后写管道会抛 BrokenPipeError。**这个异常绝不能冒到补全循环里**——
    否则剩下的句子一句都补不到，用户拿到的是缺一半的字幕。
    把「页面不更新」换成「字幕少一半」是拿新病换旧病，⑤ 走查 Q6 抓到的就是这个。
    """
    def report(total, processed, success, failed, *, waiting):
        if not progress_callback:
            return
        try:
            progress_callback(
                pct_of(processed),
                _free_fill_text(total, processed, success, failed, waiting=waiting),
            )
        except (BrokenPipeError, OSError):
            pass
    return report


def _fill_gaps_with_free(
    texts,
    translated,
    failed_indices,
    ordered_backends,
    progress_callback,
    item_result_callback=None,
):
    """Fill remaining failed segments with free backends. Mutates translated in place.

    G8（P378/P379）：**在向引擎发请求之前**先报告一次「正在等待第 k/N 句」，
    结果出来后再报告一次五个数字。以前只有后者——而一句最坏要等 3 个引擎 × 30 秒，
    那 90 秒里页面一个字都不变，用户分不清是在跑还是卡死了。
    进度事件的产生点绑在「结果出来之后」，等待期天然没有事件，这是病根。

    没有节流：用户要的就是每句都动一下。事件源修好之后，节流只会把它挡回去。
    没有免费引擎时不发「正在等待」——没有东西可等，那句话是假的。
    """
    failure_by_index: dict[int, str] = {}
    fallback_indexes = list(failed_indices)
    free_total = len(fallback_indexes)
    if not free_total:
        return failure_by_index

    total = len(texts)
    api_settled = total - free_total          # 进入免费兜底时 API 已经成功的句数
    processed = api_settled
    success = api_settled
    failed = 0

    report = _make_free_fill_reporter(
        progress_callback,
        lambda done: 96 + int(4 * (done - api_settled) / free_total),
    )

    disabled: set[str] = set()

    for idx in fallback_indexes:
        if ordered_backends:
            report(total, processed, success, failed, waiting=True)
        ok = False
        if ordered_backends:
            for backend in ordered_backends:
                name = getattr(backend, "name", "unknown")
                if name in disabled:
                    continue
                try:
                    result = backend.translate_batch([texts[idx]])
                    candidate = (result.translated[0] if result.translated else "").strip()
                    kind = getattr(result, "failure_kind", None)
                except Exception as error:
                    candidate = ""
                    kind = classify_translation_failure(error)
                if not candidate:
                    kind = kind or "empty_translation"
                if (
                    kind is None
                    and is_clean_translation_text(candidate)
                    and _is_valid_translation_for_source(texts[idx], candidate)
                ):
                    translated[idx] = candidate
                    failed_indices.remove(idx)
                    failure_by_index.pop(idx, None)
                    ok = True
                    break
                kind = kind or "quality_rejected"
                failure_by_index[idx] = kind
                if kind in _IMMEDIATE_LLM_FUSE_FAILURES:
                    disabled.add(name)
                time.sleep(0.05)
        # 每个原始失败项恰好结算一次（含"无免费引擎"边界：ordered_backends 为空则 ok=False）
        processed += 1
        if ok:
            success += 1
        else:
            failed += 1
        if item_result_callback:
            item_result_callback(idx, translated[idx] if ok else None, ok, True)
        # 落盘必须在报告之前：报告里的数字要和 worker 记的账是同一时刻的
        report(total, processed, success, failed, waiting=False)
    return failure_by_index


def _translate_llm_context_aware(
    texts: list[str],
    llm_backends: list,
    ordered_backends: list,
    progress_callback=None,
    item_result_callback=None,
    translation_stats_callback=None,
) -> tuple[list[str], list[int], dict | None, dict[int, list[str]]]:
    """Full-context LLM translation: fallback chain → LLM retry → free-backend gap fill."""
    translated, failed_indices, token_usage, sub_splits, last_llm, llm_produced = \
        _run_llm_fallback_chain(texts, llm_backends, progress_callback)
    _retry_failed_with_llm(texts, translated, failed_indices, last_llm)
    api_success_indexes = [
        idx for idx, value in enumerate(translated)
        if idx not in failed_indices and value != FAILED_TRANSLATION
    ]
    if item_result_callback:
        for idx in api_success_indexes:
            item_result_callback(idx, translated[idx], True, False)
    if translation_stats_callback:
        translation_stats_callback({
            "api_success_count": len(api_success_indexes),
            "api_total_count": len(texts),
        })
    failure_by_index = _fill_gaps_with_free(
        texts,
        translated,
        failed_indices,
        ordered_backends,
        progress_callback,
        item_result_callback,
    )

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
    for idx in final_failed:
        failure_by_index.setdefault(idx, "unknown")
    return translated, final_failed, token_usage, sub_splits, failure_by_index


# ── Main entry point (T1: tier removed) ──

def translate_with_orchestrator(
    texts: list[str],
    mode: str = "speed",
    priority: list[str] | None = None,
    api_keys: list[dict] | None = None,
    context_aware: bool = False,
    progress_callback=None,
    batch_progress_callback=None,
    engine_status_callback=None,
    item_result_callback=None,
    translation_stats_callback=None,
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
            result = _translate_llm_context_aware(
                texts,
                llm_backends,
                ordered_backends,
                progress_callback,
                item_result_callback,
                translation_stats_callback,
            )
            if batch_progress_callback:
                batch_progress_callback(
                    len(texts),
                    sum(1 for value in result[0] if value != FAILED_TRANSLATION),
                )
            return result

        else:
            return _translate_llm_per_batch(
                texts,
                llm_backends,
                ordered_backends,
                progress_callback,
                batch_progress_callback,
                engine_status_callback,
                item_result_callback,
                translation_stats_callback,
            )

    if engine_status_callback:
        engine_status_callback({"engine_category": "free", "phase": "free_running", "failure_kind": None})
    return _translate_free_only(
        texts,
        mode,
        ordered_backends,
        progress_callback,
        batch_progress_callback,
        item_result_callback,
    )
