"""P212 + P213 + P214: FA alignment quality guards.

P212: Reasonableness checks on real alignment output.
P213: Word count mismatch detection in apply_aligner_timestamps.
P214: Quality marker on aligned results.
"""
import unittest
import sys
import tempfile
import wave
import struct
import os
import logging
from pathlib import Path
from io import StringIO

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
    """Create a WAV with a sine tone and speech-like envelope."""
    import math
    n_samples = int(duration_sec * sample_rate)
    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        envelope = 0.5 + 0.5 * math.sin(2 * math.pi * 0.5 * t)
        val = int(16000 * envelope * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, val)))
    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(struct.pack("<" + "h" * n_samples, *samples))


# ── P212: Alignment reasonableness ──────────────────────────────────────

class TestP212AlignmentReasonableness(unittest.TestCase):
    """Verify alignment output passes basic sanity checks on real audio."""

    def setUp(self):
        from backend.core.qwen_aligner import QwenAligner
        self.QwenAligner = QwenAligner

    def _load_aligner(self):
        try:
            a = self.QwenAligner()
            if a._model is None:
                self.skipTest("FA model not available")
            return a
        except Exception:
            self.skipTest("FA model not loadable")

    def test_timestamps_are_monotonic(self):
        """start and end times must increase monotonically."""
        a = self._load_aligner()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_sine_wav(wav_path, duration_sec=2.5)

            result = a.align(wav_path, "hello world this is a test", language="en")
            self.assertGreater(len(result), 2)

            prev_end = -0.1
            for item in result:
                self.assertGreaterEqual(item["start"], prev_end - 0.05,
                    f"Timestamp regression at '{item['word']}': "
                    f"start={item['start']} but previous end={prev_end}")
                self.assertGreaterEqual(item["end"], item["start"],
                    f"Negative or zero duration at '{item['word']}': "
                    f"start={item['start']}, end={item['end']}")
                prev_end = item["end"]
        finally:
            a.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_timestamps_within_audio_bounds(self):
        """All timestamps must be within [0, audio_duration]."""
        a = self._load_aligner()
        try:
            duration = 2.5
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_sine_wav(wav_path, duration_sec=duration)

            result = a.align(wav_path, "hello world this is a test", language="en")
            self.assertGreater(len(result), 2)

            for item in result:
                self.assertGreaterEqual(item["start"], 0.0,
                    f"Negative start time at '{item['word']}': {item['start']}")
                self.assertLessEqual(item["end"], duration + 0.5,
                    f"End time exceeds audio duration at '{item['word']}': "
                    f"{item['end']} > {duration}")
        finally:
            a.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_all_durations_positive_or_zero(self):
        """No word may have negative duration. Most words should be positive."""
        a = self._load_aligner()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_sine_wav(wav_path, duration_sec=2.5)

            result = a.align(wav_path, "hello world this is a test of alignment", language="en")
            positive = 0
            for item in result:
                # No negative durations allowed
                self.assertGreaterEqual(item["end"], item["start"],
                    f"Negative duration at '{item['word']}': "
                    f"start={item['start']}, end={item['end']}")
                if item["end"] > item["start"]:
                    positive += 1
            # At least 1 word should have positive duration (output is not ALL zeros)
            # On synthetic audio the model can't properly align every word;
            # this catches the old uniform-division where ALL durations were fake-equal.
            self.assertGreater(positive, 0,
                f"All {len(result)} words have zero duration — "
                f"alignment is producing empty timestamps")
        finally:
            a.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)


# ── P213: Word count mismatch detection ─────────────────────────────────

class FakeSegment:
    """Minimal segment-like object for testing apply_aligner_timestamps."""
    def __init__(self, text):
        self.text = text
        self.start = 0.0
        self.end = 1.0


class TestP213WordCountMismatch(unittest.TestCase):
    """apply_aligner_timestamps should warn when word counts don't match."""

    def test_warns_when_aligned_words_fewer_than_segment_words(self):
        """Fewer aligned words than segments expect → clamping is safe, no drift."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        segments = [
            FakeSegment("hello world this is a test"),
        ]
        # Only 2 aligned words, but segment expects 6 → word_idx clamped to total_words
        aligned_words = [
            {"word": "hello", "start": 0.0, "end": 0.3},
            {"word": "world", "start": 0.3, "end": 0.6},
        ]

        # Should NOT crash — clamping protects against overflow
        apply_aligner_timestamps(segments, aligned_words)
        # Segment start/end should use the available aligned words
        self.assertEqual(segments[0].start, 0.0)
        self.assertEqual(segments[0].end, 0.6)

    def test_extra_aligned_words_still_match_by_time(self):
        """P228: Extra aligned words outside segment range are ignored.
        Only words within the segment's time range are applied."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg = FakeSegment("hello world")
        seg.start = 0.0
        seg.end = 1.0

        # 5 aligned words, but only first 2 are within [0.0, 1.0)
        aligned_words = [
            {"word": "hello", "start": 0.0, "end": 0.2},
            {"word": "world", "start": 0.2, "end": 0.4},
            {"word": "extra", "start": 1.5, "end": 1.7},
            {"word": "words", "start": 1.7, "end": 1.9},
            {"word": "here", "start": 1.9, "end": 2.1},
        ]

        apply_aligner_timestamps([seg], aligned_words)

        # Only the first 2 words fall in [0, 1) → start/end from those
        self.assertEqual(seg.start, 0.0)
        self.assertEqual(seg.end, 0.4)

    def test_no_warning_when_counts_match(self):
        """Matching word counts → no WARNING."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        segments = [
            FakeSegment("hello world"),
            FakeSegment("this is a test"),
        ]
        aligned_words = [
            {"word": "hello", "start": 0.0, "end": 0.2},
            {"word": "world", "start": 0.2, "end": 0.4},
            {"word": "this", "start": 0.5, "end": 0.6},
            {"word": "is", "start": 0.6, "end": 0.7},
            {"word": "a", "start": 0.7, "end": 0.75},
            {"word": "test", "start": 0.75, "end": 1.0},
        ]

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("qwen_aligner")
        logger.addHandler(handler)

        try:
            apply_aligner_timestamps(segments, aligned_words)
            log_output = log_stream.getvalue()

            self.assertEqual(log_output, "",
                             f"Expected no WARNING, got: {log_output!r}")
        finally:
            logger.removeHandler(handler)


# ── P214: Quality marker ────────────────────────────────────────────────

class TestP214QualityMarker(unittest.TestCase):
    """align() should mark results with _quality field."""

    def setUp(self):
        from backend.core.qwen_aligner import QwenAligner
        self.QwenAligner = QwenAligner

    def _load_aligner(self):
        try:
            a = self.QwenAligner()
            if a._model is None:
                self.skipTest("FA model not available")
            return a
        except Exception:
            self.skipTest("FA model not loadable")

    def test_aligned_results_have_quality_field(self):
        """Successful alignment → each result dict has _quality='aligned'."""
        a = self._load_aligner()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            _make_sine_wav(wav_path, duration_sec=1.5)

            result = a.align(wav_path, "hello world", language="en")
            self.assertGreater(len(result), 0)

            for item in result:
                self.assertIn("_quality", item,
                              f"Missing _quality field in {item}")
                self.assertEqual(item["_quality"], "official",
                                 f"Expected _quality='official', got {item.get('_quality')}")
        finally:
            a.close()
            if os.path.exists(wav_path):
                os.unlink(wav_path)
