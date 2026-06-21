"""Tests for V3.07 HARDEN: P170 + P172 + P178 fixes.

Run: cd v2 && .venv/bin/python -m pytest test/test_harden_v307.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re
import unittest


def _make_seg(id=0, start=0.0, end=1.0, text="hello"):
    class S:
        pass
    s = S()
    s.id = id
    s.start = start
    s.end = end
    s.text = text
    return s


# ═══════════════════════════════════════════════════════════
# P170: _run_qwen_asr_chunked empty list guard
# ═══════════════════════════════════════════════════════════

class TestP170EmptyListGuard(unittest.TestCase):

    def test_function_has_empty_list_check(self):
        """run_qwen_asr must guard against empty segments."""
        import inspect
        from backend.core.qwen_asr_runner import run_qwen_asr
        src = inspect.getsource(run_qwen_asr)
        self.assertIn("if not segments", src)


# ═══════════════════════════════════════════════════════════
# P172: L4 repeated words threshold {2,} → {1,}
# ═══════════════════════════════════════════════════════════

class TestP172RepeatWordThreshold(unittest.TestCase):

    def test_repeat_regex_catches_two_word_repeat(self):
        """The compiled regex in hallucination_filter should catch 2 repeats."""
        from backend.core.hallucination_filter import _RE_REPEATED_WORDS
        self.assertTrue(
            _RE_REPEATED_WORDS.match("谢谢 谢谢"),
            f"_RE_REPEATED_WORDS should match 2-word repeats, got: {_RE_REPEATED_WORDS}"
        )

    def test_repeat_regex_still_catches_three_repeats(self):
        """Still catches >=3 repeats."""
        from backend.core.hallucination_filter import _RE_REPEATED_WORDS
        self.assertTrue(_RE_REPEATED_WORDS.match("谢谢 谢谢 谢谢"))

    def test_repeat_regex_catches_english_two_repeat(self):
        """English 2-word repeat: 'hello hello'."""
        from backend.core.hallucination_filter import _RE_REPEATED_WORDS
        self.assertTrue(_RE_REPEATED_WORDS.match("hello hello"))

    def test_single_word_does_not_match(self):
        """'hello' alone is not a repeat."""
        from backend.core.hallucination_filter import _RE_REPEATED_WORDS
        self.assertIsNone(_RE_REPEATED_WORDS.match("hello"))

    def test_different_words_do_not_match(self):
        """'hello world' is not a repeat."""
        from backend.core.hallucination_filter import _RE_REPEATED_WORDS
        self.assertIsNone(_RE_REPEATED_WORDS.match("hello world"))


# ═══════════════════════════════════════════════════════════
# P178: _CHUNK_SIZE / _OVERLAP_SIZE dead code removal
# ═══════════════════════════════════════════════════════════

class TestP178DeadCodeRemoved(unittest.TestCase):

    def test_chunk_size_not_used_in_translate_function(self):
        """_translate_whole_with_markers uses _compute_chunk_size, not _CHUNK_SIZE."""
        import inspect
        from backend.translation.orchestrator import _translate_whole_with_markers
        src = inspect.getsource(_translate_whole_with_markers)
        # Should NOT reference the dead _CHUNK_SIZE constant directly
        self.assertNotIn("_CHUNK_SIZE", src)

    def test_overlap_size_not_used_in_translate_function(self):
        """_translate_whole_with_markers should not reference _OVERLAP_SIZE."""
        import inspect
        from backend.translation.orchestrator import _translate_whole_with_markers
        src = inspect.getsource(_translate_whole_with_markers)
        self.assertNotIn("_OVERLAP_SIZE", src)


if __name__ == "__main__":
    unittest.main()
