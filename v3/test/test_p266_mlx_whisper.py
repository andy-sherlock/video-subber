import inspect
import sys
import types
import unittest
from unittest.mock import patch


class P266MlxWhisperRoutingTests(unittest.TestCase):
    def test_mac_arm64_uses_mlx_whisper(self):
        from backend.core.whisper_runner import choose_whisper_backend

        with patch("backend.core.whisper_runner.platform.system", return_value="Darwin"), \
             patch("backend.core.whisper_runner.platform.machine", return_value="arm64"):
            self.assertEqual(choose_whisper_backend(), "mlx")

    def test_windows_keeps_faster_whisper(self):
        from backend.core.whisper_runner import choose_whisper_backend

        with patch("backend.core.whisper_runner.platform.system", return_value="Windows"), \
             patch("backend.core.whisper_runner.platform.machine", return_value="AMD64"):
            self.assertEqual(choose_whisper_backend(), "faster")

    def test_mlx_transcribe_uses_mlx_model_and_preserves_word_timestamps(self):
        fake_mlx = types.ModuleType("mlx_whisper")
        calls = {}

        def fake_transcribe(audio_path, **kwargs):
            calls["audio_path"] = audio_path
            calls["kwargs"] = kwargs
            return {
                "language": "ja",
                "duration": 2.5,
                "segments": [
                    {
                        "start": 0.1,
                        "end": 1.2,
                        "text": "こんにちは",
                        "words": [
                            {
                                "word": "こんにちは",
                                "start": 0.1,
                                "end": 1.2,
                                "probability": 0.88,
                            }
                        ],
                    }
                ],
            }

        fake_mlx.transcribe = fake_transcribe

        with patch.dict(sys.modules, {"mlx_whisper": fake_mlx}):
            from backend.core.whisper_runner import transcribe_with_mlx_whisper

            segments, info = transcribe_with_mlx_whisper(
                "/tmp/audio.wav",
                model_size="large-v3",
                language="ja",
                vad_threshold=0.0,
            )

        self.assertEqual(calls["audio_path"], "/tmp/audio.wav")
        self.assertEqual(
            calls["kwargs"]["path_or_hf_repo"],
            "mlx-community/whisper-large-v3-mlx",
        )
        self.assertTrue(calls["kwargs"]["word_timestamps"])
        self.assertEqual(calls["kwargs"]["language"], "ja")
        self.assertEqual(info.language, "ja")
        self.assertEqual(info.language_probability, 1.0)
        self.assertEqual(info.duration, 2.5)
        self.assertEqual(segments[0].text, "こんにちは")
        self.assertEqual(segments[0].words[0]["word"], "こんにちは")

    def test_mlx_language_probe_calls_detect_language_directly(self):
        calls = {}

        fake_mel_array = type("FakeArray", (), {
            "astype": lambda self, _dtype: self,
        })()

        fake_dims = types.SimpleNamespace(n_mels=128)
        fake_model = types.SimpleNamespace(
            dims=fake_dims,
            detect_language=lambda mel_seg: (
                None,
                {"ja": 0.93, "en": 0.04, "zh": 0.02},
            ),
        )

        fake_mx = types.ModuleType("mlx")
        fake_mx_core = types.ModuleType("mlx.core")
        fake_mx_core.float16 = "float16"
        fake_mx.core = fake_mx_core

        fake_audio = types.ModuleType("mlx_whisper.audio")
        def fake_load_audio(path):
            calls["audio_path"] = path
            return "fake_audio_data"
        fake_audio.load_audio = fake_load_audio
        fake_audio.log_mel_spectrogram = lambda audio, n_mels=80, padding=0: fake_mel_array
        fake_audio.pad_or_trim = lambda mel, n_frames, axis=-2: fake_mel_array
        fake_audio.N_FRAMES = 3000
        fake_audio.N_SAMPLES = 480000

        fake_load_models = types.ModuleType("mlx_whisper.load_models")
        def fake_load_model(repo, dtype=None):
            calls["repo"] = repo
            calls["dtype"] = dtype
            return fake_model
        fake_load_models.load_model = fake_load_model

        fake_mlx_whisper = types.ModuleType("mlx_whisper")

        with patch.dict(sys.modules, {
                 "mlx": fake_mx,
                 "mlx.core": fake_mx_core,
                 "mlx_whisper": fake_mlx_whisper,
                 "mlx_whisper.audio": fake_audio,
                 "mlx_whisper.load_models": fake_load_models,
             }), \
             patch("backend.core.whisper_runner.choose_whisper_backend", return_value="mlx"), \
             patch("backend.core.whisper_runner._create_language_probe_clip", return_value="/tmp/probe.wav") as make_clip, \
             patch("backend.core.whisper_runner.os.unlink") as unlink:
            from backend.core.whisper_runner import detect_language_with_whisper

            info = detect_language_with_whisper(
                "/tmp/very-long-audio.wav",
                model_size="large-v3",
                language=None,
                probe_seconds=30,
            )

        make_clip.assert_called_once_with("/tmp/very-long-audio.wav", 30)
        self.assertEqual(calls["audio_path"], "/tmp/probe.wav")
        self.assertEqual(calls["repo"], "mlx-community/whisper-large-v3-mlx")
        unlink.assert_called_once_with("/tmp/probe.wav")
        self.assertEqual(info.language, "ja")
        self.assertEqual(info.language_probability, 0.93)
        self.assertEqual(info.backend, "mlx")

    def test_create_language_probe_clip_extracts_short_audio(self):
        with patch("backend.core.whisper_runner.tempfile.mkstemp", return_value=(123, "/tmp/probe.wav")), \
             patch("backend.core.whisper_runner.os.close") as close_fd, \
             patch("backend.core.whisper_runner.subprocess.run") as run:
            from backend.core.whisper_runner import _create_language_probe_clip

            probe_path = _create_language_probe_clip("/tmp/very-long-audio.wav", 45)

        self.assertEqual(probe_path, "/tmp/probe.wav")
        close_fd.assert_called_once_with(123)
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("/tmp/very-long-audio.wav", cmd)
        self.assertIn("/tmp/probe.wav", cmd)
        self.assertIn("45", cmd)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_env_check_uses_find_spec_without_importing_mlx(self):
        from backend import env_check

        def fail_if_imports_mlx(name, *args, **kwargs):
            if name == "mlx_whisper":
                raise AssertionError("check_whisper must not import mlx_whisper")
            return original_import(name, *args, **kwargs)

        original_import = __import__
        with patch("backend.env_check.sys.platform", "darwin"), \
             patch("backend.env_check.platform_machine", return_value="arm64"), \
             patch("backend.env_check.importlib.util.find_spec", return_value=object()), \
             patch("builtins.__import__", side_effect=fail_if_imports_mlx):
            result = env_check.check_whisper()

        self.assertTrue(result["installed"])
        self.assertEqual(result["backend"], "mlx")


def test_standard_router_detects_language_before_full_whisper():
    import backend.transcriber_v2 as transcriber_v2

    # V3.08 复杂度拆分：标准模式语言探测移入 _run_standard_language_router，
    # 全量转录移入 _run_full_whisper；仍需保证 transcribe() 中探测先于全量转录。
    source = inspect.getsource(transcriber_v2.transcribe)
    probe_at = source.index("_run_standard_language_router")
    full_whisper_at = source.index("_run_full_whisper")

    assert probe_at < full_whisper_at
    assert "use_standard_router" in source
    assert "_standard_primary == \"whisper\"" in source

    # 探测在辅助函数内调用 Whisper 语言检测；全量转录在 _run_full_whisper 内调用 Whisper
    router_src = inspect.getsource(transcriber_v2._run_standard_language_router)
    assert "detect_language_with_whisper" in router_src
    full_src = inspect.getsource(transcriber_v2._run_full_whisper)
    assert "transcribe_whisper" in full_src


def test_standard_qwen_path_skips_full_whisper(monkeypatch):
    import backend.transcriber_v2 as transcriber_v2

    fake_hw = types.ModuleType("backend.core.hardware_detector")
    fake_hw.detect_hardware = lambda: {"ram_gb": 32}
    monkeypatch.setitem(sys.modules, "backend.core.hardware_detector", fake_hw)

    qwen_calls = {}
    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    fake_qwen.is_qwen_asr_available = lambda: True

    def fake_run_qwen_asr(audio_path, language=None, cache_dir=None, **_kwargs):
        qwen_calls["audio_path"] = audio_path
        qwen_calls["language"] = language
        qwen_calls["cache_dir"] = cache_dir
        return [{
            "text": "こんにちは",
            "t_start": 0.0,
            "t_end": 1.0,
            "segments": [{"text": "こんにちは", "start": 0.0, "end": 1.0}],
        }]

    fake_qwen.run_qwen_asr = fake_run_qwen_asr
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_qwen)

    fake_sv = types.ModuleType("backend.core.sensevoice_runner")
    fake_sv.is_sensevoice_available = lambda: False
    fake_sv.run_sensevoice = lambda *_args, **_kwargs: []
    monkeypatch.setitem(sys.modules, "backend.core.sensevoice_runner", fake_sv)

    info = types.SimpleNamespace(
        language="ja",
        language_probability=0.95,
        duration=30.0,
    )
    monkeypatch.setattr(
        transcriber_v2,
        "detect_language_with_whisper",
        lambda *_args, **_kwargs: info,
    )

    def fail_full_whisper(*_args, **_kwargs):
        raise AssertionError("standard qwen path must not run full Whisper")

    monkeypatch.setattr(transcriber_v2, "transcribe_whisper", fail_full_whisper)
    monkeypatch.setattr(transcriber_v2, "release_whisper_model", lambda: None)
    monkeypatch.setattr(
        transcriber_v2,
        "_qwen_chunks_to_segments",
        lambda _chunks, _lang: [transcriber_v2.Segment(0, 0.0, 1.0, "こんにちは")],
    )
    monkeypatch.setattr(
        transcriber_v2,
        "_smart_assemble",
        lambda _raw, **_kwargs: [transcriber_v2.Segment(0, 0.0, 1.0, "こんにちは")],
    )
    monkeypatch.setattr(
        transcriber_v2,
        "merge_short_subtitle_fragments",
        lambda segments, _lang, voice_timeline=None: segments,
    )
    monkeypatch.setattr(
        transcriber_v2,
        "filter_hallucinations",
        lambda segments, *_args, **_kwargs: segments,
    )

    segments = transcriber_v2.transcribe(
        "/tmp/very-long-audio.wav",
        model_size="large-v3",
        source_lang="auto",
        skip_vad=True,
        filter_keywords=True,
        speaker_detect=False,
        transcription_tier="standard",
        cache_dir="/tmp/cache",
    )

    assert [segment.text for segment in segments] == ["こんにちは"]
    assert qwen_calls == {
        "audio_path": "/tmp/very-long-audio.wav",
        "language": "ja",
        "cache_dir": "/tmp/cache",
    }


if __name__ == "__main__":
    unittest.main()
