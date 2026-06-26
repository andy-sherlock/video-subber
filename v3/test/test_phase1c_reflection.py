"""TDD tests for #7: Translation reflection optimization.

Run: cd v3 && .venv/bin/python -m unittest test.test_phase1c_reflection -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestReflectionPrompt(unittest.TestCase):
    """Tests for building the reflection review prompt."""

    def test_builds_prompt_with_texts_and_translations(self):
        from backend.translation.reflection import build_reflection_prompt
        texts = ["Hello world", "Good morning"]
        translated = ["你好世界", "早上好"]
        prompt = build_reflection_prompt(texts, translated, limit=50)
        self.assertIn("Hello world", prompt)
        self.assertIn("你好世界", prompt)
        self.assertIn("Good morning", prompt)
        self.assertIn("早上好", prompt)

    def test_respects_limit(self):
        from backend.translation.reflection import build_reflection_prompt
        texts = [f"text{i}" for i in range(100)]
        translated = [f"译文{i}" for i in range(100)]
        prompt = build_reflection_prompt(texts, translated, limit=5)
        self.assertEqual(prompt.count("[0]"), 1)
        self.assertNotIn("[5]", prompt)


class TestReflectionParsing(unittest.TestCase):
    """Tests for parsing the reflection result."""

    def test_parses_corrections(self):
        from backend.translation.reflection import parse_reflection_result
        raw = "[3] 你好世界\n[7] 谢谢你的帮助"
        result = parse_reflection_result(raw)
        self.assertEqual(result, {3: "你好世界", 7: "谢谢你的帮助"})

    def test_empty_raw_returns_empty(self):
        from backend.translation.reflection import parse_reflection_result
        self.assertEqual(parse_reflection_result(""), {})
        self.assertEqual(parse_reflection_result("  \n  "), {})

    def test_skips_invalid_lines(self):
        from backend.translation.reflection import parse_reflection_result
        raw = "Some comment\n[5] 修正\nNot a correction\n[abc] 无效"
        result = parse_reflection_result(raw)
        self.assertEqual(result, {5: "修正"})

    def test_negative_index_skipped(self):
        from backend.translation.reflection import parse_reflection_result
        raw = "[-1] bad"
        result = parse_reflection_result(raw)
        self.assertEqual(result, {})


class TestApplyReflection(unittest.TestCase):
    """Tests for applying reflection corrections to translated list."""

    def test_applies_corrections(self):
        from backend.translation.reflection import apply_reflection
        translated = ["a", "b", "c", "d"]
        corrections = {1: "BBB", 3: "DDD"}
        result = apply_reflection(translated, corrections)
        self.assertEqual(result, ["a", "BBB", "c", "DDD"])

    def test_empty_corrections_does_nothing(self):
        from backend.translation.reflection import apply_reflection
        translated = ["a", "b"]
        result = apply_reflection(translated, {})
        self.assertEqual(result, ["a", "b"])

    def test_oup_correction_ignored(self):
        from backend.translation.reflection import apply_reflection
        translated = ["a", "b"]
        result = apply_reflection(translated, {5: "F"})
        self.assertEqual(result, ["a", "b"])

    def test_none_corrections_returns_original(self):
        from backend.translation.reflection import apply_reflection
        translated = ["a", "b"]
        result = apply_reflection(translated, None)
        self.assertEqual(result, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
