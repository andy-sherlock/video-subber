"""P482：识别给的词时间倒挂（后词开始早于前词结束），断点正好落在那里时，
句子组照常建立；两组之间的重叠只交给显示层 `_repair_times` 处理。

真实现场：87b9979a1a 第 4324 词 `speak.`（1566.146–1566.306）后接
`Speak`（1566.277 起），倒挂 0.029 秒。组句处原来有一道「句子组时间重叠」
检查，只报错不修——AI 断在这里整片回退，规则断句再撞上就任务失败、0 字幕。

三条用户路都从生产入口 `worker._prepare_sentence_groups` 打进去：
没配 key（规则路）、配了 key 首次处理（AI 路）、重跑命中断点缓存。
"""

import json

from test import transcription_envelope_from_raw
from test.test_p390_worker_integration import (
    _FlakyBoundaryLLM,
    _RecordingBoundaryLLM,
    _load_worker_without_audio_models,
    _words,
)

_TEXTS = ["We", "found", "a", "hidden", "gem.", "It", "was", "great."]
_FULL_TEXT = "We found a hidden gem. It was great."


def _overlapping_words():
    """`gem.` 结束（2.03）晚于 `It` 开始（2.0）：倒挂 0.03 秒，与真实现场同形。"""
    words = _words(_TEXTS)
    words[4]["end"] = 2.03
    assert words[4]["end"] > words[5]["start"]
    return words


def _raw_path(tmp_path, words):
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": _FULL_TEXT,
        "word_timestamps": words,
    }), encoding="utf-8")
    return raw_path


def _assert_words_untouched(groups, words):
    """C4：组里的原词与时间一个不改，组的起止就是首词开始 / 末词结束。"""
    assert [w for group in groups for w in group.words] == words
    for group in groups:
        assert group.start == group.words[0]["start"]
        assert group.end == group.words[-1]["end"]


def test_rule_path_keeps_groups_split_at_overlap_and_display_tightens_it(
    monkeypatch, tmp_path,
):
    """C1 + C4 + C5：没配 key → 规则断句断在倒挂处，不再抛错；显示层把重叠收紧。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _overlapping_words()
    raw_path = _raw_path(tmp_path, words)

    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        api_keys=None,
    )

    assert reason is not None  # 规则路照常让用户看见「没用上 AI 断句」
    assert [(g.start_word_index, g.end_word_index) for g in groups] == [(0, 4), (5, 7)]
    assert groups[0].end > groups[1].start
    _assert_words_untouched(groups, words)

    from backend.core.display_timeline import build_display_timeline

    groups[0].zh = "我们找到了一颗宝石。"
    groups[1].zh = "它很棒。"
    entries = build_display_timeline(groups)
    assert entries
    for prev, nxt in zip(entries, entries[1:]):
        assert prev.end <= nxt.start


def test_ai_path_uses_ai_break_at_overlap_without_fallback(monkeypatch, tmp_path):
    """C2 + C4：AI 断点落在倒挂处 → 用 AI 的组，不整片回退。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _overlapping_words()
    raw_path = _raw_path(tmp_path, words)
    llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')

    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=llm,
    )

    assert reason is None
    assert len(llm.calls) == 1
    assert [(g.start_word_index, g.end_word_index) for g in groups] == [(0, 4), (5, 7)]
    _assert_words_untouched(groups, words)


def test_rerun_reuses_boundary_cache_at_overlap_without_asking_ai_again(
    monkeypatch, tmp_path,
):
    """C3：同一视频重跑，缓存断点落在倒挂处 → 直接复用，不再付费调 AI。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _overlapping_words()
    raw_path = _raw_path(tmp_path, words)
    cache_path = raw_path.with_name("sentence_boundaries.json")

    first_groups, _reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=cache_path,
        llm_boundary_backend=_RecordingBoundaryLLM('{"break_after_indices": [4]}'),
    )
    assert cache_path.exists()

    second_llm = _FlakyBoundaryLLM([], raise_on={0})
    second_groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=cache_path,
        llm_boundary_backend=second_llm,
    )

    assert second_llm.calls == []
    assert reason is None
    assert [g.text for g in second_groups] == [g.text for g in first_groups]

