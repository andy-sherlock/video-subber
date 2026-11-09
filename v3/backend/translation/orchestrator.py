"""V3 Translation orchestrator.

T1: Tier system removed — LLM access gated by whether user provides API keys.
P500: 逐段与上下文感知共用同一个小批函数；上下文感知每批多带全片摘要、人名表、原片邻句。
"""
import time, re, logging
from dataclasses import dataclass
from .base import (
    TranslationBackend,
    TranslationResult,
    FAILED_TRANSLATION,
    SHARED_TRANSLATION_RULES,
    _is_unchanged_pure_proper_name,
    is_clean_translation_text,
)
from .paid_request import PaidAIUnavailable, call_paid, wait_message

_log = logging.getLogger(__name__)



# P404：编号协议失败后，在同一付费后端上做整批 → 两半 → 逐条的分级恢复。
# 只有 numbering_format 会触发；连接/鉴权/限流等由付费请求层（paid_request）先处理，
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


# ── P500：上下文感知的参考材料（全片摘要 + 人名表 + 原片邻句）──

_REFERENCE_LINES_BEFORE = 3
_REFERENCE_LINES_AFTER = 2


@dataclass(frozen=True)
class FilmReference:
    """一部片的参考材料。现算、不落盘（方案第三节「状态份数」）。

    full_texts 是整片原文；positions 是本次要翻的每一句在整片里的位置，
    用来取原片里真正相邻的句子（中断后接着翻、人工重试时，要翻的只是剩下的句子）。
    """
    summary: str
    terms: dict
    full_texts: list
    positions: list


def _extract_terminology_and_summary(
    texts: list[str],
    llm,
) -> tuple[dict[str, str], str]:
    """Single LLM call: extract terminology AND generate context summary.

    Both tasks share the same input text — merging saves one network round-trip.
    On partial failure: returns what it can, caller handles fallback.
    """
    # P500 U7：读全片，一次调用，不分段、不合并（原来只读前 500 条）。
    joined = "\n".join(t for t in texts if t.strip())
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
            if getattr(result, "failure_kind", None):
                # translate_raw 自己接住异常、只交回失败类别（超时、限流、余额不足……），
                # 下面的 except 进不去；原因要在这里写进日志（/review 发现）。
                _log.warning("通读全片失败 (LLM: %s, 原因: %s)",
                             getattr(llm, 'name', 'unknown'), result.failure_kind)
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

    # 一行一条「原文 → 译文」。只按第一个箭头切开：原来按「- → >」任一字符切，
    # ② 基线真实回答「e-bike → 电动自行车」被拆成原文 'e'（/review 发现）。
    terms = {}
    for line in (terms_section or "").split("\n"):
        line = re.sub(r"^[\-*•\s]+", "", line.strip()).replace("**", "")
        if not line or line == "无":
            continue
        m = re.match(r'(.+?)\s*(?:→|->|>)\s*(.+)', line)
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


def _film_reference(full_texts, positions, llm_backends, progress_callback=None):
    """按用户的 Key 顺序请 AI 通读全片，写摘要和人名表。

    P165：摘要没有中文 → 判不可靠，换下一家。
    U2：全部失败 → 返回 None，这次不带参考翻译（等于逐段），任务卡不写，日志记一行。
    """
    reasons = []
    for llm in llm_backends:
        name = getattr(llm, "name", "LLM")
        if progress_callback:
            progress_callback(0, f"正在用 {name} 通读全片…")
        terms, summary = _extract_terminology_and_summary(full_texts, llm)
        if not summary:
            reasons.append(f"{name} 没写出摘要")
            continue
        if not _is_valid_translation(summary):
            reasons.append(f"{name} 的摘要没有中文")
            continue
        return FilmReference(
            summary=summary,
            terms=terms,
            full_texts=list(full_texts),
            positions=list(positions),
        )
    _log.warning(
        "上下文感知：全片摘要没写成（%s），这次不带参考翻译",
        "；".join(reasons) or "没有可用的 AI",
    )
    return None


def _neighbor_sections(full_texts, batch_positions, batch) -> list[str]:
    """按「在原片里连着的」把本批切成几段，每段各取原片前 3 句、后 2 句，标明属于第几条。

    第一次翻译时一批就是一整段；中断后接着翻、人工重试时，要翻的句子可能零散在片子各处，
    每段都拿自己真正的邻居（U6，用户 2026-11-09 定）。本批自己要翻的句子不重复放进邻句。
    标号与 translate_batch 发给 AI 的编号一致（它只给非空句编号）。
    """
    visible, number = {}, 0
    for index, text in enumerate(batch):
        if text.strip():
            visible[index] = number
            number += 1
    in_batch = set(batch_positions)
    runs = []
    for index, position in enumerate(batch_positions):
        if runs and position == batch_positions[runs[-1][-1]] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    lines = []
    for run in runs:
        shown = [visible[index] for index in run if index in visible]
        if not shown:
            continue
        label = f"[{shown[0]}]" if shown[0] == shown[-1] else f"[{shown[0]}]–[{shown[-1]}]"
        first, last = batch_positions[run[0]], batch_positions[run[-1]]
        before = [
            full_texts[p]
            for p in range(max(0, first - _REFERENCE_LINES_BEFORE), first)
            if p not in in_batch
        ]
        after = [
            full_texts[p]
            for p in range(last + 1, min(len(full_texts), last + 1 + _REFERENCE_LINES_AFTER))
            if p not in in_batch
        ]
        if before:
            lines += [f"【第 {label} 条在原片里的前文】", *before]
        if after:
            lines += [f"【第 {label} 条在原片里的后文】", *after]
    return lines


def _reference_text_for_batch(reference: FilmReference, batch_positions, batch) -> str:
    """一批的参考文字：摘要 + 本批出现的人名 + 每段在原片里的前 3 句、后 2 句。

    同一批的首发、整批 / 半批重发、单句重试都用这同一份文字（外审#4）。
    """
    lines = [
        "以下是参考材料，只读参考，不要翻译，也不要输出：",
        f"【内容摘要】{reference.summary}",
    ]
    batch_text = "\n".join(batch).lower()
    # 按整词找：真实人名表里有「Era → 艾拉」，camera、several 里也含 era（/review 发现）。
    terms = [
        f"{source} → {target}"
        for source, target in reference.terms.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(source.lower())}(?![a-z0-9])", batch_text)
    ]
    if terms:
        lines += ["【人名与术语，请统一用这些译法】", *terms]
    lines += _neighbor_sections(reference.full_texts, batch_positions, batch)
    return "\n".join(lines)



_TRANSLATION_CJK_MIN_RATIO = 0.1  # 10% CJK characters — below this, treat as untranslated
_SHORT_LINE_MAX_CHARS = 15  # P230: lines shorter than this get relaxed CJK ratio
_SHORT_LINE_CJK_MIN_RATIO = 0.05  # P230: 5% for short lines (often contain English names)

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
    batch, backend, results, failed_local, failure_local, *, paid, reference=None
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
        # P500 外审#4：带参考时，参考与纠正提示拼在一起，两样都不丢；半批沿用原批那份。
        context = (
            _PAID_BATCH_RETRY_CORRECTION if reference is None
            else f"{reference}\n\n{_PAID_BATCH_RETRY_CORRECTION}"
        )
        _used, result = paid(
            lambda b: b.translate_batch(sub_batch, context=context)
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
    progress_callback=None,
    batch_progress_callback=None,
    engine_status_callback=None,
    item_result_callback=None,
    translation_stats_callback=None,
    reference=None,
) -> tuple[
    list[str],
    list[int],
    dict | None,
    dict[int, list[str]],
    dict[int, str],
]:
    """Per-batch LLM translation (no full context).

    P500：`reference`（FilmReference，上下文感知才有）为 None 时行为与改动前一字不变
    （test_p500_step3_per_batch_reference.py 用改动前录下的请求文字比对）。
    有参考时，每批的首发、整批 / 半批重发、单句重试都带同一份参考文字。
    """
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
        reference_text = (
            _reference_text_for_batch(
                reference,
                reference.positions[batch_offset:batch_offset + len(batch)],
                batch,
            )
            if reference is not None else None
        )

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
            backend, initial = paid(
                (lambda b: b.translate_batch(batch)) if reference_text is None
                else (lambda b: b.translate_batch(batch, context=reference_text))
            )
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
                    paid=paid, reference=reference_text,
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
            # P404：走过分级恢复的批次，逐条阶段有硬上限，超出的保留原文。
            # 没走恢复链的失败（quality_rejected、empty_translation 等）仍然逐条全补。
            pending = sorted(failed_local)
            if protocol_recovery:
                pending = pending[:_MAX_PROTOCOL_SINGLE_RETRIES]
            for local_idx in pending:
                prompt = (
                    (f"{reference_text}\n\n" if reference_text is not None else "")
                    + "以下是本批字幕原文，用于理解语境：\n"
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
                # 把整个翻译任务打崩。
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
        # D-1 A5：格式修复后仍失败的句子保留原文，以普通结果回报（第 4 个参数 False），
        # 任务不进「部分完成」。
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
    # D-1 C7：付费成功与保留原文分开数。
    _log.info(
        "LLM 逐段翻译完成: 共 %d 条，付费成功 %d 条，保留原文 %d 条 (%s)",
        len(texts), api_success_count, kept_original_count, ", ".join(llm_names),
    )
    return all_results, final_failed, token_usage_total, {}, failure_by_index


def _translate_llm_context_aware(
    texts: list[str],
    llm_backends: list,
    progress_callback=None,
    batch_progress_callback=None,
    engine_status_callback=None,
    item_result_callback=None,
    translation_stats_callback=None,
    *,
    full_texts=None,
    positions=None,
):
    """P500：上下文感知 = 先通读全片写摘要和人名表，再交给逐段同一个小批函数，
    每批带同一份参考。句数核对、分级重发、逐句中文闸、失败句保留原文全部共用逐段那一份，
    「只有一条路有核对」在代码里写不出来（方案第三节，第 1 层防护）。

    full_texts / positions 由 translate_with_orchestrator 核对过再交进来（施工 ④）。
    """
    if full_texts is None:
        full_texts, positions = list(texts), list(range(len(texts)))
    reference = _film_reference(full_texts, positions, llm_backends, progress_callback)
    return _translate_llm_per_batch(
        texts,
        llm_backends,
        progress_callback,
        batch_progress_callback,
        engine_status_callback,
        item_result_callback,
        translation_stats_callback,
        reference=reference,
    )



def _checked_film_positions(texts, full_texts, positions):
    """P500：核对「要翻的句子」和「整片里的位置」对得上；都不给时按 texts 自身。

    对不上就意味着施工 ⑤ 取邻句会取错——直接报错，不带着错的参考去花钱。
    """
    if full_texts is None and positions is None:
        return list(texts), list(range(len(texts)))
    if full_texts is None or positions is None:
        raise ValueError("full_texts 与 positions 必须同时给出")
    if len(positions) != len(texts):
        raise ValueError("positions 与 texts 句数不一致")
    for text, position in zip(texts, positions):
        if not (0 <= position < len(full_texts)) or full_texts[position] != text:
            raise ValueError(f"第 {position} 句的位置与整片原文对不上")
    return list(full_texts), list(positions)


# ── Main entry point (T1: tier removed) ──

def translate_with_orchestrator(
    texts: list[str],
    api_keys: list[dict] | None = None,
    context_aware: bool = False,
    progress_callback=None,
    batch_progress_callback=None,
    engine_status_callback=None,
    item_result_callback=None,
    translation_stats_callback=None,
    full_texts: list[str] | None = None,
    positions: list[int] | None = None,
) -> tuple[list[str], list[int], dict | None, dict[int, list[str]]]:
    """Main entry point.

    Args:
        texts: Source texts to translate.
        api_keys: List of LLM key configs.
        context_aware: True 时先通读全片写摘要和人名表，再按逐段同样的小批翻，
                       每批带参考（P500）。
        progress_callback: fn(pct: int, message: str).
        full_texts / positions: P500（外审#3，U6）。只给上下文感知用：整片原文，以及 texts
                       每句在整片里的位置（中断后接着翻 / 人工重试时 texts 只是剩下的句子）。
                       都不给 = 按 texts 自身。逐段不收不用。

    Returns:
        (translated_texts, failed_indices, token_usage_or_none, sub_splits)
        sub_splits: {original_idx: [split_part_1, ...]} from LLM auto-splitting,
        or empty dict if no LLM or no splitting was done.
    """
    llm_backends = _build_llm_backends(api_keys or [])

    # ── LLM path: user provided API keys ──
    if llm_backends:
        if context_aware:
            full_texts, positions = _checked_film_positions(texts, full_texts, positions)
            return _translate_llm_context_aware(
                texts,
                llm_backends,
                progress_callback,
                batch_progress_callback,
                engine_status_callback,
                item_result_callback,
                translation_stats_callback,
                full_texts=full_texts,
                positions=positions,
            )

        else:
            return _translate_llm_per_batch(
                texts,
                llm_backends,
                progress_callback,
                batch_progress_callback,
                engine_status_callback,
                item_result_callback,
                translation_stats_callback,
            )

    # D-6 P503：翻译只用付费 AI。「选了翻译、真要翻、却没有 Key」只会是旧前端、恢复等绕过了
    # 门口检测——明确停下，绝不产出一份没翻译的「翻译成品」。导出（本轮故意不带 Key）
    # 只读翻译缓存、不调用这里，不受影响。
    raise PaidAIUnavailable(
        "no_key",
        "还没有填写 API Key：翻译要用你自己的 Key。请到设置里填好 Key（推荐 DeepSeek）后重试。"
    )
