"""G11 施工项 4-3 / 4-4：观察证据只留证，不参与裁决。

4-3：复用 `_make_voice_timeline` 已有的人声缺口结果——**不新建第二套检测**。
     它不是 P444 英转日事故的验收信号（事故任务这项全绿），但删掉后
     「大段有人声却没有文字」在交付前就没有接管者了。
4-4：SenseVoice 语言标签**只作观察证据**，不得升格为第二判据。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sv_events():
    return [
        {"start_s": 0.0, "end_s": 300.0, "event": "Speech",
         "tags": {"language": "en", "emotion": "NEUTRAL", "event": "Speech"}},
        {"start_s": 300.0, "end_s": 600.0, "event": "Speech",
         "tags": {"language": "en", "emotion": "NEUTRAL", "event": "Speech"}},
        {"start_s": 600.0, "end_s": 900.0, "event": "BGM",
         "tags": {"language": "ja", "emotion": "NEUTRAL", "event": "BGM"}},
    ]


def test_sensevoice_evidence_keeps_raw_events_not_vote_counts():
    """合同：保存原始事件（起止、语言、事件类型）。

    2026-08-30（G11-B）删去**三个**统计字段：`covered_seconds` / `coverage_ratio` / `audio_duration`：
    算的是「事件占了多少秒」，却极易被读成「实际处理了多少音频」，
    而且分母当时用的是最后一条字幕的结束时间。无人消费且一直在撒谎 → 删。

    **不得写死事件数量**——那由素材时长与分块结果决定；
    **不得把传播后的字幕段当成票数**（事故任务里 852 个 Speech 是 7 个粗事件
    按时间重叠传播出来的）。
    """
    from backend.transcriber_v2 import build_sound_observation

    obs = build_sound_observation(_sv_events())

    assert obs["event_count"] == 3, "有几个就是几个，不写死"
    assert [e["language"] for e in obs["events"]] == ["en", "en", "ja"]
    assert [e["event"] for e in obs["events"]] == ["Speech", "Speech", "BGM"]
    for gone in ("covered_seconds", "coverage_ratio", "audio_duration"):
        assert gone not in obs, f"三个误导性统计字段必须全部删除：{gone}"
    # 明确不做：不许出现任何形如「票数/多数/裁决」的字段
    for forbidden in ("votes", "majority", "decided_language", "verdict"):
        assert forbidden not in obs, f"SenseVoice 不得升格为裁决者：{forbidden}"


def test_sensevoice_evidence_survives_no_events():
    from backend.transcriber_v2 import build_sound_observation

    obs = build_sound_observation([])
    assert obs["event_count"] == 0
    assert obs["events"] == []
    # G11-B：没有分块事实时不写 run 字段——「缺失」的含义是「历史没有记录」，
    # 不得被读成 failed=0。
    assert "run" not in obs


# （原先这里有一条「检查 transcribe 源码里是否出现 observation_evidence」的测试。
#  那是「测代码长什么样」而不是「测软件做什么」——独立条目 6 记的正是这类。
#  下面那条行为测试已经完整覆盖它：收据里真的有这两份证据。）


def test_receipt_carries_observation_evidence(monkeypatch):
    """收据必须留下这两份观察证据。"""
    import backend.transcriber_v2 as tv2
    from test.test_g11_transcribe_orchestration import _stub_environment, _locked_en

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, tv2, events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(tv2, "sample_language_multipoint", _locked_en(events))

    class _Info:
        language = "en"; language_probability = 0.99; duration = 60.0

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = tv2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1

    monkeypatch.setattr(tv2, "transcribe_whisper", lambda *a, **k: ([], _Info()))
    monkeypatch.setattr(tv2, "_whisper_output_to_segments", fake_to_segments)

    envelope = tv2.transcribe(
        "/tmp/g11.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="standard", cache_dir="/tmp/g11cache")

    evidence = envelope.receipt.observation_evidence
    assert "voice_gaps" in evidence, "人声缺口检查结果必须留证"
    assert "sound" in evidence, "SenseVoice 观察证据必须留证"
