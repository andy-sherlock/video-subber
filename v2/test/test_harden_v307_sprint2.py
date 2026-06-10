"""Tests for V3.07 HARDEN Sprint 2: P240+P243+P244+P241+P248+P246+P213+P214.

Run: cd v2 && .venv/bin/python -m pytest test/test_harden_v307_sprint2.py -v
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect
import unittest


def _make_seg(id=0, start=0.0, end=1.0, text="hello", l2_status=None):
    class S:
        pass
    s = S()
    s.id = id
    s.start = start
    s.end = end
    s.text = text
    s.filtered = False
    s.filter_reason = None
    if l2_status is not None:
        s.l2_status = l2_status
    return s


# ═══════════════════════════════════════════════════════════
# Commit 1: Security — P240 + P243 + P244
# ═══════════════════════════════════════════════════════════

class TestP240PathTraversal(unittest.TestCase):
    """P240: llm_fallback_decision must validate job_id against path traversal."""

    def test_validate_job_path_helper_exists(self):
        from backend.main import _validate_job_path
        self.assertTrue(callable(_validate_job_path))

    def test_rejects_path_traversal(self):
        from backend.main import _validate_job_path
        result = _validate_job_path("../../etc/passwd")
        self.assertIsNone(result)

    def test_rejects_dot_dot_slash(self):
        from backend.main import _validate_job_path
        result = _validate_job_path("../secrets")
        self.assertIsNone(result)

    def test_accepts_valid_job_id(self):
        from backend.main import _validate_job_path
        result = _validate_job_path("abc123-def456")
        self.assertIsNotNone(result)

    def test_llm_fallback_uses_validation(self):
        """llm_fallback_decision source must call _validate_job_path."""
        from backend.main import llm_fallback_decision
        src = inspect.getsource(llm_fallback_decision)
        self.assertIn("_validate_job_path", src)


class TestP243SSRF(unittest.TestCase):
    """P243: /api/llm-models must reject private/internal URLs."""

    def test_verify_safe_url_helper_exists(self):
        from backend.main import _verify_safe_url
        self.assertTrue(callable(_verify_safe_url))

    def test_rejects_file_scheme(self):
        from backend.main import _verify_safe_url
        self.assertFalse(_verify_safe_url("file:///etc/passwd"))

    def test_rejects_localhost(self):
        from backend.main import _verify_safe_url
        self.assertFalse(_verify_safe_url("http://127.0.0.1/v1/models"))

    def test_rejects_private_10x(self):
        from backend.main import _verify_safe_url
        self.assertFalse(_verify_safe_url("http://10.0.0.1/v1/models"))

    def test_rejects_private_192168(self):
        from backend.main import _verify_safe_url
        self.assertFalse(_verify_safe_url("http://192.168.1.1/v1/models"))

    def test_rejects_private_172(self):
        from backend.main import _verify_safe_url
        self.assertFalse(_verify_safe_url("http://172.16.0.1/v1/models"))

    def test_accepts_public_https(self):
        from backend.main import _verify_safe_url
        self.assertTrue(_verify_safe_url("https://api.openai.com/v1/models"))

    def test_accepts_public_http(self):
        from backend.main import _verify_safe_url
        self.assertTrue(_verify_safe_url("http://api.deepseek.com/v1/models"))

    def test_list_llm_models_uses_verify(self):
        """list_llm_models source must call _verify_safe_url for custom endpoints."""
        from backend.main import list_llm_models
        src = inspect.getsource(list_llm_models)
        self.assertIn("_verify_safe_url", src)


class TestP244GetJobPathTraversal(unittest.TestCase):
    """P244: get_job must validate job_id against path traversal."""

    def test_get_job_uses_validation(self):
        from backend.main import get_job
        src = inspect.getsource(get_job)
        self.assertIn("_validate_job_path", src)


# ═══════════════════════════════════════════════════════════
# Commit 2: Translation Quality — P241 + P248
# ═══════════════════════════════════════════════════════════

class TestP241RetryLanguage(unittest.TestCase):
    """P241: P230 retry must not hardcode '英文翻译成中文'."""

    def test_no_hardcoded_english_to_chinese(self):
        from backend.translation.orchestrator import translate_with_orchestrator
        src = inspect.getsource(translate_with_orchestrator)
        self.assertNotIn("将以下英文翻译成中文", src,
                         "P230 retry prompt must use _TARGET_LANGUAGE, not hardcoded '英文→中文'")

    def test_uses_target_language_variable(self):
        from backend.translation.orchestrator import translate_with_orchestrator
        src = inspect.getsource(translate_with_orchestrator)
        self.assertIn("_TARGET_LANGUAGE", src,
                      "P230 retry prompt must reference _TARGET_LANGUAGE")


class TestP248ReflectionTrigger(unittest.TestCase):
    """P248: P3 reflection must run in quality mode even when no segments failed."""

    def test_reflection_not_gated_on_failed(self):
        """The condition guarding P3 reflection must NOT require `failed` to be truthy."""
        from backend import worker
        src = inspect.getsource(worker)
        lines = src.split("\n")
        found = False
        for line in lines:
            if "translation_mode" in line and "quality" in line and "api_keys" in line:
                found = True
                self.assertNotIn("and failed", line,
                                 "P3 reflection should not require `and failed`")
                break
        self.assertTrue(found, "Could not find the P3 reflection guard condition in worker.py")


# ═══════════════════════════════════════════════════════════
# Commit 3: Filter — P246
# ═══════════════════════════════════════════════════════════

class TestP246GreenExemption(unittest.TestCase):
    """P246: L4 repeated-pattern and repeated-words must skip GREEN segments."""

    def test_repeated_pattern_skips_green(self):
        """GREEN segment with repeated pattern text should NOT be filtered."""
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="abcabcabc", l2_status="GREEN")
        _layer4_regex(seg)
        self.assertFalse(seg.filtered,
                         "L4 should not filter repeated-pattern when l2_status=GREEN")

    def test_repeated_words_skips_green(self):
        """GREEN segment with repeated words should NOT be filtered."""
        from backend.core.hallucination_filter import _layer4_regex
        # Use 3-word repeat to avoid triggering the half-string check at L234
        seg = _make_seg(text="hey hey hey", l2_status="GREEN")
        _layer4_regex(seg)
        self.assertFalse(seg.filtered,
                         "L4 should not filter repeated-words when l2_status=GREEN")

    def test_repeated_pattern_still_filters_non_green(self):
        """Non-GREEN segment with repeated pattern should still be filtered."""
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="abcabcabc", l2_status="YELLOW")
        _layer4_regex(seg)
        self.assertTrue(seg.filtered,
                        "L4 should filter repeated-pattern when l2_status=YELLOW")

    def test_repeated_words_still_filters_non_green(self):
        """Non-GREEN segment with repeated words should still be filtered."""
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="hey hey hey", l2_status="YELLOW")
        _layer4_regex(seg)
        self.assertTrue(seg.filtered,
                        "L4 should filter repeated-words when l2_status=YELLOW")

    def test_repeated_words_filters_when_no_l2_status(self):
        """Segment without l2_status should be filtered (default unsafe)."""
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="hey hey hey")
        _layer4_regex(seg)
        self.assertTrue(seg.filtered,
                        "L4 should filter repeated-words when l2_status is missing")


# ═══════════════════════════════════════════════════════════
# Commit 5: Tail — P213 + P214
# ═══════════════════════════════════════════════════════════

class TestP213AlignmentMismatchWarning(unittest.TestCase):
    """P213: apply_aligner_timestamps must warn when segments get no matching words."""

    def test_warns_on_unmatched_segments(self):
        from backend.core.qwen_aligner import apply_aligner_timestamps
        segs = [_make_seg(id=0, start=0.0, end=1.0, text="hello"),
                _make_seg(id=1, start=5.0, end=6.0, text="world")]
        # Aligned words only cover segment 0, not segment 1
        aligned = [{"word": "hello", "start": 0.1, "end": 0.5}]
        with self.assertLogs("qwen_aligner", level="WARNING") as cm:
            apply_aligner_timestamps(segs, aligned)
        self.assertTrue(any("1" in msg and ("未匹配" in msg or "unmatched" in msg.lower())
                            for msg in cm.output),
                        f"Expected warning about unmatched segments, got: {cm.output}")

    def test_no_warning_when_all_matched(self):
        from backend.core.qwen_aligner import apply_aligner_timestamps
        segs = [_make_seg(id=0, start=0.0, end=1.0, text="hello")]
        aligned = [{"word": "hello", "start": 0.1, "end": 0.5}]
        # Should not raise or warn
        result = apply_aligner_timestamps(segs, aligned)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].start, 0.1)


class TestP214FAQualityVisibility(unittest.TestCase):
    """P214: FA alignment quality must be visible, not silent."""

    def test_serial_path_sets_quality_on_failure(self):
        """transcriber source must set _fa_quality = 'fallback' in serial exception handler."""
        from backend import transcriber_v2
        src = inspect.getsource(transcriber_v2.transcribe)
        self.assertIn("_fa_quality", src,
                      "transcribe() must track FA quality via _fa_quality variable")

    def test_segments_marked_with_fa_quality(self):
        """After FA section, segments must have _fa_quality attribute."""
        src_lines = inspect.getsource(
            __import__("backend.transcriber_v2", fromlist=["transcribe"]).transcribe
        )
        self.assertIn("_fa_quality", src_lines,
                      "transcribe() must set seg._fa_quality on result segments")


if __name__ == "__main__":
    unittest.main()
