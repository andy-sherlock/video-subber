"""P500 施工 ⑤：上下文感知改成「先读全片写摘要和人名表 → 用逐段同一个小批函数翻，每批带同一份参考」。

方案 `pipelines/方案-P500-上下文感知改小批-2026-11-07.md` 第五～七节；合同表见管道 ⑥.16（K1–K12）。
假 AI 按请求内容作答，并记下收到的每一句完整请求文字。
"""

import re

import pytest

from backend.translation import base as translation_base
from backend.translation import orchestrator as orch

_BATCH_MARKER = "仅按 [编号] 译文 格式输出"
_SUMMARY_MARKER = "## 内容摘要"
_SINGLE_MARKER = "只翻译第 "

GOOD_SUMMARY = (
    "## 术语表\nSarah → 莎拉\nQueenstown → 皇后镇\nPookie → 布奇\n"
    "## 内容摘要\n一段新西兰皇后镇的旅行视频，朋友之间的口语对话。"
)


def _to_translate(prompt):
    """批请求里真正要翻的行（「[编号] 原文」），参考材料里不会出现这种行。"""
    return re.findall(r"^\[(\d+)\] (.+)$", prompt.split("你是专业视频字幕翻译", 1)[1], re.M)


class _FakeLLM(translation_base.LLMBackend):
    def __init__(self, name="假AI", *, summary=GOOD_SUMMARY, batch=None, single=None):
        self._name = name
        self._summary = summary
        self._batch = batch      # fn(prompt, call_no) -> 回答；默认逐行回「译文N」
        self._single = single    # fn(prompt) -> 回答；默认「单句译文」
        self.prompts = []

    @property
    def name(self):
        return self._name

    @property
    def _api_url(self):
        return "https://example.invalid/v1/chat/completions"

    def _build_headers(self):
        return {"Content-Type": "application/json"}

    def _call_api_parse(self, response_data):
        return ""

    def _call_api_raw(self, prompt, max_tokens=8192):
        self.prompts.append(prompt)
        usage = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        if _SUMMARY_MARKER in prompt and _BATCH_MARKER not in prompt:
            answer = self._summary(prompt) if callable(self._summary) else self._summary
            if isinstance(answer, Exception):
                raise answer
            return answer, usage
        if _BATCH_MARKER in prompt:
            n = sum(1 for p in self.prompts if _BATCH_MARKER in p)
            if self._batch:
                return self._batch(prompt, n), usage
            return "\n".join(f"[{i}] 译文{i}号" for i, _ in _to_translate(prompt)), usage
        return (self._single(prompt) if self._single else "单句的中文译文"), usage

    def batch_prompts(self):
        return [p for p in self.prompts if _BATCH_MARKER in p]

    def summary_prompts(self):
        return [p for p in self.prompts if _SUMMARY_MARKER in p and _BATCH_MARKER not in p]

    def single_prompts(self):
        return [p for p in self.prompts if _BATCH_MARKER not in p and _SUMMARY_MARKER not in p]


FILM = [f"Line number {i} of the film." for i in range(12)]
FILM[3] = "My friend Sarah is very nervous."
FILM[7] = "Welcome to Queenstown, everyone."


def _run(backends, texts=None, positions=None, full_texts=FILM, monkeypatch=None):
    monkeypatch.setattr(orch.time, "sleep", lambda *_a: None)
    texts = list(full_texts) if texts is None else texts
    if positions is None:
        positions = list(range(len(texts)))
    return orch._translate_llm_context_aware(
        texts, backends, lambda *a: None, full_texts=full_texts, positions=positions,
    )


def _reference_of(prompt):
    """从批请求里取出参考材料那一段（「语境: 」到翻译指令之前）。"""
    assert prompt.startswith("语境: "), "批请求没有带参考"
    return prompt[len("语境: "):prompt.index("你是专业视频字幕翻译")]


# ── K1 摘要读全片、一次调用 ──────────────────────────────────────────────

def test_summary_reads_whole_film_in_one_call(monkeypatch):
    film = [f"Sentence {i} here." for i in range(600)]
    llm = _FakeLLM()
    _run([llm], full_texts=film, monkeypatch=monkeypatch)
    assert len(llm.summary_prompts()) == 1
    assert "Sentence 599 here." in llm.summary_prompts()[0], "第 500 句之后也要读"


def test_summary_reads_whole_film_even_when_retrying_a_few(monkeypatch):
    llm = _FakeLLM()
    _run([llm], texts=[FILM[2], FILM[9]], positions=[2, 9], monkeypatch=monkeypatch)
    assert all(line in llm.summary_prompts()[0] for line in FILM), "人工重试时摘要也读整片"


# ── K2 摘要失败 ────────────────────────────────────────────────────────

def test_english_summary_moves_to_next_backend(monkeypatch):
    first = _FakeLLM("第一家", summary="## 术语表\n无\n## 内容摘要\nA travel video about New Zealand.")
    second = _FakeLLM("第二家")
    _run([first, second], monkeypatch=monkeypatch)
    assert len(second.summary_prompts()) == 1
    assert "皇后镇的旅行视频" in _reference_of(first.batch_prompts()[0]), \
        "翻译仍按用户顺序从第一家开始，带的是第二家写的摘要"


def test_all_summaries_fail_translates_like_per_batch(monkeypatch):
    llm = _FakeLLM(summary=RuntimeError("断了"))
    plain = _FakeLLM(summary=RuntimeError("断了"))
    _run([llm], monkeypatch=monkeypatch)
    monkeypatch.setattr(orch.time, "sleep", lambda *_a: None)
    orch._translate_llm_per_batch(list(FILM), [plain], progress_callback=lambda *a: None)
    assert llm.batch_prompts() == plain.batch_prompts(), "摘要全失败 = 不带参考，与逐段一字不差"


# ── K3 参考的内容 ──────────────────────────────────────────────────────

def _sections(ref):
    """参考文字按「【标题】」切成 {标题: [原文行]}。"""
    out, title = {}, None
    for line in ref.strip().split("\n"):
        if line.startswith("【") and "】" in line:
            title = line[1:line.index("】")]
            out[title] = []
        elif title is not None:
            out[title].append(line)
    return out


def test_reference_has_summary_relevant_terms_and_true_neighbors(monkeypatch):
    """第一次翻译：一批是连着的一整段，标明是第几条的前文 / 后文。"""
    llm = _FakeLLM()
    _run([llm], texts=[FILM[3], FILM[4]], positions=[3, 4], monkeypatch=monkeypatch)
    ref = _reference_of(llm.batch_prompts()[0])
    assert "皇后镇的旅行视频" in ref
    assert "Sarah → 莎拉" in ref
    assert "Queenstown" not in ref and "Pookie" not in ref, "只带本批出现的人名"
    sec = _sections(ref)
    assert sec["第 [0]–[1] 条在原片里的前文"] == [FILM[0], FILM[1], FILM[2]]
    assert sec["第 [0]–[1] 条在原片里的后文"] == [FILM[5], FILM[6]]
    assert "只读" in ref and "不要翻译" in ref


def test_scattered_lines_each_get_their_own_neighbors(monkeypatch):
    """人工重试只剩第 2、9 句、同一批：每句各拿自己在原片里的前后文，分开标明。"""
    llm = _FakeLLM()
    _run([llm], texts=[FILM[2], FILM[9]], positions=[2, 9], monkeypatch=monkeypatch)
    sec = _sections(_reference_of(llm.batch_prompts()[0]))
    assert sec["第 [0] 条在原片里的前文"] == [FILM[0], FILM[1]]
    assert sec["第 [0] 条在原片里的后文"] == [FILM[3], FILM[4]]
    assert sec["第 [1] 条在原片里的前文"] == [FILM[6], FILM[7], FILM[8]]
    assert sec["第 [1] 条在原片里的后文"] == [FILM[10], FILM[11]]


def test_neighbors_skip_lines_that_are_in_this_batch(monkeypatch):
    """第 2、4 句都要重翻：第 2 句的后文只给第 3 句，不重复放第 4 句（它已在要翻的那一栏）。"""
    llm = _FakeLLM()
    _run([llm], texts=[FILM[2], FILM[4], FILM[5]], positions=[2, 4, 5], monkeypatch=monkeypatch)
    sec = _sections(_reference_of(llm.batch_prompts()[0]))
    assert sec["第 [0] 条在原片里的后文"] == [FILM[3]]
    assert sec["第 [1]–[2] 条在原片里的前文"] == [FILM[1], FILM[3]]
    assert sec["第 [1]–[2] 条在原片里的后文"] == [FILM[6], FILM[7]]


def test_reference_never_attaches_whole_film(monkeypatch):
    """⑦.1 用户裁决：C+（每批再附整片原文）已删。参考文字只有摘要、人名、邻句，
    没有「整片原文」段；内部开关和字段都不存在，实验脚本也打不开它。"""
    llm = _FakeLLM()
    _run([llm], texts=[FILM[3]], positions=[3], monkeypatch=monkeypatch)
    ref = _reference_of(llm.batch_prompts()[0])
    assert "整片原文" not in ref
    assert FILM[11] not in ref
    assert not hasattr(orch, "_ATTACH_FULL_SOURCE")
    assert "full_source" not in orch.FilmReference.__dataclass_fields__


# ── K4–K7 每次请求都带同一份参考；错位被抓 ───────────────────────────────

def _misnumbered_then_ok(prompt, n):
    lines = _to_translate(prompt)
    if n <= 2:  # 首发和整批纠正：多回一行（P483 的形状）
        return "\n".join(f"[{i}] 译文{i}号" for i in range(len(lines) + 1))
    if n == 3:  # 前半批：对
        return "\n".join(f"[{i}] 译文{i}号" for i, _ in lines)
    return "[0] 只有一行"  # 后半批：仍不对 → 逐句


def test_every_request_carries_the_same_reference(monkeypatch):
    llm = _FakeLLM(batch=_misnumbered_then_ok)
    texts = FILM[2:6]
    translated, failed, *_ = _run([llm], texts=texts, positions=[2, 3, 4, 5], monkeypatch=monkeypatch)

    batches = llm.batch_prompts()
    assert len(batches) == 4, "首发 + 整批纠正 + 两个半批"
    first_ref = _reference_of(batches[0])
    for retry in batches[1:]:
        assert _reference_of(retry).startswith(first_ref), "重发沿用同一份参考"
        assert orch._PAID_BATCH_RETRY_CORRECTION in retry, "纠正提示也在"
    singles = llm.single_prompts()
    assert len(singles) == 2
    for single in singles:
        assert first_ref.strip() in single, "单句重试带同一份参考"
    assert failed == []
    assert translated[:2] == ["译文0号", "译文1号"]
    assert translated[2:] == ["单句的中文译文", "单句的中文译文"]


def test_extra_lines_are_caught_not_silently_shifted(monkeypatch):
    """P483：AI 多回一行，旧上下文感知报「失败 0」整片错位；现在走句数核对和纠正。"""
    def always_extra(prompt, n):
        lines = _to_translate(prompt)
        return "\n".join(f"[{i}] 译文{i}号" for i in range(len(lines) + 1))

    llm = _FakeLLM(batch=always_extra)
    translated, failed, *_ = _run([llm], texts=FILM[:4], positions=[0, 1, 2, 3], monkeypatch=monkeypatch)
    assert len(llm.batch_prompts()) >= 2, "条数不对必须重发纠正"
    assert translated == ["单句的中文译文"] * 4, "最后靠逐句翻，句句对号"


# ── K8 仍失败保留原文 ─────────────────────────────────────────────────

def test_still_failed_lines_keep_original(monkeypatch):
    llm = _FakeLLM(
        batch=lambda prompt, n: "\n".join(f"[{i}] {s}" for i, s in _to_translate(prompt)),
        single=lambda prompt: "Still English only",
    )
    translated, failed, *_ = _run([llm], texts=FILM[:3], positions=[0, 1, 2], monkeypatch=monkeypatch)
    assert translated == FILM[:3]
    assert failed == []


# ── K10 删除与保留 ───────────────────────────────────────────────────

DELETED = [
    "_OVERLAP_RATIO", "_compute_chunk_size", "_SEAM_CONTEXT", "_INDEXED_LINE_RE",
    "_SEAM_REVIEW_COMMENT_MARKERS", "_looks_like_seam_review_comment",
    "_generate_context_summary", "_review_seam", "_INDEXED_LINE_PATTERNS",
    "_translate_one_chunk", "_initialize_whole_translation", "_build_overlapping_chunks",
    "_build_whole_translation_prompt_prefix", "_translate_chunks_with_prompt",
    "_review_translation_seams", "_finalize_whole_translation",
    "_translate_whole_with_markers", "_run_llm_fallback_chain", "_retry_failed_with_llm",
    "extract_terminology", "_SUBTITLE_PROMPT",
]


@pytest.mark.parametrize("name", DELETED)
def test_whole_film_code_is_gone(name):
    assert not hasattr(orch, name)


def test_kept_gates_still_importable_by_sentence_splitter():
    # sentence_splitter.py:437 在函数里这样导入
    from backend.translation.orchestrator import _is_valid_translation
    assert _is_valid_translation("这是一句中文") is True
    assert orch._is_valid_translation_for_source("Hi", "你好") is True
    assert orch._TRANSLATION_CJK_MIN_RATIO == 0.1


def test_terms_match_whole_words_only(monkeypatch):
    """真实人名表（② 基线 O-30分钟对话）里有「Era → 艾拉」：camera、several 里也含 era，不能被当成本批出现了这个人。"""
    summary = "## 术语表\nEra → 艾拉\n## 内容摘要\n两个朋友聊天的视频，口语对话。"
    film = ["I bought a new camera.", "Era is my best friend.", "We met several times."]
    llm = _FakeLLM(summary=summary)
    _run([llm], texts=[film[0]], positions=[0], full_texts=film, monkeypatch=monkeypatch)
    assert "Era → 艾拉" not in _reference_of(llm.batch_prompts()[0])
    llm2 = _FakeLLM(summary=summary)
    _run([llm2], texts=[film[1]], positions=[1], full_texts=film, monkeypatch=monkeypatch)
    assert "Era → 艾拉" in _reference_of(llm2.batch_prompts()[0])


def test_name_table_parsing_keeps_hyphenated_terms():
    """② 基线新西兰两次真实回答里都有「e-bike → 电动自行车」，旧解析拆成原文 'e'。"""
    class _R:
        def translate_raw(self, prompt, max_tokens=4096):
            return translation_base.TranslationResult(
                translated=["## 术语表\ne-bike → 电动自行车\nJean-Luc -> 让-吕克\nKiwi → 新西兰人\n"
                            "## 内容摘要\n新西兰旅行视频。"],
                backend_name="r")
    terms, summary = orch._extract_terminology_and_summary(["We rode an e-bike."], _R())
    assert terms == {"e-bike": "电动自行车", "Jean-Luc": "让-吕克", "Kiwi": "新西兰人"}
    assert summary == "新西兰旅行视频。"


def test_summary_failure_reason_is_logged(monkeypatch, caplog):
    """摘要那一次超时 / 限流时，日志要写出真实原因，实验靠它把「摘要失败」的那次作废（U2）。"""
    class _Timeout:
        name = "超时的AI"
        def translate_raw(self, prompt, max_tokens=4096):
            return translation_base.TranslationResult(
                translated=[""], backend_name=self.name, failure_kind="timeout")
    with caplog.at_level("WARNING"):
        assert orch._film_reference(["a line"], [0], [_Timeout()]) is None
    assert "timeout" in caplog.text
