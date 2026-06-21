"""Standard/Precise mode restructuring tests."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestShouldQwenPipeline:
    def test_standard_with_enough_ram(self):
        from backend.transcriber_v2 import should_qwen_pipeline
        assert should_qwen_pipeline("standard", 16) is True

    def test_standard_with_low_ram(self):
        from backend.transcriber_v2 import should_qwen_pipeline
        assert should_qwen_pipeline("standard", 8) is False

    def test_precise_with_enough_ram(self):
        from backend.transcriber_v2 import should_qwen_pipeline
        assert should_qwen_pipeline("precise", 16) is True

    def test_precise_with_low_ram(self):
        from backend.transcriber_v2 import should_qwen_pipeline
        assert should_qwen_pipeline("precise", 8) is False


class TestShouldLoadWhisper:
    def test_standard_no_whisper(self):
        from backend.transcriber_v2 import should_load_whisper
        assert should_load_whisper("standard") is False

    def test_precise_loads_whisper(self):
        from backend.transcriber_v2 import should_load_whisper
        assert should_load_whisper("precise") is True


class TestWorkerTierValidation:
    def test_fast_becomes_standard(self):
        """Backward compat: 'fast' should be treated as 'standard'."""
        tier = _normalize_tier("fast")
        assert tier == "standard"

    def test_standard_unchanged(self):
        tier = _normalize_tier("standard")
        assert tier == "standard"

    def test_precise_unchanged(self):
        tier = _normalize_tier("precise")
        assert tier == "precise"

    def test_none_defaults_standard(self):
        tier = _normalize_tier(None)
        assert tier == "standard"


def _normalize_tier(tier):
    """Mirror the normalization logic that worker.py should implement."""
    if tier == "fast" or tier is None:
        return "standard"
    return tier
