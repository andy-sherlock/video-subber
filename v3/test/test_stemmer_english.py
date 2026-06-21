"""TDD tests for English stemmer suffix improvements.

Run: cd v2 && .venv/bin/python -m pytest test/test_stemmer_english.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class TestEnglishStemmerNewSuffixes(unittest.TestCase):
    """Verify newly added English suffixes produce correct stems."""

    def _stem(self, text):
        from backend.core.hallucination_filter import LightweightStemmer
        return LightweightStemmer.clean_and_stem(text, "en")

    def test_able_suffix(self):
        stems = self._stem("comfortable")
        self.assertIn("comfort", stems)

    def test_ible_suffix(self):
        stems = self._stem("possible")
        self.assertIn("poss", stems)

    def test_ful_suffix(self):
        stems = self._stem("beautiful")
        self.assertIn("beauti", stems)

    def test_ous_suffix(self):
        stems = self._stem("dangerous")
        self.assertIn("danger", stems)

    def test_ive_suffix(self):
        stems = self._stem("active")
        self.assertIn("act", stems)

    def test_ize_suffix(self):
        stems = self._stem("organize")
        self.assertIn("organ", stems)

    def test_ise_suffix(self):
        stems = self._stem("organise")
        self.assertIn("organ", stems)

    def test_ity_suffix(self):
        stems = self._stem("university")
        self.assertIn("univers", stems)

    def test_al_suffix(self):
        stems = self._stem("natural")
        self.assertIn("natur", stems)

    def test_existing_suffixes_still_work(self):
        stems = self._stem("running played singer nicely boxes cats")
        self.assertIn("run", stems)
        self.assertIn("play", stems)
        self.assertIn("sing", stems)
        self.assertIn("nice", stems)
        self.assertIn("box", stems)
        self.assertIn("cat", stems)

    def test_double_consonant_after_ing(self):
        stems = self._stem("swimming")
        self.assertIn("swim", stems)

    def test_cross_validation_same_word_different_form(self):
        """Both ASRs hearing same word with different inflections should share stems."""
        stems_a = self._stem("comfortable running naturally")
        stems_b = self._stem("comfort run nature")
        shared = stems_a & stems_b
        self.assertGreaterEqual(len(shared), 2)

    def test_short_words_unchanged(self):
        stems = self._stem("the and but or")
        self.assertIn("the", stems)
        self.assertIn("and", stems)


if __name__ == "__main__":
    unittest.main()
