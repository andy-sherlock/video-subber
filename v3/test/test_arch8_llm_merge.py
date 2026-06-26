"""Tests for ARCH ⑧: LLM merge — combine terminology + context summary into one call.

Run: cd v3 && .venv/bin/python -m unittest test.test_arch8_llm_merge -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from unittest.mock import MagicMock

from backend.translation.base import TranslationResult


def _make_llm(name="test-llm", raw_response=""):
    """Create a mock LLM backend whose translate_raw returns the given text."""
    llm = MagicMock()
    llm.name = name
    llm.translate_raw.return_value = TranslationResult(
        translated=[raw_response],
        backend_name=name,
        tokens_used={"input_tokens": 100, "output_tokens": 50},
        duration_ms=500,
    )
    return llm


# ═══════════════════════════════════════════════════════════
# Parsing tests: _extract_terminology_and_summary
# ═══════════════════════════════════════════════════════════

class TestExtractTerminologyAndSummary(unittest.TestCase):
    """Test the new merged extraction function."""

    def setUp(self):
        from backend.translation.orchestrator import _extract_terminology_and_summary
        self._func = _extract_terminology_and_summary

    # ── Happy path ──

    def test_both_sections_parsed_correctly(self):
        """Happy path: LLM returns both sections with valid content."""
        response = (
            "## 术语表\n"
            "OpenAI → 开放人工智能\n"
            "transformer → 变换器\n"
            "## 内容摘要\n"
            "本视频介绍了深度学习模型的基本架构，主讲人语气专业正式。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["deep learning", "transformer", "OpenAI"], llm)

        self.assertIsInstance(terms, dict)
        self.assertIsInstance(summary, str)
        self.assertEqual(len(terms), 2)
        self.assertEqual(terms["OpenAI"], "开放人工智能")
        self.assertEqual(terms["transformer"], "变换器")
        self.assertIn("深度学习", summary)
        self.assertIn("专业正式", summary)

    def test_both_sections_with_different_delimiters(self):
        """Terms using various delimiter formats: →, ->, >."""
        response = (
            "## 术语表\n"
            "GPT → 生成式预训练变换器\n"
            "API -> 应用程序接口\n"
            "NLP > 自然语言处理\n"
            "## 内容摘要\n"
            "技术教程，介绍NLP相关概念。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["GPT", "API", "NLP"], llm)

        self.assertEqual(len(terms), 3)
        self.assertEqual(terms["GPT"], "生成式预训练变换器")
        self.assertEqual(terms["API"], "应用程序接口")
        self.assertEqual(terms["NLP"], "自然语言处理")
        self.assertIn("技术教程", summary)

    def test_terms_section_with_extra_whitespace(self):
        """Term lines with extra whitespace around delimiters."""
        response = (
            "## 术语表\n"
            "  AlphaGo   →    阿尔法围棋  \n"
            "  PyTorch ->  PyTorch框架  \n"
            "## 内容摘要\n"
            "AI技术讨论。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["AlphaGo", "PyTorch"], llm)

        self.assertEqual(len(terms), 2)
        self.assertEqual(terms["AlphaGo"], "阿尔法围棋")

    def test_terms_with_chinese_char_delimiter(self):
        """Term lines using Chinese arrow characters."""
        response = (
            "## 术语表\n"
            "自然语言处理 → 自然语言处理技术\n"
            "机器学习 → 机器学习方法\n"
            "## 内容摘要\n"
            "人工智能入门教程。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["自然语言处理", "机器学习"], llm)

        self.assertEqual(len(terms), 2)
        self.assertEqual(terms["自然语言处理"], "自然语言处理技术")
        self.assertEqual(terms["机器学习"], "机器学习方法")

    # ── Partial responses: only terminology ──

    def test_only_terms_section_present(self):
        """When LLM only returns terminology (missing summary)."""
        response = (
            "## 术语表\n"
            "Docker → 容器化平台\n"
            "K8s → Kubernetes\n"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["Docker", "K8s"], llm)

        self.assertEqual(len(terms), 2)
        self.assertEqual(summary, "")

    # ── Partial responses: only summary ──

    def test_only_summary_section_present(self):
        """When LLM only returns summary (missing terminology)."""
        response = (
            "## 内容摘要\n"
            "这是一个关于容器技术的教程视频。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["Docker", "K8s", "container"], llm)

        self.assertEqual(terms, {})
        self.assertIn("容器技术", summary)

    # ── Empty input ──

    def test_empty_input_returns_empty(self):
        """Empty texts list should return ({}, "")."""
        llm = _make_llm(raw_response="anything")
        terms, summary = self._func([], llm)
        self.assertEqual(terms, {})
        self.assertEqual(summary, "")

    def test_all_whitespace_input_returns_empty(self):
        """Texts with only whitespace should return ({}, "")."""
        llm = _make_llm(raw_response="anything")
        terms, summary = self._func(["   ", "\n", "\t"], llm)
        self.assertEqual(terms, {})
        self.assertEqual(summary, "")

    # ── LLM exception ──

    def test_llm_exception_returns_empty(self):
        """When translate_raw raises, return ({}, "")."""
        llm = MagicMock()
        llm.name = "broken-llm"
        llm.translate_raw.side_effect = RuntimeError("API down")

        terms, summary = self._func(["hello world"], llm)
        self.assertEqual(terms, {})
        self.assertEqual(summary, "")

    # ── Garbage output ──

    def test_garbage_output_no_headings_returns_empty(self):
        """Response without markdown headings returns empty results."""
        llm = _make_llm(raw_response="some random text without any headings at all")
        terms, summary = self._func(["hello", "world"], llm)
        self.assertEqual(terms, {})
        self.assertEqual(summary, "")

    def test_unrecognized_heading_returned_as_summary(self):
        """Response with only non-matching heading: treat content after heading as summary."""
        response = (
            "## 其他内容\n"
            "这是一些不属于术语表也不属于摘要的文本。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["hello"], llm)
        # No recognized heading → both empty
        self.assertEqual(terms, {})
        self.assertEqual(summary, "")

    # ── Edge cases ──

    def test_empty_terms_table_with_summary(self):
        """LLM says 无 (no terms) but still provides summary."""
        response = (
            "## 术语表\n"
            "无\n"
            "## 内容摘要\n"
            "日常对话，两个朋友在聊天。"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["你好", "最近怎么样"], llm)

        self.assertEqual(terms, {})
        self.assertIn("日常对话", summary)

    def test_summary_before_terms(self):
        """LLM puts summary before terms (reversed order)."""
        response = (
            "## 内容摘要\n"
            "技术分享视频。\n"
            "## 术语表\n"
            "CPU → 中央处理器\n"
        )
        llm = _make_llm(raw_response=response)
        terms, summary = self._func(["CPU", "RAM"], llm)

        self.assertEqual(len(terms), 1)
        self.assertEqual(terms["CPU"], "中央处理器")
        self.assertIn("技术分享", summary)

    def test_llm_returns_no_translated_text(self):
        """translate_raw returns empty translated list."""
        llm = MagicMock()
        llm.name = "empty-llm"
        llm.translate_raw.return_value = TranslationResult(
            translated=[],
            backend_name="empty-llm",
        )
        terms, summary = self._func(["hello"], llm)
        self.assertEqual(terms, {})
        self.assertEqual(summary, "")


# ═══════════════════════════════════════════════════════════
# Integration: translate_with_orchestrator call site
# ═══════════════════════════════════════════════════════════

class TestOrchestratorMergeIntegration(unittest.TestCase):
    """Verify translate_with_orchestrator uses the merge path."""

    def test_merged_function_exists_and_callable(self):
        """_extract_terminology_and_summary is importable and callable."""
        from backend.translation.orchestrator import _extract_terminology_and_summary
        self.assertTrue(callable(_extract_terminology_and_summary))

    def test_old_functions_still_exist_as_fallbacks(self):
        """extract_terminology and _generate_context_summary are still available."""
        from backend.translation.orchestrator import (
            extract_terminology,
            _generate_context_summary,
        )
        self.assertTrue(callable(extract_terminology))
        self.assertTrue(callable(_generate_context_summary))


if __name__ == "__main__":
    unittest.main()
