"""TDD tests for P165: Content gate smoke test after P1.

Run: cd v3 && .venv/bin/python -m pytest test/test_p165_content_gate.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class TestIsValidTranslation(unittest.TestCase):
    """Test the CJK ratio check used by P165 content gate."""

    def _check(self, text):
        from backend.translation.orchestrator import _is_valid_translation
        return _is_valid_translation(text)

    def test_chinese_text_passes(self):
        self.assertTrue(self._check("这是一段中文翻译"))

    def test_mostly_chinese_passes(self):
        self.assertTrue(self._check("这是中文 with some English"))

    def test_pure_russian_fails(self):
        self.assertFalse(self._check("Это русский текст без китайского"))

    def test_pure_english_fails(self):
        self.assertFalse(self._check("This is pure English text"))

    def test_empty_fails(self):
        self.assertFalse(self._check(""))

    def test_whitespace_fails(self):
        self.assertFalse(self._check("   "))

    def test_none_fails(self):
        self.assertFalse(self._check(None))

    def test_borderline_10_percent(self):
        text = "一" + "x" * 9
        self.assertTrue(self._check(text))

    def test_below_10_percent(self):
        text = "一" + "x" * 20
        self.assertFalse(self._check(text))


if __name__ == "__main__":
    unittest.main()
