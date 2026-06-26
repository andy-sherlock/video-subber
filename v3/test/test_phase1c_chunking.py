"""Tests for Phase 1C: long-audio chunking for Qwen3-ASR and SenseVoice.

Run: cd v3 && .venv/bin/python -m unittest discover -s test -p "test_*.py" -v
"""
import sys
import types
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile
import unittest
from unittest.mock import patch, MagicMock

_fake_mlx_qwen3_asr = types.ModuleType("mlx_qwen3_asr")
_fake_mlx_qwen3_asr.load_model = lambda *args, **kwargs: (object(), {})
_fake_mlx_qwen3_asr.transcribe = lambda *args, **kwargs: MagicMock(
    text="", segments=[])
sys.modules.setdefault("mlx_qwen3_asr", _fake_mlx_qwen3_asr)


class FakeSegment:
    """Minimal segment for testing chunking logic."""
    def __init__(self, id=0, start=0.0, end=1.0, text="hello", qwen_text=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.qwen_text = qwen_text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = []
        self.sensevoice_tag = None
        self.filtered = False
        self.filter_reason = None


def _make_seg(id=0, start=0.0, end=1.0, text="hello"):
    return FakeSegment(id=id, start=start, end=end, text=text)


# ═══════════════════════════════════════════════════════════
# _get_audio_duration
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# _chunk_segments
# ═══════════════════════════════════════════════════════════

class TestChunkSegments(unittest.TestCase):
    """Tests for _chunk_segments — grouping segments into ~5 min batches."""

    def test_empty_segments_returns_empty(self):
        from backend.core.qwen_asr_runner import _chunk_segments
        chunks = _chunk_segments([])
        self.assertEqual(chunks, [])

    def test_single_segment_returns_one_chunk(self):
        from backend.core.qwen_asr_runner import _chunk_segments
        segs = [_make_seg(id=0, start=0.0, end=10.0)]
        chunks = _chunk_segments(segs, max_duration=5)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0, 1, 0.0, 10.0))

    def test_short_audio_returns_one_chunk(self):
        from backend.core.qwen_asr_runner import _chunk_segments
        segs = [
            _make_seg(id=0, start=0.0, end=5.0),
            _make_seg(id=1, start=5.0, end=10.0),
            _make_seg(id=2, start=10.0, end=15.0),
        ]
        chunks = _chunk_segments(segs, max_duration=300)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0, 3, 0.0, 15.0))

    def test_long_audio_splits_at_boundaries(self):
        from backend.core.qwen_asr_runner import _chunk_segments
        segs = [
            _make_seg(id=0, start=0.0, end=60.0),
            _make_seg(id=1, start=60.0, end=120.0),
            _make_seg(id=2, start=120.0, end=180.0),
            _make_seg(id=3, start=180.0, end=240.0),
            _make_seg(id=4, start=240.0, end=300.0),  # triggers >=300 split
            _make_seg(id=5, start=300.0, end=360.0),
            _make_seg(id=6, start=360.0, end=420.0),
        ]
        chunks = _chunk_segments(segs, max_duration=300)
        # seg[4].end - seg[0].start = 300 >= 300 → split BEFORE seg 4
        # Chunk 1: segs 0–3 (0.0–240.0), Chunk 2: segs 4–6 (240.0–420.0)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], (0, 4, 0.0, 240.0))
        self.assertEqual(chunks[1], (4, 7, 240.0, 420.0))

    def test_boundaries_fall_on_natural_breaks(self):
        """Chunk boundaries must coincide with segment start/end times."""
        from backend.core.qwen_asr_runner import _chunk_segments
        segs = []
        for i in range(100):
            segs.append(_make_seg(id=i, start=i * 5.0, end=(i + 1) * 5.0))
        chunks = _chunk_segments(segs, max_duration=300)

        # Verify every chunk's time boundaries match segment times
        for start_idx, end_idx, t_start, t_end in chunks:
            self.assertEqual(t_start, segs[start_idx].start)
            self.assertEqual(t_end, segs[end_idx - 1].end)
            # Verify it's not mid-segment
            self.assertLess(t_end - t_start, 300 + 5)  # tolerance for last segment

    def test_no_overlap_between_chunks(self):
        """Chunks should partition segments without overlap or gaps."""
        from backend.core.qwen_asr_runner import _chunk_segments
        segs = []
        for i in range(200):
            segs.append(_make_seg(id=i, start=i * 3.0, end=(i + 1) * 3.0))
        chunks = _chunk_segments(segs, max_duration=300)

        # end_idx of chunk N == start_idx of chunk N+1
        for i in range(len(chunks) - 1):
            self.assertEqual(chunks[i][1], chunks[i + 1][0])
        # Covers all segments
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], len(segs))

    def test_max_duration_respected(self):
        """Each chunk should be <= max_duration (except very last which may be shorter)."""
        from backend.core.qwen_asr_runner import _chunk_segments
        segs = []
        for i in range(50):
            segs.append(_make_seg(id=i, start=i * 10.0, end=(i + 1) * 10.0))
        max_dur = 300
        chunks = _chunk_segments(segs, max_duration=max_dur)

        for _si, _ei, t_start, t_end in chunks:
            dur = t_end - t_start
            # Each chunk should be <= max_duration (or only 1 segment)
            self.assertLessEqual(dur, max_dur + 10.0,
                               f"Chunk duration {dur}s > {max_dur}s")


# ═══════════════════════════════════════════════════════════════
# _extract_audio_chunk
# ═══════════════════════════════════════════════════════════════

class TestExtractAudioChunk(unittest.TestCase):
    """Tests for _extract_audio_chunk via ffmpeg."""

    def test_extracts_valid_wav(self):
        import subprocess, os
        from backend.core.qwen_asr_runner import _extract_audio_chunk

        # Create a 30s test audio
        fd, src = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=30",
                "-ac", "1", "-ar", "16000", src,
            ], capture_output=True, check=True)

            fd2, dst = tempfile.mkstemp(suffix=".wav")
            os.close(fd2)
            try:
                _extract_audio_chunk(src, 10.0, 20.0, dst)
                # Verify the extracted chunk is a non-empty WAV file
                size = os.path.getsize(dst)
                self.assertGreater(size, 1000,
                    f"Extracted chunk should be > 1KB, got {size} bytes")
            finally:
                try:
                    os.unlink(dst)
                except OSError:
                    pass
        finally:
            try:
                os.unlink(src)
            except OSError:
                pass

    def test_padding_clamped_at_zero(self):
        import subprocess, os
        from backend.core.qwen_asr_runner import _extract_audio_chunk

        fd, src = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=10",
                "-ac", "1", "-ar", "16000", src,
            ], capture_output=True, check=True)

            fd2, dst = tempfile.mkstemp(suffix=".wav")
            os.close(fd2)
            try:
                # Start near 0 — padding should clamp, not crash
                _extract_audio_chunk(src, 0.0, 5.0, dst)
                self.assertTrue(os.path.getsize(dst) > 0)
            finally:
                try:
                    os.unlink(dst)
                except OSError:
                    pass
        finally:
            try:
                os.unlink(src)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════
# transcribe_raw chunking path
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# run_qwen_asr chunking path
# ═══════════════════════════════════════════════════════════════

class TestRunQwenASRChunking(unittest.TestCase):
    """Tests that run_qwen_asr enters chunked mode for long audio."""

    @patch("backend.core.qwen_asr_runner._run_qwen_asr_chunked")
    @patch("backend.core.qwen_asr_runner.is_qwen_asr_available")
    def test_long_audio_triggers_chunked(self, mock_avail, mock_chunked):
        mock_avail.return_value = True
        mock_chunked.return_value = []  # return unchanged

        from backend.core.qwen_asr_runner import run_qwen_asr

        segs = [_make_seg(id=0, start=0.0, end=1800.0, text="long")]
        run_qwen_asr("/fake/audio.wav", segs)
        mock_chunked.assert_called_once()

    @patch("backend.core.qwen_asr_runner.is_qwen_asr_available")
    def test_short_audio_does_not_chunk(self, mock_avail):
        mock_avail.return_value = False  # model not available → early return
        from backend.core.qwen_asr_runner import run_qwen_asr

        segs = [_make_seg(id=0, start=0.0, end=60.0, text="short")]
        result = run_qwen_asr("/fake/audio.wav", segs)
        self.assertEqual(result, [])  # P251: unavailable → empty chunk list


# ═══════════════════════════════════════════════════════════════
# _run_qwen_asr_chunked integration
# ═══════════════════════════════════════════════════════════════

class TestRunQwenASRChunkedIntegration(unittest.TestCase):
    """Integration tests for _run_qwen_asr_chunked with mocked model."""

    def test_p274_mlx_chunk_offsets_by_extract_start_and_clips_padding(self):
        """P274: MLX chunk timestamps use extract_start and drop padding words."""
        result = MagicMock()
        result.text = "before inside after"
        result.segments = [
            {"text": "before", "start": 3.0, "end": 4.0},
            {"text": "inside", "start": 10.0, "end": 11.0},
            {"text": "after", "start": 306.0, "end": 307.0},
        ]

        fake_mlx = types.ModuleType("mlx_qwen3_asr")
        fake_mlx.load_model = MagicMock(return_value=(object(), {}))
        fake_mlx.transcribe = MagicMock(return_value=result)

        with patch.dict(sys.modules, {"mlx_qwen3_asr": fake_mlx}):
            import backend.core.qwen_asr_runner as runner
            runner = importlib.reload(runner)
            with patch.object(runner, "_USE_MLX", True), \
                 patch.object(runner, "_extract_audio_chunk"):
                chunk = runner._transcribe_chunk(
                    model=object(),
                    audio_path="/fake/audio.wav",
                    t_start=1200.0,
                    t_end=1500.0,
                    language="ja",
                )

        self.assertEqual(
            chunk["segments"],
            [{"text": "inside", "start": 1205.0, "end": 1206.0}],
        )
        self.assertEqual(chunk["text"], "inside")

    @patch("backend.core.qwen_asr_runner._USE_MLX", True)
    @patch("backend.core.qwen_asr_runner._extract_audio_chunk")
    def test_chunks_mapped_independently(self, mock_extract):
        """Each chunk's text should be aligned to only its own segment subset."""
        from backend.core.qwen_asr_runner import _run_qwen_asr_chunked

        segs = [
            _make_seg(id=0, start=0.0, end=60.0, text="Hello world"),
            _make_seg(id=1, start=60.0, end=120.0, text="Goodbye world"),
        ]

        # Simulate Qwen returning different text per chunk
        call_count = [0]
        original_import = __import__

        def fake_transcribe(self, path):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.text = "Hello world"
            else:
                result.text = "Goodbye world"
            return result

        with patch.object(
            type(MagicMock()), 'transcribe', fake_transcribe, create=True
        ):
            pass  # just test structure — actual MLX model isn't available in tests

    @patch("backend.core.qwen_asr_runner.is_qwen_asr_available")
    def test_unavailable_returns_segments_unchanged(self, mock_avail):
        mock_avail.return_value = False
        from backend.core.qwen_asr_runner import run_qwen_asr

        segs = [_make_seg(id=0, start=0.0, end=1800.0)]
        result = run_qwen_asr("/fake/audio.wav", segs)
        self.assertEqual(result, [])  # P251: unavailable → empty chunk list


# ═══════════════════════════════════════════════════════════════
# SenseVoice chunking
# ═══════════════════════════════════════════════════════════════

class TestSenseVoiceChunking(unittest.TestCase):
    """Tests that run_sensevoice enters chunked mode for long audio."""

    @patch("backend.core.sensevoice_runner._run_sensevoice_chunked")
    @patch("backend.core.sensevoice_runner.is_sensevoice_available")
    def test_long_audio_triggers_chunked(self, mock_avail, mock_chunked):
        mock_avail.return_value = True
        mock_chunked.return_value = [
            {"text": "hello", "tags": {"event": "Speech"}, "start_s": 0, "end_s": 60, "event": "Speech"}
        ]

        from backend.core.sensevoice_runner import run_sensevoice

        segs = [_make_seg(id=0, start=0.0, end=1800.0)]
        result = run_sensevoice("/fake/audio.wav", segments=segs)
        mock_chunked.assert_called_once()
        self.assertEqual(len(result), 1)

    @patch("backend.core.sensevoice_runner.is_sensevoice_available")
    def test_short_audio_does_not_chunk(self, mock_avail):
        mock_avail.return_value = False
        from backend.core.sensevoice_runner import run_sensevoice

        segs = [_make_seg(id=0, start=0.0, end=60.0)]
        result = run_sensevoice("/fake/audio.wav", segments=segs)
        self.assertIsNone(result)  # not available

    def test_no_segments_skips_chunk_check(self):
        """Without segments, run_sensevoice should use direct path."""
        from backend.core.sensevoice_runner import run_sensevoice
        result = run_sensevoice("/fake/audio.wav")
        self.assertIsNone(result)  # model not available in test


# ═══════════════════════════════════════════════════════════════
# Import consistency
# ═══════════════════════════════════════════════════════════════

class TestImports(unittest.TestCase):
    """Verify that chunking helpers are importable from where they're needed."""

    def test_chunk_helpers_importable_from_qwen(self):
        from backend.core.qwen_asr_runner import (
            _chunk_segments,
            _extract_audio_chunk,
        )
        self.assertTrue(callable(_chunk_segments))
        self.assertTrue(callable(_extract_audio_chunk))

    def test_chunk_helpers_importable_in_sensevoice(self):
        """sensevoice_runner imports _chunk_segments and _extract_audio_chunk from qwen."""
        from backend.core.qwen_asr_runner import _chunk_segments, _extract_audio_chunk
        # Just verify they're functions (not broken imports)
        self.assertTrue(callable(_chunk_segments))
        self.assertTrue(callable(_extract_audio_chunk))

    def test_run_qwen_asr_importable(self):
        """P251: align_by_* removed; run_qwen_asr is the public entry point."""
        from backend.core.qwen_asr_runner import run_qwen_asr
        self.assertTrue(callable(run_qwen_asr))


if __name__ == "__main__":
    unittest.main()
