"""#7 Translation reflection — LLM reviews and corrects its own translations."""
import re
import logging

from .base import is_clean_translation_text

_log = logging.getLogger(__name__)

_REFLECTION_RE = re.compile(r"^\[(\d+)\]\s*(.+)$")


def build_reflection_prompt(
    texts: list[str], translated: list[str], limit: int = 30
) -> str:
    """Build a prompt asking the LLM to review and correct translations."""
    lines = []
    for i, (src, tgt) in enumerate(zip(texts, translated)):
        if i >= limit:
            break
        lines.append(f"[{i}] {src} → {tgt}")
    return (
        "请逐条审查以下翻译，挑出翻译不准确、不通顺、不符合语境的条目。\n"
        "对每条有问题的翻译输出：[编号] 修正后的译文\n"
        "如果某条不需要修正，不要输出该条。\n\n"
        + "\n".join(lines)
    )


def parse_reflection_result(
    raw: str, source_texts: list[str] | None = None
) -> dict[int, str]:
    """Parse reflection output into {index: corrected_text}."""
    if not raw or not raw.strip():
        return {}
    from .orchestrator import _is_valid_translation_for_source

    corrections = {}
    seen: set[int] = set()
    for line in raw.strip().split("\n"):
        m = _REFLECTION_RE.match(line.strip())
        if m:
            idx = int(m.group(1))
            text = m.group(2).strip()
            if idx in seen:
                _log.debug("反思建议拒绝: idx=%d kind=duplicate_number", idx)
                return {}
            seen.add(idx)
            if source_texts is not None and not 0 <= idx < len(source_texts):
                _log.debug("反思建议拒绝: idx=%d kind=outside_range", idx)
                continue
            if not is_clean_translation_text(text):
                _log.debug("反思建议拒绝: idx=%d kind=quality_rejected", idx)
                continue
            source = source_texts[idx] if source_texts is not None else ""
            if not _is_valid_translation_for_source(source, text):
                _log.debug("反思建议拒绝: idx=%d kind=target_language", idx)
                continue
            corrections[idx] = text
    return corrections


def apply_reflection(
    translated: list[str], corrections: dict[int, str] | None
) -> list[str]:
    """Apply corrections to the translated list. Returns new list."""
    if not corrections:
        return translated[:]
    result = translated[:]
    for idx, text in corrections.items():
        if 0 <= idx < len(result):
            result[idx] = text
    return result
