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

class TestComputeChunkSize(unittest.TestCase):

    def setUp(self):
        from backend.translation.orchestrator import _compute_chunk_size
        self._compute = _compute_chunk_size

    # P253: fatigue-aware formula (ctx * 0.30 - 2000) // 8, clamp [200, 1200]

    def test_32k_context_returns_978(self):
        """(32768*0.30 - 2000)//8 = 978"""
        llm = _make_llm()
        llm.max_input_tokens = 32768
        self.assertEqual(self._compute(llm), 978)

    def test_64k_context_capped_at_1200(self):
        """(65536*0.30 - 2000)//8 = 2207 → cap 1200"""
        llm = _make_llm()
        llm.max_input_tokens = 65536
        self.assertEqual(self._compute(llm), 1200)

    def test_128k_context_capped_at_1200(self):
        """128K → cap 1200"""
        llm = _make_llm()
        llm.max_input_tokens = 128000
        self.assertEqual(self._compute(llm), 1200)

    def test_200k_context_capped_at_1200(self):
        """200K → cap 1200"""
        llm = _make_llm()
        llm.max_input_tokens = 200000
        self.assertEqual(self._compute(llm), 1200)

    def test_8k_context_clamped_to_200_floor(self):
        """(8192*0.30 - 2000)//8 = 57 → clamp 200"""
        llm = _make_llm()
        llm.max_input_tokens = 8192
        self.assertEqual(self._compute(llm), 200)

    def test_20k_context_returns_500(self):
        """(20000*0.30 - 2000)//8 = 500"""
        llm = _make_llm()
        llm.max_input_tokens = 20000
        self.assertEqual(self._compute(llm), 500)


# ═══════════════════════════════════════════════════════════
# Integration: _translate_whole_with_markers uses _compute_chunk_size
# ═══════════════════════════════════════════════════════════

class TestChunkSizeIntegration(unittest.TestCase):

    def test_translate_function_uses_compute_chunk_size(self):
        """_translate_whole_with_markers uses _compute_chunk_size for chunking."""
        from backend.translation.orchestrator import _translate_whole_with_markers
        from backend.translation.base import TranslationResult

        class _RecordingLLM:
            name = "recording"
            max_input_tokens = 32768

            def __init__(self):
                self.prompts = []

            def translate_raw(self, prompt, max_tokens=16384):
                self.prompts.append(prompt)
                lines = []
                for idx in re.findall(r"\[(\d+)\]", prompt.split("【待翻译字幕】")[-1]):
                    lines.append(f"[{idx}] 译文{idx}")
                return TranslationResult(
                    translated=["\n".join(lines)],
                    backend_name=self.name,
                    tokens_used={"calls": 1},
                )

        llm = _RecordingLLM()
        with patch("backend.translation.orchestrator._compute_chunk_size", return_value=3):
            translated, token_usage, sub_splits = _translate_whole_with_markers(
                ["a", "b", "c", "d", "e", "f", "g"],
                llm,
            )

        self.assertEqual(len(llm.prompts), 3)
        self.assertEqual(translated, ["译文0", "译文1", "译文2", "译文3", "译文4", "译文5", "译文6"])
        self.assertEqual(token_usage, {"calls": 3})
        self.assertEqual(sub_splits, {})


if __name__ == "__main__":
    unittest.main()
