"""P207: Qwen3-ForcedAligner official API — anti-fabrication tests.

Verifies the aligner returns real timestamps from the official
Qwen3ForcedAligner.from_pretrained() + model.align() API, not
uniformly-divided fake timestamps.
"""
import unittest
import sys
import tempfile
import wave
import struct
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_test_wav(path: str, duration_sec: float = 2.0, sample_rate: int = 16000):
    """Create a minimal silent WAV file for testing."""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))


def _make_sine_wav(path: str, duration_sec: float = 2.0, sample_rate: int = 16000,
                   freq: float = 440.0):
    """Create a WAV with a sine tone (for better alignment signal)."""
    import math
    n_samples = int(duration_sec * sample_rate)
    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        # Vary amplitude to simulate speech-like envelope
        envelope = 0.5 + 0.5 * math.sin(2 * math.pi * 0.5 * t)  # 0.5 Hz envelope
        val = int(16000 * envelope * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, val)))
    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(struct.pack("<" + "h" * n_samples, *samples))


class TestP207AntiFabrication(unittest.TestCase):
    """Verify aligner returns real timestamps, not uniform fake data."""

    def setUp(self):
        from backend.core.qwen_aligner import QwenAligner
        self.QwenAligner = QwenAligner

    def test_align_returns_varied_timestamps(self):
        """Timestamps should vary naturally — not all identical duration."""
        aligner = None
        try:
            aligner = self.QwenAligner()
            if aligner._model is None:
                self.skipTest("FA model not available")
        except Exception:
            self.skipTest("FA model not loadable")

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_sine_wav(wav_path, duration_sec=2.5)

            # Multi-word text
            text = "hello world this is a test of the alignment system"
            result = aligner.align(wav_path, text, language="en")

            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 2,
                               "Should return multiple word entries")

            # Key anti-fabrication check: durations should NOT all be equal
            durations = [w["end"] - w["start"] for w in result if "start" in w and "end" in w]
            unique_durations = set(round(d, 6) for d in durations)
            self.assertGreater(len(unique_durations), 1,
                               f"All word durations identical ({durations[0] if durations else 'N/A'}s) "
                               f"— aligner is fabricating uniform timestamps!")
        finally:
            if aligner:
                aligner.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_output_dict_keys(self):
        """Each result dict must have 'word', 'start', 'end' keys."""
        aligner = None
        try:
            aligner = self.QwenAligner()
            if aligner._model is None:
                self.skipTest("FA model not available")
        except Exception:
            self.skipTest("FA model not loadable")

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_sine_wav(wav_path, duration_sec=1.5)

            result = aligner.align(wav_path, "hello world", language="en")
            self.assertGreater(len(result), 0)

            for item in result:
                self.assertIn("word", item, f"Missing 'word' key in {item}")
                self.assertIn("start", item, f"Missing 'start' key in {item}")
                self.assertIn("end", item, f"Missing 'end' key in {item}")
                self.assertIsInstance(item["start"], (int, float))
                self.assertIsInstance(item["end"], (int, float))
                self.assertGreaterEqual(item["end"], item["start"],
                                        f"end < start: {item}")
        finally:
            if aligner:
                aligner.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)


class TestP207EdgeCases(unittest.TestCase):
    """Edge case handling."""

    def setUp(self):
        from backend.core.qwen_aligner import QwenAligner
        self.QwenAligner = QwenAligner

    def test_align_empty_text_returns_empty(self):
        """Empty or whitespace-only text returns []."""
        aligner = None
        try:
            aligner = self.QwenAligner()
            if aligner._model is None:
                self.skipTest("FA model not available")
        except Exception:
            self.skipTest("FA model not loadable")

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_test_wav(wav_path, duration_sec=1.0)

            self.assertEqual(aligner.align(wav_path, "", language="en"), [])
            self.assertEqual(aligner.align(wav_path, "   ", language="en"), [])
        finally:
            if aligner:
                aligner.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_align_closed_raises(self):
        """Calling align() after close() raises RuntimeError."""
        aligner = None
        try:
            aligner = self.QwenAligner()
            if aligner._model is None:
                self.skipTest("FA model not available")
        except Exception:
            self.skipTest("FA model not loadable")

        aligner.close()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            _make_test_wav(wav_path, duration_sec=1.0)
            with self.assertRaises(RuntimeError):
                aligner.align(wav_path, "hello", language="en")
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_align_propagates_error_no_silent_fallback(self):
        """Errors from official API must propagate — no silent fake data."""
        # Create an aligner, close it properly, then verify it refuses align()
        aligner = None
        try:
            aligner = self.QwenAligner()
            if aligner._model is None:
                self.skipTest("FA model not available")
        except Exception:
            self.skipTest("FA model not loadable")

        # close() should set _closed flag
        aligner.close()
        self.assertTrue(aligner._closed)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            _make_test_wav(wav_path, duration_sec=1.0)
            # This should raise, NOT return fake evenly-spaced timestamps
            with self.assertRaises(RuntimeError):
                aligner.align(wav_path, "hello world", language="en")
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_nonexistent_audio_raises(self):
        """Passing a nonexistent file path should raise an error."""
        aligner = None
        try:
            aligner = self.QwenAligner()
            if aligner._model is None:
                self.skipTest("FA model not available")
        except Exception:
            self.skipTest("FA model not loadable")

        try:
            # Official API should raise on missing file, not silently return fake data
            with self.assertRaises(Exception):
                aligner.align("/nonexistent/path/audio.wav", "hello", language="en")
        finally:
            if aligner:
                aligner.close()


class TestP207Availability(unittest.TestCase):
    """is_qwen_aligner_available() detection."""

    def test_available_when_package_and_model_present(self):
        """Returns True when qwen-asr is installed and model is cached."""
        from backend.core.qwen_aligner import is_qwen_aligner_available

        # qwen-asr 0.0.6 IS installed, model IS cached
        result = is_qwen_aligner_available()
        self.assertTrue(result,
                        "is_qwen_aligner_available() should return True: "
                        "qwen-asr installed, model cached")

    def test_unavailable_when_package_missing(self):
        """Returns False when qwen_asr cannot be imported."""
        with unittest.mock.patch.dict(sys.modules, {"qwen_asr": None}):
            # Force re-import by clearing the cached module
            import backend.core.qwen_aligner as m
            # Simulate: the import guard at top of qwen_aligner.py would catch ImportError
            # and is_qwen_aligner_available should check for qwen_asr
            with unittest.mock.patch.object(m, '_has_qwen_asr_package', False):
                result = m.is_qwen_aligner_available()
                self.assertFalse(result)
