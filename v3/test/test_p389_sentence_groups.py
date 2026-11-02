"""P389：翻译前先按词索引建立完整句子组。"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from test import locked_decision

import pytest

from test import (
    transcription_envelope_from_raw,
    transcription_envelope_from_segments,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _words(texts, start=0.0, step=0.4, gap=0.05):
    result = []
    cursor = start
    for text in texts:
        result.append({
            "word": text,
            "text": text,
            "start": round(cursor, 3),
            "end": round(cursor + 0.3, 3),
        })
        cursor += step + gap
    return result


def _segments_from_words(words, boundaries):
    from backend.transcriber_v2 import Segment

    result = []
    for seg_id, (begin, end) in enumerate(boundaries):
        chunk = [dict(word) for word in words[begin:end]]
        seg = Segment(
            id=seg_id,
            start=chunk[0]["start"],
            end=chunk[-1]["end"],
            text=" ".join(word["text"] for word in chunk),
        )
        seg.words = chunk
        result.append(seg)
    return result


def test_reliable_sentence_end_punctuation_creates_complete_groups():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["I", "am", "here.", "What", "is", "this?"])
    groups = build_sentence_groups(
        "I am here. What is this?", words, language="en"
    )

    assert [group.text for group in groups] == ["I am here.", "What is this?"]
    assert [(group.start_word_index, group.end_word_index) for group in groups] == [
        (0, 2),
        (3, 5),
    ]


def test_missing_punctuation_uses_a_safe_pause_without_inventing_text():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["hello", "world", "next"], step=0.1)
    words[2]["start"] = 1.6
    words[2]["end"] = 1.9

    groups = build_sentence_groups("hello world next", words, language="en")

    assert [group.text for group in groups] == ["hello world", "next"]
    assert "。" not in groups[0].text
    assert "。" not in groups[1].text


def test_suspicious_english_period_does_not_cut_after_function_word():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words([
        "I", "could", "show", "you", "a", "little", "bit", "of.",
        "What", "this", "part", "is", "like.",
    ], step=0.08)
    groups = build_sentence_groups(
        "I could show you a little bit of. What this part is like.",
        words,
        language="en",
    )

    assert len(groups) == 1
    assert groups[0].text.endswith("like.")
    assert groups[0].text == "I could show you a little bit of. What this part is like."


def test_suspicious_period_needs_a_longer_pause_before_it_can_split():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["I", "could", "show", "you", "a", "little", "bit", "of.", "What", "next."], step=0.08)
    words[8]["start"] = words[7]["end"] + 0.5
    words[8]["end"] = words[8]["start"] + 0.3
    words[9]["start"] = words[8]["end"] + 0.05
    words[9]["end"] = words[9]["start"] + 0.3
    groups = build_sentence_groups(
        "I could show you a little bit of. What next.",
        words,
        language="en",
    )

    assert len(groups) == 1


def test_full_text_punctuation_is_mapped_to_raw_words_before_grouping():
    from backend.core.sentence_groups import build_sentence_groups

    # G12 第三批 B（A+ §11）：词在识别阶段已带好标点，分组不再贴第二遍；按词自带标点分组。
    words = _words(["I", "am", "here.", "What", "next?"])
    groups = build_sentence_groups("I am here. What next?", words, language="en")

    assert [group.text for group in groups] == ["I am here.", "What next?"]




def test_raw_qwen_words_are_the_source_even_if_old_segment_container_is_shorter():
    from backend.core.sentence_groups import group_qwen_raw_for_translation

    raw_words = _words(["This", "is", "the", "old", "lifeboat", "house", "today."])
    old_container = _segments_from_words(raw_words[:5], [(0, 5)])
    groups = group_qwen_raw_for_translation(
        old_container,
        "This is the old lifeboat house today.",
        raw_words,
        language="en",
    )

    assert groups[0].text == "This is the old lifeboat house today."
    assert [word["text"] for group in groups for word in group.words] == [
        word["text"] for word in raw_words
    ]


def test_worker_prepare_uses_existing_qwen_raw_file_before_old_segments(tmp_path):
    from backend import worker

    raw_words = _words(["This", "is", "the", "complete", "sentence."])
    old_container = _segments_from_words(raw_words[:3], [(0, 3)])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "This is the complete sentence.",
        "word_timestamps": raw_words,
    }), encoding="utf-8")

    groups, _fallback_reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, old_container),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
    )

    assert [group.text for group in groups] == ["This is the complete sentence."]
    assert sum(len(group.words) for group in groups) == 5


def test_full_text_and_word_text_must_match_before_grouping():
    from backend.core.sentence_groups import SentenceGroupingError, build_sentence_groups

    words = _words(["Hello", "world."])
    build_sentence_groups("Hello world.", words, language="en")

    with pytest.raises(SentenceGroupingError, match="全文与词级文字不一致"):
        build_sentence_groups("Hello there.", words, language="en")


def test_grouping_keeps_every_word_once_and_in_original_order():
    from backend.core.sentence_groups import build_sentence_groups

    source = ["One", "two", "three.", "Four", "five", "six."]
    words = _words(source)
    groups = build_sentence_groups(" ".join(source), words, language="en")

    flattened = [word["text"] for group in groups for word in group.words]
    assert flattened == source
    assert len(flattened) == len(source)
    assert [
        index
        for group in groups
        for index in range(group.start_word_index, group.end_word_index + 1)
    ] == list(range(len(source)))


def test_group_times_are_first_word_start_and_last_word_end():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["First", "sentence.", "Second", "sentence."], step=0.4)
    groups = build_sentence_groups(
        "First sentence. Second sentence.", words, language="en"
    )

    assert groups[0].start == words[0]["start"]
    assert groups[0].end == words[1]["end"]
    assert groups[1].start == words[2]["start"]
    assert groups[1].end == words[3]["end"]
    assert groups[0].end <= groups[1].start


def test_no_empty_groups_and_no_time_backtracking():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["A", "short.", "Another", "one."], step=0.4)
    groups = build_sentence_groups("A short. Another one.", words, language="en")

    assert all(group.text.strip() for group in groups)
    assert all(group.start <= group.end for group in groups)
    assert all(left.end <= right.start for left, right in zip(groups, groups[1:]))


def test_zero_duration_word_is_not_left_as_standalone_group():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["I", "do", "cream", "first."], step=0.4)
    words[1].update(start=1.0, end=1.0)
    words[2].update(start=1.7, end=2.0)
    words[3].update(start=2.1, end=2.4)

    groups = build_sentence_groups(
        "I do cream first.", words, language="en", pause_threshold=0.45
    )

    assert [group.text for group in groups] == ["I do cream first."]
    assert all(group.start < group.end for group in groups)
    assert [word["text"] for group in groups for word in group.words] == [
        "I", "do", "cream", "first."
    ]


def test_pause_around_weak_english_words_keeps_context():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["The", "thing", "is", "you", "go."], step=0.1)
    words[2].update(start=1.3, end=1.6)
    words[3].update(start=2.2, end=2.5)
    words[4].update(start=2.6, end=2.9)

    groups = build_sentence_groups(
        "The thing is you go.", words, language="en", pause_threshold=0.45
    )

    assert [group.text for group in groups] == ["The thing is you go."]


def test_strong_boundary_does_not_leave_discourse_marker_alone():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["We", "went", "to", "explore.", "So", "we", "walk."], step=0.1)
    words[4].update(start=2.0, end=2.3)
    words[5].update(start=2.9, end=3.2)
    words[6].update(start=3.3, end=3.6)

    groups = build_sentence_groups(
        "We went to explore. So we walk.", words, language="en"
    )

    assert [group.text for group in groups] == [
        "We went to explore.", "So we walk."
    ]
    assert all(group.text.strip() != "So" for group in groups)


def test_max_duration_splits_at_word_boundary_without_changing_words():
    from backend.core.sentence_groups import build_sentence_groups

    words = _words(["one", "two", "three", "four"], step=0.8)
    groups = build_sentence_groups(
        "one two three four", words, language="en", max_duration=1.0
    )

    assert [word["text"] for group in groups for word in group.words] == [
        "one", "two", "three", "four"
    ]
    assert len(groups) >= 2


def test_chinese_and_japanese_words_join_without_spaces_and_use_end_marks():
    from backend.core.sentence_groups import build_sentence_groups

    zh_words = _words(["今天", "天气很好。", "我们", "出发吧！"])
    ja_words = _words(["今日は", "晴れです。", "元気", "ですか？"])

    zh_groups = build_sentence_groups(
        "今天天气很好。我们出发吧！", zh_words, language="zh"
    )
    ja_groups = build_sentence_groups(
        "今日は晴れです。元気ですか？", ja_words, language="ja"
    )

    assert [group.text for group in zh_groups] == ["今天天气很好。", "我们出发吧！"]
    assert [group.text for group in ja_groups] == ["今日は晴れです。", "元気ですか？"]
    assert all(" " not in group.text for group in zh_groups + ja_groups)


def test_precise_matched_segments_keep_qwen_words_for_sentence_grouping():
    from backend.transcriber_v2 import Segment, _assemble_qwen_words_on_whisper_boundaries
    from backend.core.sentence_groups import group_segments_for_translation

    qwen_words = _words(["This", "is", "precise."])
    whisper_segments = [
        SimpleNamespace(start=0.0, end=1.5, text="This is precise.", sensevoice_tag=None)
    ]

    precise_segments = _assemble_qwen_words_on_whisper_boundaries(qwen_words, whisper_segments, "en")

    assert precise_segments[0].words == qwen_words
    groups = group_segments_for_translation(precise_segments, language="en")
    assert [group.text for group in groups] == ["This is precise."]
    assert isinstance(groups[0], Segment)


def test_standard_and_precise_outputs_share_the_same_grouping_entry():
    from backend.transcriber_v2 import _assemble_qwen_primary, _assemble_qwen_words_on_whisper_boundaries
    from backend.core.sentence_groups import group_segments_for_translation

    qwen_words = _words(["One", "standard."])
    standard = _assemble_qwen_primary(qwen_words, [], "en")

    precise_words = _words(["One", "precise."])
    precise = _assemble_qwen_words_on_whisper_boundaries(
        precise_words,
        [SimpleNamespace(start=0.0, end=1.0, text="One precise.", sensevoice_tag=None)],
        "en",
    )

    assert [g.text for g in group_segments_for_translation(standard, language="en")] == [
        "One standard."
    ]
    assert [g.text for g in group_segments_for_translation(precise, language="en")] == [
        "One precise."
    ]


class _FakeCheckpoint:
    def save(self, *args, **kwargs):
        return None


def test_translation_entry_receives_complete_groups_not_old_fragments(monkeypatch, tmp_path):
    from backend import worker

    words = _words(["This", "is", "a", "boat.", "So", "we", "go."])
    fragments = _segments_from_words(words, [(0, 4), (4, 7)])
    groups, _fallback_reason = worker._prepare_sentence_groups(
        transcription_envelope_from_segments(worker, fragments, language="en")
    )
    received = []

    def fake_translate(*, texts, **kwargs):
        received.extend(texts)
        return ([f"译文 {index}" for index in range(len(texts))], [], None, {})

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )
    monkeypatch.setattr(worker, "_reflection_review", lambda *args: args[3])

    worker._step_translate(
        "translate",
        groups,
        tmp_path / "translated.json",
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        len(groups),
        _FakeCheckpoint(),
    )

    assert received == ["This is a boat.", "So we go."]
    assert all(text not in {"是", "所以", "船，", "船,"} for text in received)


def test_old_snapshot_cannot_restore_translations_to_new_sentence_groups(
    monkeypatch, tmp_path
):
    from backend import worker

    words = _words(["This", "is", "one.", "This", "is", "two."])
    groups, _fallback_reason = worker._prepare_sentence_groups(
        transcription_envelope_from_segments(
            worker, _segments_from_words(words, [(0, 3), (3, 6)])
        ),
    )
    (tmp_path / ".snapshot.json").write_text(
        json.dumps({"0": "旧碎片译文", "1": "旧碎片译文", "2": "旧碎片译文"}),
        encoding="utf-8",
    )
    received = []

    def fake_translate(*, texts, **kwargs):
        received.extend(texts)
        return (["新整句译文"] * len(texts), [], None, {})

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )
    monkeypatch.setattr(worker, "_reflection_review", lambda *args: args[3])

    worker._step_translate(
        "translate",
        groups,
        tmp_path / "translated.json",
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        len(groups),
        _FakeCheckpoint(),
    )

    assert received == ["This is one.", "This is two."]
    assert [group.zh for group in groups] == ["新整句译文", "新整句译文"]


def test_display_line_splitting_does_not_change_translation_input(monkeypatch, tmp_path):
    from backend import worker
    from backend.core.sentence_groups import group_segments_for_translation
    # G5-1：word_align_segments 已并入 display_timeline（同一映射算法）
    from backend.core.display_timeline import build_display_timeline

    words = _words(["One", "sentence.", "Another", "sentence."])
    groups = group_segments_for_translation(
        _segments_from_words(words, [(0, 4)]), language="en"
    )
    received = []

    def fake_translate(*, texts, **kwargs):
        received.extend(texts)
        return (["第一句。第二句。", "第三句。"], [], None, {})

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )
    monkeypatch.setattr(worker, "_reflection_review", lambda *args: args[3])

    worker._step_translate(
        "translate",
        groups,
        tmp_path / "translated.json",
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        len(groups),
        _FakeCheckpoint(),
    )
    display_segments = build_display_timeline(groups)

    # G5-1：同上——素材过短，0.84 秒补救阶梯会把它们合并。
    # 本测试保的是翻译输入不变（下一行），显示条目数不是它的契约。
    joined = "".join(e.text for e in display_segments)
    # G12 第三批 B 收尾（P471）：成品按奈飞规范去掉句号，按不带句号的正文检查。
    for piece in ("第一句", "第二句", "第三句"):
        assert piece in joined, f"显示层丢了「{piece}」"
    assert received == ["One sentence.", "Another sentence."]


def test_srt_display_clamps_tiny_boundary_overlap(tmp_path):
    """微小重叠必须被收紧。

    G5-1 改了两件事，断言随之更新：
      1. 收紧从写出器移到 display_timeline（写出器只验证不改时间，R-14）。
         第二条的 10.937 仍然被推到 10.940，这一点没变。
      2. 第二条原本只有 0.56 秒（10.940→11.500），低于 0.84 秒最短显示线，
         按 P402 的修复必须向后延长到 11.780。旧断言里的 11.500 正是
         「肉眼看不见的短字幕被放行」的旧行为。
    """
    from backend.core.subtitler import build_srt
    from backend.core.display_timeline import build_display_timeline
    from backend.transcriber_v2 import Segment

    first = Segment(-1, 10.0, 10.940, "first")
    first.zh = "第一条"
    first.words = []
    second = Segment(-2, 10.937, 11.5, "second")
    second.zh = "第二条"
    second.words = []
    output = tmp_path / "candidate.srt"

    build_srt(build_display_timeline([first, second]), output)
    timings = [
        line for line in output.read_text(encoding="utf-8").splitlines()
        if " --> " in line
    ]

    assert timings == [
        "00:00:10,000 --> 00:00:10,940",
        "00:00:10,940 --> 00:00:11,780",
    ]


@pytest.mark.parametrize("tier", ["standard", "precise"])
def test_worker_main_standard_and_precise_hit_one_shared_group_entry(
    monkeypatch, tmp_path, tier
):
    from backend import worker

    job_dir = tmp_path / tier
    job_dir.mkdir()
    video_path = job_dir / "input.mp4"
    video_path.write_bytes(b"video")
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps({
        "video_path": str(video_path),
        "video_filename": "input.mp4",
        "outputMode": "srt",
        "language": "en",
        "transcriptionTier": tier,
    }), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    raw_words = _words(["The", tier, "path."])
    (cache / "qwen_raw.json").write_text(json.dumps({
        "full_text": f"The {tier} path.",
        "word_timestamps": raw_words,
    }), encoding="utf-8")
    calls = []
    stage_cache = {}

    def fake_transcribe(*args):
        words = _words(["The", tier, "path."])
        segments = _segments_from_words(words, [(0, 3)])
        receipt = worker.TranscriptionReceipt(
            routed_primary="qwen",
            actual_primary="qwen",
            word_source="qwen_word_timestamps",
            source_text=f"The {tier} path.",
            source_words=words,
                      language_decision=locked_decision(),
        artifact_language_check="ok",
                  )
        return worker.TranscriptionEnvelope(segments, receipt)

    def capture_invalidation(
        _stage, _old_config, _config, _setting_stage, _stage_order,
        stage_cache_map, _checkpoint,
    ):
        stage_cache.update(stage_cache_map)
        return "start"

    # 给真实入口套一层计数器；原函数仍会执行，测试不是只看源码字符串。
    original_group = worker._prepare_sentence_groups
    monkeypatch.setattr(
        worker,
        "_prepare_sentence_groups",
        # G11：断句不再接收 language 实参，语言只从收据读。
        # 间谍改为从收据取语言——intent（两种模式共用同一个断句入口）不变，
        # 而且顺带把「语言确实来自收据」也锁住了。
        lambda transcription, **kwargs: (
            calls.append((
                tier,
                transcription.receipt.language_decision.language,
                len(transcription),
            ))
            or original_group(transcription, **kwargs)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", f"p389-{tier}")
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(worker, "_validate_config", lambda config: None)
    monkeypatch.setattr(worker, "_download_source_if_url", lambda config, directory, path: path)
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache, "hash"),
    )
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(worker, "_read_checkpoint_meta", lambda path: {})
    monkeypatch.setattr(worker, "_invalidate_stale_caches", capture_invalidation)
    monkeypatch.setattr(worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    monkeypatch.setattr(worker, "_step_transcribe", fake_transcribe)
    monkeypatch.setattr(worker, "_run_transcript_refiner", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "_step_translate",
        lambda *args, **kwargs: ([], [], len(args[1]), {}, None),
    )
    monkeypatch.setattr(worker, "_step_subtitle", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_step_burn_or_mux", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_validate_output_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_deliver_outputs", lambda *args, **kwargs: False)

    assert worker.main() is None
    assert calls == [(tier, "en", 1)]
    assert str(cache / "sentence_boundaries.json") in stage_cache["transcribe"]
