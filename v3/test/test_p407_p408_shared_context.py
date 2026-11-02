"""Tests for P407 (worker context cache) + P408 (shared fatigue chunk formula).

Run: cd v3 && .venv/bin/python -m pytest test/test_p407_p408_shared_context.py -v
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


# ═══════════════════════════════════════════════════════════
# B1/B2: compute_fatigue_chunk — shared formula
# ═══════════════════════════════════════════════════════════

class TestComputeFatigueChunk(unittest.TestCase):
    """Test the shared formula: (ctx × 0.30 − overhead) ÷ per_unit, clamp [floor, ceiling]."""

    def setUp(self):
        from backend.model_cache import compute_fatigue_chunk
        self._compute = compute_fatigue_chunk

    # ── B5: Translation side values MUST NOT change ──

    def test_translation_32k_returns_978(self):
        """(32768*0.30 - 2000)//8 = 978. Must match existing test_arch9."""
        result = self._compute(32768, overhead=2000, per_unit=8, floor=200, ceiling=1200)
        self.assertEqual(result, 978)

    def test_translation_64k_capped_1200(self):
        """(65536*0.30 - 2000)//8 = 2207 → cap 1200."""
        result = self._compute(65536, overhead=2000, per_unit=8, floor=200, ceiling=1200)
        self.assertEqual(result, 1200)

    def test_translation_128k_capped_1200(self):
        result = self._compute(128000, overhead=2000, per_unit=8, floor=200, ceiling=1200)
        self.assertEqual(result, 1200)

    def test_translation_1m_capped_1200(self):
        result = self._compute(1000000, overhead=2000, per_unit=8, floor=200, ceiling=1200)
        self.assertEqual(result, 1200)

    def test_translation_8k_floor_200(self):
        """(8192*0.30 - 2000)//8 = 57 → floor 200."""
        result = self._compute(8192, overhead=2000, per_unit=8, floor=200, ceiling=1200)
        self.assertEqual(result, 200)

    def test_translation_20k_returns_500(self):
        """(20000*0.30 - 2000)//8 = 500."""
        result = self._compute(20000, overhead=2000, per_unit=8, floor=200, ceiling=1200)
        self.assertEqual(result, 500)

    # ── B3/B4: Sentence side — now has overhead and ceiling ──

    def test_sentence_32k_with_overhead_1000(self):
        """(32768*0.30 - 1000)//8 = (9830-1000)//8 = 8830//8 = 1103."""
        result = self._compute(32768, overhead=1000, per_unit=8, floor=1, ceiling=2000)
        self.assertEqual(result, 1103)

    def test_sentence_1m_capped_at_ceiling(self):
        """1M context → (300000-1000)//8 = 37375 → capped at ceiling."""
        result = self._compute(1000000, overhead=1000, per_unit=8, floor=1, ceiling=2000)
        self.assertEqual(result, 2000)

    def test_sentence_tiny_ctx_uses_floor(self):
        """Very small context → formula goes negative → floor kicks in."""
        result = self._compute(2000, overhead=1000, per_unit=8, floor=1, ceiling=2000)
        # int(2000*0.30) = 600, 600-1000 = -400, -400//8 = -50 → floor 1
        self.assertEqual(result, 1)

    # ── B6: per_unit parameterization ──

    def test_different_per_unit(self):
        """Same ctx/overhead but per_unit=4 should double the result vs per_unit=8."""
        r8 = self._compute(32768, overhead=2000, per_unit=8, floor=1, ceiling=99999)
        r4 = self._compute(32768, overhead=2000, per_unit=4, floor=1, ceiling=99999)
        # r8 = (9830-2000)//8 = 978; r4 = (9830-2000)//4 = 1957
        self.assertEqual(r8, 978)
        self.assertEqual(r4, 1957)

    # ── B2: FATIGUE_RATIO is the single source ──

    def test_fatigue_ratio_is_030(self):
        from backend.model_cache import FATIGUE_RATIO
        self.assertEqual(FATIGUE_RATIO, 0.30)


# ═══════════════════════════════════════════════════════════
# A1/A2/A3: save and load context cache
# ═══════════════════════════════════════════════════════════

class TestContextCachePersistence(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._cache_path = Path(self._tmpdir) / "context_cache.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _save(self, data):
        from backend.model_cache import save_context_cache
        save_context_cache(data, path=self._cache_path)

    def _load(self):
        from backend.model_cache import load_context_cache
        return load_context_cache(path=self._cache_path)

    def test_round_trip(self):
        """Write dict to file, read back, must match."""
        data = {"deepseek-v4-flash": 1000000, "gpt-4o": 128000}
        self._save(data)
        loaded = self._load()
        self.assertEqual(loaded, data)

    def test_file_not_exists_returns_empty(self):
        """No file → empty dict, no crash."""
        loaded = self._load()
        self.assertEqual(loaded, {})

    def test_file_is_valid_json(self):
        """Saved file must be parseable JSON."""
        self._save({"test": 42})
        raw = self._cache_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        self.assertEqual(parsed["test"], 42)

    def test_corrupt_file_returns_empty(self):
        """Corrupted file → empty dict, no crash."""
        self._cache_path.write_text("{invalid json", encoding="utf-8")
        loaded = self._load()
        self.assertEqual(loaded, {})

    def test_atomic_write_uses_replace(self):
        """save must use os.replace for atomicity."""
        with patch("os.replace", wraps=os.replace) as mock_replace:
            self._save({"a": 1})
            mock_replace.assert_called_once()

    def test_creates_parent_directory(self):
        """save must create parent dirs if missing."""
        nested = Path(self._tmpdir) / "sub" / "dir" / "cache.json"
        from backend.model_cache import save_context_cache
        save_context_cache({"x": 1}, path=nested)
        self.assertTrue(nested.exists())


# ═══════════════════════════════════════════════════════════
# A4/A5: fallback behavior
# ═══════════════════════════════════════════════════════════

class TestGetModelContext(unittest.TestCase):

    def setUp(self):
        from backend.model_cache import _MODEL_CONTEXT_CACHE
        self._cache = _MODEL_CONTEXT_CACHE
        self._original = dict(_MODEL_CONTEXT_CACHE)

    def tearDown(self):
        self._cache.clear()
        self._cache.update(self._original)

    def test_memory_dict_hit(self):
        """When model is in memory dict, return that value."""
        from backend.model_cache import get_model_context
        self._cache["test-model"] = 65536
        self.assertEqual(get_model_context("test-model"), 65536)

    def test_empty_dict_no_file_returns_32768(self):
        """No memory, no file → default 32768."""
        from backend.model_cache import get_model_context
        self._cache.clear()
        with patch("backend.model_cache._CONTEXT_CACHE_PATH",
                   Path("/nonexistent/path/cache.json")):
            result = get_model_context("unknown-model")
            self.assertEqual(result, 32768)

    def test_fallback_table_for_known_model(self):
        """Model in local fallback table → return that value."""
        from backend.model_cache import get_model_context, _LOCAL_CONTEXT_FALLBACK
        self._cache.clear()
        if _LOCAL_CONTEXT_FALLBACK:
            known_model = next(iter(_LOCAL_CONTEXT_FALLBACK))
            expected = _LOCAL_CONTEXT_FALLBACK[known_model]
            with patch("backend.model_cache._CONTEXT_CACHE_PATH",
                       Path("/nonexistent/path/cache.json")):
                result = get_model_context(known_model)
                self.assertEqual(result, expected)


# ═══════════════════════════════════════════════════════════
# C1: cache version bump
# ═══════════════════════════════════════════════════════════

class TestCacheVersionBump(unittest.TestCase):

    def test_boundary_cache_version_never_regresses_below_p407(self):
        """P407/P408: chunk_words changed → version had to reach 5.

        原断言写死 `== 5`。P409 改提示词后必须升到 6，这条就红了——但那是**合法
        升版**，不是回归。写死字面量会让每一次合法升版都误报一次红灯，训练人
        「看到红灯就改数字」，久了就没人想为什么。

        这里要守的不变量是「P407/P408 那次的作废没有被人退回去」，所以断言
        「不低于 5」。「改了提示词是否记得升版本」由
        `test_p390_semantic_gate.py::test_cache_version_bumped_on_prompt_change`
        从行为上验证（旧版本存档必须被拒绝重算），比钉字面量可靠。
        """
        from backend.worker import _P390_BOUNDARY_CACHE_VERSION
        self.assertGreaterEqual(_P390_BOUNDARY_CACHE_VERSION, 5)


# ═══════════════════════════════════════════════════════════
# C2: _p395_chunk_words now delegates to shared function
# ═══════════════════════════════════════════════════════════

class TestP395ChunkWordsRefactored(unittest.TestCase):
    """_p395_chunk_words must now produce different results than before
    (it has overhead and ceiling), and must NOT have its own copy of the formula."""

    def test_32k_with_overhead(self):
        """With overhead=1000, 32K context should NOT return 1228 (old value).
        New: (32768*0.30 - 1000) // 8 = 1103, then clamp."""
        from backend.worker import _p395_chunk_words
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.max_input_tokens = 32768
        result = _p395_chunk_words(llm)
        self.assertNotEqual(result, 1228, "Old formula without overhead still in use")
        self.assertGreater(result, 0)

    def test_1m_context_is_capped(self):
        """1M context must NOT return 37500 (old uncapped value)."""
        from backend.worker import _p395_chunk_words
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.max_input_tokens = 1000000
        result = _p395_chunk_words(llm)
        self.assertLess(result, 10000, "Sentence chunk_words not capped for large context")

    def test_no_duplicate_fatigue_ratio_in_worker(self):
        """_P395_CONTEXT_FATIGUE_RATIO must be DELETED from worker.py —
        30% lives only in model_cache.py now."""
        import backend.worker as w
        self.assertFalse(
            hasattr(w, '_P395_CONTEXT_FATIGUE_RATIO'),
            "Duplicate FATIGUE_RATIO still exists in worker.py"
        )

    def test_no_duplicate_tokens_per_word_in_worker(self):
        """_P395_TOKENS_PER_WORD_LINE must be DELETED from worker.py."""
        import backend.worker as w
        self.assertFalse(
            hasattr(w, '_P395_TOKENS_PER_WORD_LINE'),
            "Duplicate TOKENS_PER_WORD constant still exists in worker.py"
        )

    def test_no_duplicate_default_max_input_in_worker(self):
        """_P395_DEFAULT_MAX_INPUT_TOKENS must be DELETED from worker.py."""
        import backend.worker as w
        self.assertFalse(
            hasattr(w, '_P395_DEFAULT_MAX_INPUT_TOKENS'),
            "Duplicate DEFAULT_MAX_INPUT constant still exists in worker.py"
        )


if __name__ == "__main__":
    unittest.main()
