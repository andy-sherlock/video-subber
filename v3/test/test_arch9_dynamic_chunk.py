"""Tests for ARCH ⑨: Dynamic chunk size — read context window from LiteLLM cache.

Run: cd v3 && .venv/bin/python -m pytest test/test_arch9_dynamic_chunk.py -v
"""
import sys
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from unittest.mock import MagicMock, patch


def _make_llm(model="test-model"):
    """Create a minimal mock LLM with a _model attr + max_input_tokens property."""
    llm = MagicMock()
    llm._model = model
    return llm


# ═══════════════════════════════════════════════════════════
# max_input_tokens property
# ═══════════════════════════════════════════════════════════

class TestMaxInputTokens(unittest.TestCase):

    def setUp(self):
        from backend.model_cache import _MODEL_CONTEXT_CACHE, get_model_context
        self.cache = _MODEL_CONTEXT_CACHE
        self.get_context = get_model_context
        self._file_patcher = patch(
            'backend.model_cache.load_context_cache', return_value={},
        )
        self._file_patcher.start()

    def tearDown(self):
        self.cache.clear()
        self._file_patcher.stop()

    def test_reads_from_context_cache(self):
        """When model name suffix-matches a cache entry, return its value."""
        self.cache["deepseek/deepseek-chat"] = 131072
        self.assertEqual(self.get_context("deepseek-chat"), 131072)

    def test_suffix_match_across_providers(self):
        """gpt-4o should match openai/gpt-4o in cache."""
        self.cache["openai/gpt-4o"] = 128000
        self.assertEqual(self.get_context("gpt-4o"), 128000)

    def test_exact_match_preferred(self):
        """Exact key match takes priority over suffix match."""
        self.cache["gemini-2.0-flash"] = 1048576
        self.cache["google/gemini-2.0-flash"] = 999999
        self.assertEqual(self.get_context("gemini-2.0-flash"), 1048576)

    def test_no_match_defaults_32k(self):
        """Model not in cache returns default 32768."""
        self.assertEqual(self.get_context("totally-unknown-model-xyz"), 32768)

    def test_cache_has_zero_value_falls_back(self):
        """0 in cache is treated as unknown — fall back to default."""
        self.cache["some/model"] = 0
        self.assertEqual(self.get_context("model"), 32768)

    def test_empty_cache_hits_local_fallback(self):
        """Empty memory + empty file → local fallback table (P407)."""
        self.assertEqual(self.get_context("gpt-4o"), 128000)

    def test_empty_cache_unknown_model_defaults_32k(self):
        """Model not in any tier → ultimate default 32768."""
        self.assertEqual(self.get_context("nonexistent-model-xyz"), 32768)

    def test_empty_model_name_defaults_32k(self):
        """Empty model name."""
        self.cache["openai/gpt-4o"] = 128000
        self.assertEqual(self.get_context(""), 32768)


# ═══════════════════════════════════════════════════════════
# _compute_chunk_size
# ═══════════════════════════════════════════════════════════

# P500 施工 ⑤：翻译分块大小 _compute_chunk_size 及其接线测试 随整片翻译代码删除（方案 5.2）。



if __name__ == "__main__":
    unittest.main()
