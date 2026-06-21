"""P287: _assemble_qwen_primary() must backfill whisper_text for all languages.

P284 fixed this for English only (via _assemble_qwen_on_whisper_boundaries).
Non-English languages still go through _assemble_qwen_primary which did not
set whisper_text, causing the refiner to fall back to single-draft mode.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from types import SimpleNamespace


def _make_whisper_seg(start, end, text):
    return SimpleNamespace(
        start=start, end=end, text=text,
        sensevoice_tag=None,
    )


class TestP287WhisperTextBackfill(unittest.TestCase):

    def test_ja_qwen_primary_has_whisper_text(self):
        """Japanese Qwen primary assembly must backfill whisper_text."""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "今日", "start": 0.0, "end": 0.3},
            {"text": "は", "start": 0.3, "end": 0.4},
            {"text": "天気", "start": 0.5, "end": 0.8},
            {"text": "が", "start": 0.8, "end": 0.9},
            {"text": "いい", "start": 1.0, "end": 1.3},
            {"text": "ですね", "start": 1.4, "end": 1.8},
        ]
        whisper_segments = [
            _make_whisper_seg(0.0, 1.8, "今日は天気がいいですね"),
        ]

        result = _assemble_qwen_primary(qwen_words, whisper_segments, "ja")
        self.assertTrue(len(result) > 0)
        has_any_whisper = any(
            getattr(s, "whisper_text", "") for s in result
        )
        self.assertTrue(
            has_any_whisper,
            f"No segment has whisper_text. Segments: {[(s.text, getattr(s, 'whisper_text', '')) for s in result]}"
        )

    def test_zh_qwen_primary_has_whisper_text(self):
        """Chinese Qwen primary assembly must backfill whisper_text."""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "你好", "start": 0.0, "end": 0.4},
            {"text": "世界", "start": 0.5, "end": 0.9},
        ]
        whisper_segments = [
            _make_whisper_seg(0.0, 0.9, "你好世界"),
        ]

        result = _assemble_qwen_primary(qwen_words, whisper_segments, "zh")
        self.assertTrue(len(result) > 0)
        self.assertTrue(
            any(getattr(s, "whisper_text", "") for s in result),
            "Chinese segment missing whisper_text"
        )

    def test_ko_qwen_primary_has_whisper_text(self):
        """Korean Qwen primary assembly must backfill whisper_text."""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "안녕", "start": 0.0, "end": 0.3},
            {"text": "하세요", "start": 0.4, "end": 0.8},
        ]
        whisper_segments = [
            _make_whisper_seg(0.0, 0.8, "안녕하세요"),
        ]

        result = _assemble_qwen_primary(qwen_words, whisper_segments, "ko")
        self.assertTrue(len(result) > 0)
        self.assertTrue(
            any(getattr(s, "whisper_text", "") for s in result),
            "Korean segment missing whisper_text"
        )

    def test_multi_segment_whisper_overlap(self):
        """Multiple Whisper segments map to correct Qwen segments."""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "おはよう", "start": 0.0, "end": 0.5},
            {"text": "ございます", "start": 0.6, "end": 1.2},
            # big pause → new segment
            {"text": "今日", "start": 3.0, "end": 3.3},
            {"text": "は", "start": 3.3, "end": 3.4},
            {"text": "いい", "start": 3.5, "end": 3.8},
            {"text": "天気", "start": 3.9, "end": 4.2},
        ]
        whisper_segments = [
            _make_whisper_seg(0.0, 1.3, "おはようございます"),
            _make_whisper_seg(2.9, 4.3, "今日はいい天気"),
        ]

        result = _assemble_qwen_primary(qwen_words, whisper_segments, "ja")
        self.assertTrue(len(result) >= 2, f"Expected >=2 segments, got {len(result)}")

        for s in result:
            wt = getattr(s, "whisper_text", "")
            self.assertTrue(
                wt,
                f"Segment '{s.text}' (start={s.start}) has no whisper_text"
            )

    def test_no_whisper_segments_no_crash(self):
        """When no Whisper segments available, still works (whisper_text empty)."""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "テスト", "start": 0.0, "end": 0.5},
        ]

        result = _assemble_qwen_primary(qwen_words, [], "ja")
        self.assertEqual(len(result), 1)
        # whisper_text should be empty string, not crash
        wt = getattr(result[0], "whisper_text", "")
        self.assertEqual(wt, "")

    def test_none_whisper_segments_no_crash(self):
        """When whisper_segments is None (standard mode), must not crash."""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "テスト", "start": 0.0, "end": 0.5},
        ]

        result = _assemble_qwen_primary(qwen_words, None, "ja")
        self.assertEqual(len(result), 1)
        wt = getattr(result[0], "whisper_text", "")
        self.assertEqual(wt, "")

    def test_refiner_mode_becomes_dual_with_backfill(self):
        """After backfill, _choose_refiner_mode should return 'dual'."""
        from backend.transcriber_v2 import _assemble_qwen_primary
        from backend.worker import _choose_refiner_mode

        qwen_words = [
            {"text": "今日", "start": 0.0, "end": 0.3},
            {"text": "は", "start": 0.3, "end": 0.4},
            {"text": "天気", "start": 0.5, "end": 0.8},
            {"text": "が", "start": 0.8, "end": 0.9},
            {"text": "いい", "start": 1.0, "end": 1.3},
        ]
        whisper_segments = [
            _make_whisper_seg(0.0, 1.3, "今日は天気がいい"),
        ]

        segments = _assemble_qwen_primary(qwen_words, whisper_segments, "ja")
        mode = _choose_refiner_mode(segments)
        self.assertEqual(
            mode, "dual",
            f"Expected refiner mode 'dual' but got '{mode}'. "
            f"whisper_texts: {[getattr(s, 'whisper_text', '') for s in segments]}"
        )


if __name__ == "__main__":
    unittest.main()
