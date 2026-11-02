"""P390 补完：语义闸 + 反馈重试 + 本地回退前移（T1-T22）。

一句话：断句的三条路（大模型答案、本地回退答案、缓存读回的答案）必须过同一道闸，
这道闸只回答一个是非题——「这一组的最后一个词，是不是一个『话没说完』的词」。
"""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from backend.core import sentence_groups as sg
from test import transcription_envelope_from_raw, locked_decision
from backend.transcriber_v2 import (
    ArtifactLanguageMismatch as _real_artifact_mismatch,
    TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY as _real_word_sources,
)


def _words(texts, *, step=0.4, gap=0.1):
    """按顺序造词级时间；每个词 0.3 秒，词间隔 gap。"""
    out = []
    clock = 0.0
    for text in texts:
        out.append({
            "word": text,
            "text": text,
            "start": round(clock, 3),
            "end": round(clock + 0.3, 3),
        })
        clock += 0.3 + gap
    return out


def _text_of(words):
    return " ".join(word["text"] for word in words)


def _load_worker(monkeypatch):
    class _Segment:
        def __init__(self, id, start, end, text):
            self.id = id
            self.start = start
            self.end = end
            self.text = text
            self.words = []
            self.zh = ""
            self.speaker_change = False
            self.uncertain = False

    class _Receipt:
        def __init__(self, routed_primary, actual_primary, word_source,
                     source_text, source_words, source_fingerprint=None,
                     reference_text="", reference_usable=False,
                     language_decision=None, engine_received_language=None,
                     engines_in_text_chain=None,
                     artifact_language_check="ok",
                     transcription_request_fingerprint="",
                     observation_evidence=None):
            self.routed_primary = routed_primary
            self.actual_primary = actual_primary
            self.word_source = word_source
            self.source_text = source_text
            self.source_words = source_words
            self.source_fingerprint = source_fingerprint or "test-source"
            # G2/B9+B10：收据带上副引擎的完整转录与可用性判定。
            self.reference_text = reference_text
            self.reference_usable = reference_usable
            # G11：语言决定与各引擎实收语言，随收据一起穿过存档往返。
            if language_decision is None:
                from test import locked_decision
                language_decision = locked_decision()
            self.language_decision = language_decision
            self.engine_received_language = dict(engine_received_language or {})
            # G11-B：本次哪些引擎的文字进了正式链路——缓存复用检查的依据。
            self.engines_in_text_chain = tuple(
                engines_in_text_chain
                if engines_in_text_chain is not None
                else self.engine_received_language.keys())
            self.artifact_language_check = artifact_language_check
            self.transcription_request_fingerprint = transcription_request_fingerprint
            self.observation_evidence = dict(observation_evidence or {})

    class _Envelope:
        def __init__(self, segments, receipt):
            self.segments = list(segments)
            self.receipt = receipt

        def __iter__(self):
            return iter(self.segments)

        def __len__(self):
            return len(self.segments)

        def __getitem__(self, index):
            return self.segments[index]

        def __setitem__(self, index, value):
            self.segments[index] = value

    monkeypatch.setitem(
        sys.modules,
        "backend.transcriber_v2",
        SimpleNamespace(
            Segment=_Segment,
            TranscriptionEnvelope=_Envelope,
            TranscriptionReceipt=_Receipt,
            transcribe=lambda *args, **kwargs: [],
            _whisper_is_usable=lambda *args, **kwargs: False,
            # G11：worker 会 import 这两个异常类型；替身模块必须提供**真的**那两个，
            # 否则 except 捕获的是另一个类，测试会假绿。
            ArtifactLanguageMismatch=_real_artifact_mismatch,
            TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY=_real_word_sources,
        ),
    )
    monkeypatch.delitem(sys.modules, "backend.worker", raising=False)
    return importlib.import_module("backend.worker")


class _RecordingBoundaryLLM:
    def __init__(self, responses):
        if isinstance(responses, str):
            responses = [responses]
        self.responses = list(responses)
        self.calls = []

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


# ── 语义正确性 ────────────────────────────────────────────────

def test_llm_break_after_conjunction_is_rejected():
    """T1：大模型在 but 后面断 → 闸必须拦下，结果里不能有这个断点。"""
    words = _words(["It", "was", "freezing", "but.", "It", "was", "worth", "it."])
    resolved, _added = sg.resolve_llm_primary_breaks(
        _text_of(words),
        words,
        break_after_indices=[3],
        language="en",
    )
    assert 3 not in resolved, "组尾挂着 but 的断点必须被拦下"


def test_break_after_as_is_rejected_and_as_well_stays_in_one_group():
    """真实索引 378：语义闸拦下 as 后断点，as 和 well 必须留在同一组。"""
    words = _words(["word"] * 378 + ["as", "well", "continues."])
    raw_text = _text_of(words)

    resolved, _added = sg.resolve_llm_primary_breaks(
        raw_text,
        words,
        break_after_indices=[378],
        language="en",
        max_duration=999,
        max_words=999,
        max_chars=9999,
    )

    assert 378 not in resolved
    bounds = [-1, *resolved, len(words) - 1]
    assert any(
        left < 378 and right >= 379
        for left, right in zip(bounds, bounds[1:])
    ), "索引 378 的 as 和索引 379 的 well 必须落在同一组"


def test_fallback_break_after_conjunction_is_rejected():
    """T2：本地回退自己在 but 后面断 → 也要被同一道闸管住。"""
    words = _words(["It", "was", "freezing", "but.", "It", "was", "worth", "it."])
    groups = sg.build_sentence_groups(_text_of(words), words, language="en")

    assert all(
        not group.text.rstrip().lower().endswith("but.")
        for group in groups
    ), "本地回退不能把 but 留在组尾"
    joined = [group.text for group in groups]
    assert any(text.lower().startswith("but") for text in joined), \
        "but 应该出现在下一组的开头"


def test_cached_breaks_are_reused_without_re_judging_semantics(monkeypatch, tmp_path):
    """T3（2026-07-28 用户裁决后改写）：缓存不再过语义闸，必须能正常复用。

    原来这条测的是「缓存里的坏断点要被拦下重算」。施工中实测发现那样做会出事：
    断句最后一层「硬限制」（一组不许超过 20 秒 / 80 词 / 240 字）按设计就不看语义，
    它切出来的刀完全可能落在 the / on 这种词上（已用 120 词无标点长句实测复现）。
    语义闸一扫就作废整份缓存 → 重算 → 结果一模一样 → 再作废，
    这个视频的断句缓存永远不生效，每跑一次都白调一遍大模型。

    「旧逻辑写的存档要作废」这个真正的风险，由版本号（A2）确定性挡住，见 T20。
    这条测试守住的是：别再有人顺手把这道闸加回缓存路径。
    """
    worker = _load_worker(monkeypatch)
    words = _words(["It", "was", "freezing", "but.", "It", "was", "worth", "it."])
    raw_text = _text_of(words)
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": raw_text,
        "word_timestamps": words,
    }), encoding="utf-8")

    bad_groups = sg.group_qwen_raw_from_break_indices(
        [], raw_text, words, break_after_indices=[3], language="en",
    )
    envelope = transcription_envelope_from_raw(worker, raw_path)
    cache_path = tmp_path / "sentence_boundaries.json"
    cache_path.write_text(json.dumps({
        "version": worker._P390_BOUNDARY_CACHE_VERSION,
        "source_fingerprint": envelope.receipt.source_fingerprint,
        "hint_fingerprint": "legacy-no-sound-map",
        "language": "en",
        "break_after_indices": [3],
        "group_fingerprint": worker._p390_group_fingerprint(bad_groups),
    }), encoding="utf-8")

    llm = _RecordingBoundaryLLM('{"break_after_indices": [2]}')
    groups, _reason = worker._prepare_sentence_groups(
        envelope, boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert llm.calls == [], "版本和指纹都对得上的缓存必须直接复用，不许重算"
    assert [group.start_word_index for group in groups] == [0, 4], \
        "复用的就是缓存里那份断点，不是重算出来的"


def test_wrong_asr_period_after_so_and_to():
    """T4：so. / to. 这类误加的句号，不能产生「组尾是 so/to」的组。"""
    words = _words([
        "I", "have", "to.", "show", "you", "this", "so.", "you", "can", "see.",
    ])
    groups = sg.build_sentence_groups(_text_of(words), words, language="en")
    tails = [group.text.rstrip().lower().rstrip(".") for group in groups]
    assert "i have to" not in tails
    assert not any(tail.endswith(" so") or tail == "so" for tail in tails)


def test_two_word_conjunction_group_rejected():
    """T5：`and then.` 这种 2 词组也要被拦（旧检查只管 1 个词的组）。"""
    words = _words(["We", "walked", "around", "and", "then.", "we", "left."])
    resolved, _added = sg.resolve_llm_primary_breaks(
        _text_of(words),
        words,
        break_after_indices=[2, 4],
        language="en",
    )
    assert 4 not in resolved, "组尾是 then 的 2 词组必须被拦下"


def test_subordinate_clause_not_split_at_if():
    """T6：`...called Devon, if` → if 归下一组开头。"""
    words = _words([
        "So", "if", "you", "are", "in", "Devon,", "if",
        "you", "like", "cream", "tea.",
    ])
    groups = sg.build_sentence_groups(_text_of(words), words, language="en")
    assert all(
        not group.text.rstrip().lower().endswith("if")
        for group in groups
    )


# ── 半句停顿 / 真句末 / 不许误伤 ──────────────────────────────

def test_long_pause_after_conjunction_still_not_split():
    """T7：but 后面有 0.782 秒真实停顿（1195 的真数据），仍然不许断。"""
    words = _words(["absolutely", "freezing", "but.", "It", "was", "worth", "it."])
    words[3]["start"] = round(words[2]["end"] + 0.782, 3)
    words[3]["end"] = round(words[3]["start"] + 0.3, 3)
    for index in range(4, len(words)):
        words[index]["start"] = round(words[index - 1]["end"] + 0.1, 3)
        words[index]["end"] = round(words[index]["start"] + 0.3, 3)

    groups = sg.build_sentence_groups(_text_of(words), words, language="en")
    assert all(
        not group.text.rstrip().lower().endswith("but.")
        for group in groups
    )


def test_real_sentence_end_still_splits():
    """T8：真正的句末照常断（保护 153 不被新闸误伤）。"""
    words = _words([
        "It", "is", "a", "hidden", "gem", "in", "the", "UK.",
        "We", "went", "there", "last", "year.",
    ])
    groups = sg.build_sentence_groups(_text_of(words), words, language="en")
    assert len(groups) == 2
    assert groups[0].text.endswith("UK.")


def test_question_ending_in_auxiliary_is_not_flagged():
    """T8b：`What do you do?` 是完整的一句话，不能因为结尾是 do 就被拦下。

    真实数据里就有这一处（断点 489）。疑问句天生以助动词、介词结尾，
    闸门把它拦下会连累整个窗口退回本地规则，是纯亏。
    """
    words = _words(["What", "do", "you", "do?", "I", "make", "tea."])
    assert sg.find_unfinished_group_endings(words, [3], language="en") == []


def test_llm_removal_of_bad_break_preserved():
    """T9：大模型删掉的坏断点不能被闸门或回退加回来（保护 115）。"""
    words = _words(["It's", "around", "22", "degrees", "today."])
    resolved, _added = sg.resolve_llm_primary_breaks(
        _text_of(words),
        words,
        break_after_indices=[],
        language="en",
    )
    assert resolved == [], "大模型说不断，就不能有人偷偷加回来"


def test_llm_override_of_wrong_period_preserved():
    """T10：大模型推翻了 `English became.` 这个错句号，结果要保住（保护 1640）。"""
    words = _words([
        "Eventually", "English", "became.", "More", "widely", "spoken", "here.",
    ])
    resolved, _added = sg.resolve_llm_primary_breaks(
        _text_of(words),
        words,
        break_after_indices=[],
        language="en",
    )
    assert 2 not in resolved, "became. 后面不能断"


def test_accepted_boundary_1471_unchanged():
    """T11：用户已接受的 1471（`...ice cream` | `Is because...`）保持原样。"""
    words = _words([
        "the", "best", "ice", "cream.", "Is", "because", "of", "the", "milk.",
    ])
    resolved, _added = sg.resolve_llm_primary_breaks(
        _text_of(words),
        words,
        break_after_indices=[3],
        language="en",
    )
    assert resolved == [3], "cream 是实词，这个断点必须保留"


def test_normal_sentences_not_over_merged():
    """T12：正常句子不会因为新闸被合并（组数不塌）。"""
    texts = []
    for index in range(12):
        texts.extend(["Sentence", str(index), "ends", "here."])
    words = _words(texts)
    groups = sg.build_sentence_groups(_text_of(words), words, language="en")
    assert len(groups) == 12


# ── 不变量 ────────────────────────────────────────────────────

_INVARIANT_TEXTS = [
    "It", "was", "like", "freezing,", "absolutely", "freezing,", "but.",
    "It", "was", "worth", "it.", "So", "if", "you", "are", "in", "Devon,",
    "if", "you", "like", "cream", "tea.", "and", "then.", "we", "left.",
]


def _invariant_groups():
    words = _words(_INVARIANT_TEXTS)
    return words, sg.build_sentence_groups(_text_of(words), words, language="en")


def test_word_count_preserved():
    """T13：所有组的词数之和 == 输入词数。"""
    words, groups = _invariant_groups()
    assert sum(len(group.words) for group in groups) == len(words)


def test_word_order_preserved():
    """T14：拼接回去的词序列与输入完全一致。"""
    words, groups = _invariant_groups()
    flat = [word["text"] for group in groups for word in group.words]
    assert flat == [word["text"] for word in words]


def test_word_timestamps_unchanged():
    """T15：每个词的 start/end 一字节不差。"""
    words, groups = _invariant_groups()
    flat = [(word["start"], word["end"]) for group in groups for word in group.words]
    assert flat == [(word["start"], word["end"]) for word in words]


def test_no_empty_or_zero_duration_group():
    """T16：没有空组，也没有 end <= start 的组。"""
    _, groups = _invariant_groups()
    assert all(group.text.strip() for group in groups)
    assert all(group.end > group.start for group in groups)


def test_no_overlapping_groups():
    """T17：相邻组不重叠。"""
    _, groups = _invariant_groups()
    for left, right in zip(groups, groups[1:]):
        assert left.end <= right.start


def test_group_text_equals_joined_words():
    """T18：组文字 == 词拼接（大模型不许改写原词）。"""
    _, groups = _invariant_groups()
    for group in groups:
        assert group.text == " ".join(word["text"] for word in group.words)


# ── 路径一致性 ────────────────────────────────────────────────


def test_cache_version_bumped_on_prompt_change(monkeypatch, tmp_path):
    """T20：临时展示词逻辑升级后，旧版本存档必须作废，当前版本可复用。

    这条是「静默失效」的守门员：版本号校验被删掉、被简化掉、或忘记 bump，
    这条测试立刻变红，而不是「跑完发现没效果」。
    """
    worker = _load_worker(monkeypatch)
    words = _words(["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    raw_text = _text_of(words)
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": raw_text,
        "word_timestamps": words,
    }), encoding="utf-8")

    stale_groups = sg.group_qwen_raw_from_break_indices(
        [], raw_text, words, break_after_indices=[4], language="en",
    )
    cache_path = tmp_path / "sentence_boundaries.json"
    cache_path.write_text(json.dumps({
        "version": 6,  # P409 写下的旧版断句逻辑，本批必须拒绝
        "source_fingerprint": worker._p390_source_fingerprint(raw_text, words),
        "language": "en",
        "break_after_indices": [4],
        "group_fingerprint": worker._p390_group_fingerprint(stale_groups),
    }), encoding="utf-8")

    stale_llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=stale_llm,
    )
    assert stale_llm.calls, "旧版本存档必须被拒绝并重新计算"
    rewritten = json.loads(cache_path.read_text(encoding="utf-8"))
    assert rewritten["version"] == worker._P390_BOUNDARY_CACHE_VERSION == 9  # G12 第三批 B 升版本

    # 反过来：本轮自己写下的存档必须能被复用，否则就是每次都白算
    fresh_llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=fresh_llm,
    )
    assert fresh_llm.calls == [], "当前版本写下的存档必须能复用"


def test_retry_only_once_then_fallback(monkeypatch, tmp_path):
    """T22：坏答案最多重试一次，绝不无限循环。

    P395：语义不合格**不再重试**（直接剔除坏断点），
    只有「回答本身坏了」才原样重发一次。这里改测后者。
    """
    worker = _load_worker(monkeypatch)
    words = _words(["It", "was", "freezing", "but.", "It", "was", "worth", "it."])
    raw_text = _text_of(words)
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": raw_text,
        "word_timestamps": words,
    }), encoding="utf-8")

    # 一直给「回答本身坏了」的答案：最多原样重发一次，不能无限循环
    llm = _RecordingBoundaryLLM("这不是 JSON")
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 2, "坏答案最多问两次：原始 + 一次重试"
    assert reason, "两次都坏就整篇回退，并且要让用户看见"
    assert all(
        not group.text.rstrip().lower().endswith("but.")
        for group in groups
    ), "回退结果同样不能把 but 留在组尾"
