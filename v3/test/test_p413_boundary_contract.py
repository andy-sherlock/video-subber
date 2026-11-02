"""P413：断点编号必须来自同一份范围，单个坏编号不能废掉整份回答。"""

import json
import importlib
import sys
from types import SimpleNamespace

import pytest

from test import transcription_envelope_from_raw, locked_decision
from backend.transcriber_v2 import (
    ArtifactLanguageMismatch as _real_artifact_mismatch,
    TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY as _real_word_sources,
)


def _words(count):
    # G12 第三批 B：首字母大写，避免触发 A+ 规则 1（本文件验编号契约，与分组语义无关）
    return [
        {
            "word": f"W{index}",
            "text": f"W{index}",
            "start": float(index),
            "end": float(index) + 0.5,
        }
        for index in range(count)
    ]


def _timed_words(*times):
    return [
        {
            "word": f"w{index}",
            "text": f"w{index}",
            "start": float(start),
            "end": float(end),
        }
        for index, (start, end) in enumerate(times)
    ]


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


class _BoundaryLLM:
    max_input_tokens = 1_000_000

    def __init__(self, response):
        self.response = response
        self.calls = []

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        return self.response


def _parse_without_old_whole_answer_failure(parse, raw_response, **kwargs):
    try:
        return parse(raw_response, **kwargs)
    except Exception as exc:  # pragma: no cover - this is the RED assertion
        pytest.fail(f"旧代码把局部编号错误当成整份失败：{exc}")


def test_p413_drops_one_out_of_range_index_and_keeps_the_rest():
    """最后一个越界编号不能让前面的合法断点全部丢失。"""
    from backend.core.sentence_groups import parse_llm_primary_break_response

    result = _parse_without_old_whole_answer_failure(
        parse_llm_primary_break_response,
        json.dumps({"break_after_indices": [0, 1, 2, 1411, 1412]}),
        allowed_range=range(0, 1412),
    )

    assert result == [0, 1, 2, 1411]


def test_p413_drops_duplicate_or_descending_items_and_keeps_later_valid_items():
    """重复或倒序的单个编号被删除，后面的正常编号仍然保留。"""
    from backend.core.sentence_groups import parse_llm_primary_break_response

    result = _parse_without_old_whole_answer_failure(
        parse_llm_primary_break_response,
        json.dumps({"break_after_indices": [1, 2, 2, 4, 3, 5]}),
        allowed_range=range(0, 6),
    )

    assert result == [1, 2, 4, 5]


def test_p413_prompt_builder_returns_the_same_break_range_it_describes():
    """提示词显示所有词，但返回的可断范围不包含最后一个词。"""
    from backend.core import sentence_groups

    result = sentence_groups.build_llm_primary_boundary_prompt(
        _words(5),
        language="en",
        start_index=0,
        end_index=4,
    )

    assert len(result) == 3
    _system_prompt, user_prompt, allowed_break_range = result
    assert "4: W4" in user_prompt
    assert list(allowed_break_range) == [0, 1, 2, 3]


def test_p413_single_word_has_no_legal_break_range():
    """只有一个词时，最后一个词后面没有可断位置。"""
    from backend.core import sentence_groups

    result = sentence_groups.build_llm_primary_boundary_prompt(
        _words(1),
        language="en",
        start_index=0,
        end_index=0,
    )

    assert len(result) == 3
    assert list(result[2]) == []


def test_p413_abnormal_answer_log_excerpt_has_a_fixed_upper_bound(monkeypatch):
    """异常回答要留证据，但不能让一条日志无限变大。"""
    worker = _load_worker(monkeypatch)

    excerpt = worker._p413_log_excerpt("x" * 20_000)

    assert len(excerpt) <= worker._P413_LOG_RESPONSE_LIMIT
    assert "中间省略" in excerpt


def test_p413_one_word_needs_no_model_break_call(monkeypatch, tmp_path):
    """没有合法断点时直接生成单词组，不把空答案误判成失败。"""
    worker = _load_worker(monkeypatch)
    words = _words(1)
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(
        json.dumps({
            "full_text": words[0]["text"],
            "word_timestamps": words,
        }),
        encoding="utf-8",
    )
    llm = _BoundaryLLM('{"break_after_indices": []}')

    groups, fallback_reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=llm,
    )

    assert fallback_reason is None
    assert llm.calls == []
    assert len(groups) == 1
    assert groups[0].start_word_index == 0
    assert groups[0].end_word_index == 0


def test_p413_production_path_keeps_valid_breaks_after_one_bad_final_number(
    monkeypatch, tmp_path, caplog,
):
    """生产入口不能因一个最后越界编号而退回规则组句。"""
    worker = _load_worker(monkeypatch)
    words = _words(5)
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(
        json.dumps({
            "full_text": " ".join(word["text"] for word in words),
            "word_timestamps": words,
        }),
        encoding="utf-8",
    )
    llm = _BoundaryLLM('{"break_after_indices": [1, 3, 4]}')

    groups, fallback_reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=llm,
    )

    assert fallback_reason is None
    assert len(llm.calls) == 1
    assert "允许返回的断点编号范围：0 到 3" in llm.calls[0][0]
    assert [group.start_word_index for group in groups] == [0, 2, 4]
    assert groups[-1].end_word_index == 4
    assert "P413 断句回答删除 1 个无效编号" in caplog.text
    assert '原回答={"break_after_indices": [1, 3, 4]}' in caplog.text


def test_p413_tail_drops_the_break_after_a_middle_zero_duration_group():
    """中间坏组撤销自己的结尾刀，零时长词并入右组。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 1), (1, 1), (1, 2))
    full_text = " ".join(word["text"] for word in words)

    breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[0, 1],
        language="en",
    )
    groups = sentence_groups.rebuild_sentence_groups_from_raw_break_indices(
        full_text,
        words,
        break_after_indices=breaks,
        language="en",
    )

    assert breaks == [0]
    assert added_by_limits == set()
    assert [(group.start_word_index, group.end_word_index) for group in groups] == [
        (0, 0),
        (1, 2),
    ]


def test_p413_tail_drops_the_previous_break_for_a_final_zero_duration_group():
    """全片最后没有右组时，只能撤销前一刀并入左组。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 1), (1, 1))
    full_text = " ".join(word["text"] for word in words)

    breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[0],
        language="en",
    )

    assert breaks == []
    assert added_by_limits == set()


def test_p413_tail_rechecks_after_each_removed_break():
    """连续坏组不能只扫旧分组一次，撤刀后必须重新计算。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 1), (1, 1), (1, 1), (1, 2))
    full_text = " ".join(word["text"] for word in words)

    breaks, _added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[0, 1, 2],
        language="en",
    )

    assert breaks == [0]


def test_p413_tail_cleans_before_hard_limits_can_choose_a_new_safe_break():
    """先撤刀再补刀，才能让合并后的长组重新得到安全切点。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 0), (0, 0), (1, 1), (1, 2))
    full_text = " ".join(word["text"] for word in words)

    breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[1],
        language="en",
        max_duration=1.0,
    )

    assert breaks == [2]
    assert added_by_limits == {2}


def test_p413_tail_does_not_mechanically_readd_the_rejected_break():
    """原坏刀被撤后，补刀应向前找可用位置，不能把同一刀补回来。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 10), (10, 19), (20.5, 20.5))
    full_text = " ".join(word["text"] for word in words)

    breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[1],
        language="en",
        max_duration=20.0,
    )
    groups = sentence_groups.rebuild_sentence_groups_from_raw_break_indices(
        full_text,
        words,
        break_after_indices=breaks,
        language="en",
    )

    assert breaks == [0]
    assert added_by_limits == {0}
    assert all(group.end - group.start <= 20.0 for group in groups)


def test_p413_tail_refuses_a_merge_that_would_hide_a_later_word_end():
    """撤刀不能让字幕结束时间早于组内任一原词的结束时间。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 2), (1, 1))
    full_text = " ".join(word["text"] for word in words)

    with pytest.raises(sentence_groups.SentenceGroupingError, match="词时间"):
        sentence_groups.enforce_llm_break_hard_limits(
            full_text,
            words,
            break_after_indices=[0],
            language="en",
        )


def test_p413_tail_cleans_after_hard_limits_without_miscounting_added_breaks():
    """补刀后撤掉模型刀时，仍只把真正的补刀记进统计。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 0), (0, 1), (2, 2), (2, 3))
    full_text = " ".join(word["text"] for word in words)

    breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[2],
        language="en",
        max_duration=1.0,
    )

    assert breaks == [1]
    assert added_by_limits == {1}


def test_p413_tail_does_not_readd_a_removed_break_when_limits_conflict():
    """第二次撤刀后只做终检；冲突时有限失败，不能加刀撤刀循环。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 19), (20.5, 20.5))
    full_text = " ".join(word["text"] for word in words)

    with pytest.raises(sentence_groups.SentenceGroupingError, match="硬限制"):
        sentence_groups.enforce_llm_break_hard_limits(
            full_text,
            words,
            break_after_indices=[],
            language="en",
            max_duration=20.0,
        )


def test_p413_tail_keeps_normal_breaks_unchanged():
    """没有坏组时，两次检查都必须是无操作。"""
    from backend.core import sentence_groups

    words = _timed_words((0, 1), (1, 2), (2, 3), (3, 4))
    full_text = " ".join(word["text"] for word in words)

    breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[1],
        language="en",
    )

    assert breaks == [1]
    assert added_by_limits == set()


def test_p413_tail_production_path_keeps_ai_breaks_words_and_cache(
    monkeypatch, tmp_path,
):
    """真实生产入口局部撤刀后继续用 AI，且重跑直接复用安全缓存。"""
    worker = _load_worker(monkeypatch)
    words = [
        {"word": "前。", "text": "前。", "start": 0.0, "end": 1.0},
        {"word": "零。", "text": "零。", "start": 1.0, "end": 1.0},
        {"word": "後。", "text": "後。", "start": 1.0, "end": 2.0},
        {"word": "終。", "text": "終。", "start": 2.0, "end": 3.0},
    ]
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(
        json.dumps({
            "full_text": "".join(word["text"] for word in words),
            "word_timestamps": words,
        }),
        encoding="utf-8",
    )
    llm = _BoundaryLLM('{"break_after_indices": [0, 1, 2]}')

    first_groups, first_fallback = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="ja"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )
    second_groups, second_fallback = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="ja"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    expected_words = [
        (word["text"], word["start"], word["end"])
        for word in words
    ]
    actual_words = [
        (word["text"], word["start"], word["end"])
        for group in first_groups
        for word in group.words
    ]
    assert first_fallback is None
    assert second_fallback is None
    assert len(llm.calls) == 1
    assert [group.start_word_index for group in first_groups] == [0, 1, 3]
    assert [group.end_word_index for group in first_groups] == [0, 2, 3]
    assert actual_words == expected_words
    assert [group.text for group in second_groups] == [
        group.text for group in first_groups
    ]
