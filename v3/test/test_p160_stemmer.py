"""TDD tests for LightweightStemmer (P160 Phase 2 - Chinese first).

Run: cd v3 && .venv/bin/python -m pytest test/test_p160_stemmer.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


# ── Stems must NOT import from hallucination_filter yet — TDD RED phase ──
# The class doesn't exist. These tests will FAIL with ImportError.


class TestLightweightStemmerChinese(unittest.TestCase):
    """Chinese: no stemming needed, extract individual characters."""

    def test_single_char_words(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("你好世界", "zh")
        self.assertEqual(stems, {"你", "好", "世", "界"})

    def test_chinese_with_punctuation(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("你好，世界！", "zh")
        self.assertEqual(stems, {"你", "好", "世", "界"})

    def test_chinese_with_spaces(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("我 找 你 有 事", "zh")
        self.assertEqual(stems, {"我", "找", "你", "有", "事"})

    def test_chinese_empty_returns_empty(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("", "zh")
        self.assertEqual(stems, set())

    def test_chinese_whitespace_only(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("   ", "zh")
        self.assertEqual(stems, set())

    def test_chinese_dedup_characters(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("你好你好你好", "zh")
        self.assertEqual(stems, {"你", "好"})


class TestLightweightStemmerDefault(unittest.TestCase):
    """Languages without a specific stemmer fall back to split()."""

    def test_unknown_lang_splits_on_space(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("hello world test", "xx")
        self.assertEqual(stems, {"hello", "world", "test"})

    def test_empty_text_returns_empty(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("", "en")
        self.assertEqual(stems, set())

    def test_none_text_returns_empty(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem(None, "en")
        self.assertEqual(stems, set())


class TestLightweightStemmerEnglish(unittest.TestCase):
    """English: lightweight suffix stripping."""

    def test_ing_suffix_removed(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("running walking", "en")
        self.assertIn("run", stems)
        self.assertIn("walk", stems)

    def test_ed_suffix_removed(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("walked jumped", "en")
        self.assertIn("walk", stems)
        self.assertIn("jump", stems)

    def test_plural_s_removed(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("dogs cats", "en")
        self.assertEqual(stems, {"dog", "cat"})

    def test_short_words_untouched(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("a is at", "en")
        self.assertEqual(stems, {"a", "is", "at"})


class TestLightweightStemmerRussian(unittest.TestCase):
    """Russian: manual Snowball-like suffix stripping."""

    def test_money_forms_stem_to_same_root(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("деньги денег деньгах", "ru")
        # Lightweight stemmer — stems may differ by a trailing char
        # but all must share the core root "ден"
        self.assertTrue(all(s.startswith("ден") for s in stems),
                        f"All stems should start with ден, got: {stems}")

    def test_reverse_suffix_sya_removed(self):
        from backend.core.hallucination_filter import LightweightStemmer
        stems = LightweightStemmer.clean_and_stem("заниматься", "ru")
        root = list(stems)[0]
        # With -ся removed, should start with "занима" not "заниматься"
        self.assertFalse(root.endswith("ся"))
        self.assertTrue(root.startswith("занима"))


if __name__ == "__main__":
    unittest.main()
