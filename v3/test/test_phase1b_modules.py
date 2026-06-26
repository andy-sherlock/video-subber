"""Tests for Phase 1B modules: hardware_detector, sensevoice_runner, qwen_asr_runner.

Run: cd v3 && .venv/bin/python -m unittest discover -s test -p "test_*.py" -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    """Minimal segment for testing cross-validation matching."""
    def __init__(self, id=0, start=0.0, end=1.0, text="hello"):
        self.id = id
        self.start = start
        self.end = end
        self.text = text


class TestHardwareDetector(unittest.TestCase):
    """Tests for hardware_detector.py pure functions."""

    def test_recommend_tier_none_is_fast(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(None), "fast")

    def test_recommend_tier_4gb_is_fast(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(4), "fast")

    def test_recommend_tier_7point9_is_fast(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(7.9), "fast")

    def test_recommend_tier_8gb_is_standard(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(8), "standard")

    def test_recommend_tier_15point9_is_standard(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(15.9), "standard")

    def test_recommend_tier_16gb_is_precise(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(16), "precise")

    def test_recommend_tier_32gb_is_precise(self):
        from backend.core.hardware_detector import recommend_tier
        self.assertEqual(recommend_tier(32), "precise")

    def test_downgrade_precise_to_standard(self):
        from backend.core.hardware_detector import downgrade_tier
        self.assertEqual(downgrade_tier("precise", "test"), "standard")

    def test_downgrade_standard_to_fast(self):
        from backend.core.hardware_detector import downgrade_tier
        self.assertEqual(downgrade_tier("standard", "test"), "fast")

    def test_downgrade_fast_stays_fast(self):
        from backend.core.hardware_detector import downgrade_tier
        self.assertEqual(downgrade_tier("fast", "test"), "fast")

    def test_downgrade_unknown_tier_falls_back_to_fast(self):
        from backend.core.hardware_detector import downgrade_tier
        self.assertEqual(downgrade_tier("ultra_maximum", "test"), "fast")

    def test_is_apple_silicon_is_bool(self):
        from backend.core.hardware_detector import is_apple_silicon
        self.assertIsInstance(is_apple_silicon(), bool)

    def test_detect_hardware_has_expected_keys(self):
        from backend.core.hardware_detector import detect_hardware
        hw = detect_hardware()
        for key in ("platform", "has_gpu", "gpu_type", "ram_gb", "recommended_tier"):
            self.assertIn(key, hw)

    def test_recommended_tier_matches_detect(self):
        from backend.core.hardware_detector import detect_hardware
        hw = detect_hardware()
        self.assertIn(hw["recommended_tier"], ("fast", "standard", "precise"))


class TestSenseVoiceRunner(unittest.TestCase):
    """Tests for sensevoice_runner.py pure functions."""

    def test_extract_tags_speech(self):
        from backend.core.sensevoice_runner import extract_tags
        result = extract_tags("<|zh|><|NEUTRAL|><|Speech|>你好")
        self.assertEqual(result["language"], "zh")
        self.assertEqual(result["emotion"], "NEUTRAL")
        self.assertEqual(result["event"], "Speech")
        self.assertEqual(result["clean_text"], "你好")

    def test_extract_tags_bgm(self):
        from backend.core.sensevoice_runner import extract_tags
        result = extract_tags("<|en|><|HAPPY|><|BGM|>music")
        self.assertEqual(result["event"], "BGM")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["clean_text"], "music")

    def test_extract_tags_laughter(self):
        from backend.core.sensevoice_runner import extract_tags
        result = extract_tags("<|en|><|HAPPY|><|Laughter|>hahaha")
        self.assertEqual(result["event"], "Laughter")

    def test_extract_tags_missing_event_defaults_to_speech(self):
        from backend.core.sensevoice_runner import extract_tags
        result = extract_tags("<|zh|><|NEUTRAL|>")
        self.assertEqual(result["event"], "Speech")
        self.assertEqual(result["language"], "zh")

    def test_extract_tags_empty_string(self):
        from backend.core.sensevoice_runner import extract_tags
        result = extract_tags("")
        self.assertEqual(result["language"], "unknown")
        self.assertEqual(result["emotion"], "NEUTRAL")
        self.assertEqual(result["event"], "Speech")
        self.assertEqual(result["clean_text"], "")

    def test_extract_tags_only_clean_text(self):
        from backend.core.sensevoice_runner import extract_tags
        result = extract_tags("纯文本无标签")
        self.assertEqual(result["language"], "unknown")
        self.assertEqual(result["event"], "Speech")
        self.assertEqual(result["clean_text"], "纯文本无标签")

    def test_is_sensevoice_available(self):
        from backend.core.sensevoice_runner import is_sensevoice_available
        self.assertIsInstance(is_sensevoice_available(), bool)


class TestQwenASRRunner(unittest.TestCase):
    """Tests for qwen_asr_runner.py pure functions."""

    # P251: _split_sentences / _match_qwen_to_whisper removed (Qwen-ASR no
    # longer maps onto Whisper segments). L2 window assignment is covered by
    # test_p251_qwen_window_assign.py.

    def test_is_qwen_asr_available(self):
        from backend.core.qwen_asr_runner import is_qwen_asr_available
        self.assertIsInstance(is_qwen_asr_available(), bool)


if __name__ == "__main__":
    unittest.main()
