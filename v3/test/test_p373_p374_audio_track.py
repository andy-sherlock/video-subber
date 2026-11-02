from types import SimpleNamespace

from backend.core import extractor
from backend import worker


def test_has_audio_stream_returns_three_states(monkeypatch):
    monkeypatch.setattr(extractor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="0\n"))
    assert extractor.has_audio_stream("video.mp4") == "has_audio"
    monkeypatch.setattr(extractor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=""))
    assert extractor.has_audio_stream("video.mp4") == "no_audio"
    monkeypatch.setattr(extractor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="bad"))
    assert extractor.has_audio_stream("video.mp4") == "unknown"


def test_extract_audio_checks_source_even_when_audio_cache_exists(monkeypatch, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"cached")
    calls = []
    monkeypatch.setattr("backend.core.extractor.get_video_resolution", lambda path: (640, 360))
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: calls.append(path) or "has_audio")
    monkeypatch.setattr(worker, "extract_audio", lambda *a: (_ for _ in ()).throw(AssertionError("不得重抽")))

    result = worker._step_extract_audio("transcribe", "video.mp4", audio, {}, SimpleNamespace(save=lambda *a, **k: None))

    assert result == (640, 360, "has_audio")
    assert calls == ["video.mp4"]


def test_output_audio_matrix_rejects_silent_product_from_audio_source(monkeypatch):
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: "no_audio")
    try:
        worker._validate_output_audio("has_audio", "burn", "result.mp4")
    except worker.SubtitleOutputError as error:
        assert "音轨" in str(error)
    else:
        raise AssertionError("有声源生成无声成品必须失败")


def test_output_audio_matrix_allows_silent_source_and_skips_srt(monkeypatch):
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: "no_audio")
    assert worker._validate_output_audio("no_audio", "burn", "result.mp4") == "no_audio"
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: (_ for _ in ()).throw(AssertionError("SRT 不得探测成品")))
    assert worker._validate_output_audio("has_audio", "srt", None) is None
