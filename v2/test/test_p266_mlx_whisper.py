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
                "language_probability": 0.91,
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
        self.assertEqual(info.language_probability, 0.91)
        self.assertEqual(info.duration, 2.5)
        self.assertEqual(segments[0].text, "こんにちは")
        self.assertEqual(segments[0].words[0]["word"], "こんにちは")

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


if __name__ == "__main__":
    unittest.main()
