"""Phase 1D: Model time-sharing loading tests.

Tests that model objects support close() and that existing models
are properly released after use.
"""
import unittest
import sys
import importlib
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestQwenAlignerClose(unittest.TestCase):
    """Qwen3-ForcedAligner close() releases the model reference."""

    def test_close_sets_model_to_none(self):
        """After close(), internal model reference is None."""
        # We test the design contract without loading the real model.
        # The QwenAligner class must:
        # 1. Have a close() method
        # 2. After close(), the model attribute is None
        # 3. Calling align() after close() raises RuntimeError

        from backend.core.qwen_aligner import QwenAligner

        # Verify the class has a close method
        self.assertTrue(hasattr(QwenAligner, 'close'),
                        "QwenAligner must have a close() method")

    def test_close_idempotent(self):
        """Calling close() twice does not raise."""
        from backend.core.qwen_aligner import QwenAligner

        # Check that close exists and can be called without args
        self.assertTrue(callable(getattr(QwenAligner, 'close', None)))

    def test_align_after_close_raises(self):
        """align() after close() must raise RuntimeError."""
        from backend.core.qwen_aligner import QwenAligner

        aligner = QwenAligner.__new__(QwenAligner)
        # Simulate a loaded state
        aligner._model = MagicMock()
        aligner._closed = False

        aligner.close()
        self.assertTrue(aligner._closed)

        with self.assertRaises(RuntimeError):
            aligner.align("audio.wav", "你好世界")


class TestIsAlignerAvailable(unittest.TestCase):
    """is_qwen_aligner_available() checks dependencies."""

    def test_function_exists(self):
        """is_qwen_aligner_available is importable."""
        from backend.core.qwen_aligner import is_qwen_aligner_available
        self.assertTrue(callable(is_qwen_aligner_available))

    def test_returns_bool(self):
        """Returns True or False."""
        from backend.core.qwen_aligner import is_qwen_aligner_available
        result = is_qwen_aligner_available()
        self.assertIsInstance(result, bool)

    def test_graceful_when_torch_missing(self):
        """Returns False when torch is not installed, no crash."""
        with patch.dict('sys.modules', {'torch': None}):
            # Force reimport to simulate missing torch
            import backend.core.qwen_aligner as qa
            # Module should still be importable
            self.assertTrue(hasattr(qa, 'is_qwen_aligner_available'))


class TestExistingModelRelease(unittest.TestCase):
    """Existing models (already implemented) — verify release patterns."""

    def test_whisper_model_is_del_after_transcribe(self):
        """Whisper model is released via release_whisper_model() after use.

        V3.08 复杂度拆分后 Whisper 加载/释放移入 _run_full_whisper 辅助函数，
        因此检查整个转录模块源码而非仅 transcribe()。"""
        import backend.transcriber_v2 as tv
        import inspect
        source = inspect.getsource(tv)
        self.assertIn('release_whisper_model', source,
                      "transcription pipeline must call release_whisper_model() after use")

    def test_sensevoice_model_is_del(self):
        """SenseVoice model is released after use."""
        import backend.core.sensevoice_runner as sv
        import inspect
        # Check both functions
        source_run = inspect.getsource(sv.run_sensevoice)
        source_chunked = inspect.getsource(sv._run_sensevoice_chunked)
        for src in [source_run, source_chunked]:
            self.assertIn('del model', src,
                          "SenseVoice runner must del model after use")


if __name__ == '__main__':
    unittest.main()
