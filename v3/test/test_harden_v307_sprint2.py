"""Tests for V3.07 HARDEN Sprint 2: P240+P243+P244+P241+P248+P246+P213+P214.

Run: cd v3 && .venv/bin/python -m pytest test/test_harden_v307_sprint2.py -v
"""
import json
import sys
import logging
import tempfile
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


class _FakeWhisperSeg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


# ═══════════════════════════════════════════════════════════
# Commit 1: Security — P240 + P243 + P244
# ═══════════════════════════════════════════════════════════

class TestP240PathTraversal(unittest.TestCase):
    """P240: shared job_id validation must reject path traversal."""

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

    def test_rejects_sibling_with_same_prefix(self):
        """A sibling path like jobs_evil must not pass a string-prefix check."""
        from backend.main import _validate_job_path, JOBS_DIR
        result = _validate_job_path(f"../{JOBS_DIR.name}_evil")
        self.assertIsNone(result)

    def test_accepts_valid_job_id(self):
        from backend.main import _validate_job_path
        result = _validate_job_path("abc123-def456")
        self.assertIsNotNone(result)


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

    def test_get_job_prunes_stale_done_job_when_meta_missing(self):
        import backend.main as main

        old_jobs_dir = main.JOBS_DIR
        old_jobs = dict(main.JOBS)
        old_global = dict(main.GLOBAL_HASH_INDEX)
        old_reverse = dict(main.JOB_ID_TO_HASH)
        try:
            with tempfile.TemporaryDirectory() as td:
                main.JOBS_DIR = Path(td)
                jid = "stale-done-job"
                tk = "stale-task-key"
                main.JOBS.clear()
                main.GLOBAL_HASH_INDEX.clear()
                main.JOB_ID_TO_HASH.clear()
                main.JOBS[jid] = {"id": jid, "status": "done", "result_srt": str(Path(td) / jid / "out.srt")}
                main.GLOBAL_HASH_INDEX[tk] = {"job_id": jid, "status": "done", "config": {}, "file_hash": "x"}
                main.JOB_ID_TO_HASH[jid] = tk

                response = main.get_job(jid)

                self.assertFalse(isinstance(response, dict))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(jid, main.JOBS)
                self.assertNotIn(jid, main.JOB_ID_TO_HASH)
                self.assertNotIn(tk, main.GLOBAL_HASH_INDEX)
        finally:
            main.JOBS_DIR = old_jobs_dir
            main.JOBS.clear()
            main.JOBS.update(old_jobs)
            main.GLOBAL_HASH_INDEX.clear()
            main.GLOBAL_HASH_INDEX.update(old_global)
            main.JOB_ID_TO_HASH.clear()
            main.JOB_ID_TO_HASH.update(old_reverse)


class TestP303ActiveJobsRecovery(unittest.TestCase):
    """P303: stale interrupted jobs must not create bogus recovery cards."""

    def _with_main_state(self):
        import backend.main as main

        return {
            "main": main,
            "jobs_dir": main.JOBS_DIR,
            "jobs": dict(main.JOBS),
            "global_index": dict(main.GLOBAL_HASH_INDEX),
            "reverse_index": dict(main.JOB_ID_TO_HASH),
        }

    def _restore_main_state(self, state):
        main = state["main"]
        main.JOBS_DIR = state["jobs_dir"]
        main.JOBS.clear()
        main.JOBS.update(state["jobs"])
        main.GLOBAL_HASH_INDEX.clear()
        main.GLOBAL_HASH_INDEX.update(state["global_index"])
        main.JOB_ID_TO_HASH.clear()
        main.JOB_ID_TO_HASH.update(state["reverse_index"])

    def _index_job(self, main, jid, status, cfg):
        task_key = f"task-{jid}"
        main.GLOBAL_HASH_INDEX[task_key] = {
            "job_id": jid,
            "status": status,
            "config": cfg,
            "file_hash": f"hash-{jid}",
        }
        main.JOB_ID_TO_HASH[jid] = task_key

    def test_active_jobs_skips_unrecoverable_interrupted_private_url(self):
        state = self._with_main_state()
        main = state["main"]
        try:
            with tempfile.TemporaryDirectory() as td:
                main.JOBS_DIR = Path(td)
                main.JOBS.clear()
                main.GLOBAL_HASH_INDEX.clear()
                main.JOB_ID_TO_HASH.clear()

                jid = "old-private-url"
                (main.JOBS_DIR / jid).mkdir()
                self._index_job(
                    main,
                    jid,
                    "interrupted",
                    {"source_url": "http://127.0.0.1:8766/api/config", "video_filename": "config"},
                )

                self.assertEqual(main.get_active_jobs(), {"jobs": []})
        finally:
            self._restore_main_state(state)

    def test_active_jobs_keeps_interrupted_job_with_input_file(self):
        state = self._with_main_state()
        main = state["main"]
        try:
            with tempfile.TemporaryDirectory() as td:
                main.JOBS_DIR = Path(td)
                main.JOBS.clear()
                main.GLOBAL_HASH_INDEX.clear()
                main.JOB_ID_TO_HASH.clear()

                jid = "recoverable-upload"
                job_dir = main.JOBS_DIR / jid
                job_dir.mkdir()
                (job_dir / "input.mp4").write_bytes(b"video")
                # P516：能否恢复看任务记录里的原视频地址
                (job_dir / "meta.json").write_text(json.dumps(
                    {"status": "interrupted", "config": {}, "source_path": str(job_dir / "input.mp4")}),
                    encoding="utf-8")
                self._index_job(main, jid, "interrupted", {})

                jobs = main.get_active_jobs()["jobs"]
                self.assertEqual([job["state"]["job_id"] for job in jobs], [jid])
        finally:
            self._restore_main_state(state)


# ═══════════════════════════════════════════════════════════
# Commit 2: Translation Quality — P241 + P248
# ═══════════════════════════════════════════════════════════

# P500 施工 ⑤：「≤10 句才补」_retry_failed_with_llm 的提示词检查 随整片翻译代码删除（方案 5.2）。


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
        """Non-GREEN segment with repeated pattern should still be filtered.

        闸门 2 真跑补漏：L4 三条文本重复分支已交出删除权，改为只标记。
        全库审计它们删掉的是 'Canada. Canada.'、'Oh yeah. Oh yeah.'、'Meow Meow'
        ——全是真话。本条意图不变，只把断言从「删除」改为「标可疑」。
        """
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="abcabcabc", l2_status="YELLOW")
        _layer4_regex(seg)
        self.assertFalse(seg.filtered, "重复模式已交出删除权")
        self.assertTrue(seg.uncertain,
                        "L4 should still MARK repeated-pattern when l2_status=YELLOW")

    def test_repeated_words_still_filters_non_green(self):
        """Non-GREEN segment with repeated words should still be filtered.

        闸门 2 真跑补漏：L4 三条文本重复分支已交出删除权，改为只标记。
        全库审计它们删掉的是 'Canada. Canada.'、'Oh yeah. Oh yeah.'、'Meow Meow'
        ——全是真话。本条意图不变，只把断言从「删除」改为「标可疑」。
        """
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="hey hey hey", l2_status="YELLOW")
        _layer4_regex(seg)
        self.assertFalse(seg.filtered, "真人说 hey hey hey 是常态，不许删")
        self.assertTrue(seg.uncertain,
                        "L4 should still MARK repeated-words when l2_status=YELLOW")

    def test_repeated_words_filters_when_no_l2_status(self):
        """Segment without l2_status should be filtered (default unsafe).

        闸门 2 真跑补漏：L4 三条文本重复分支已交出删除权，改为只标记。
        全库审计它们删掉的是 'Canada. Canada.'、'Oh yeah. Oh yeah.'、'Meow Meow'
        ——全是真话。本条意图不变，只把断言从「删除」改为「标可疑」。
        """
        from backend.core.hallucination_filter import _layer4_regex
        seg = _make_seg(text="hey hey hey")
        _layer4_regex(seg)
        self.assertFalse(seg.filtered, "真人说 hey hey hey 是常态，不许删")
        self.assertTrue(seg.uncertain,
                        "L4 should still MARK repeated-words when l2_status is missing")


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
        """转录模块必须在串行异常路径用 _fa_quality 跟踪 FA 质量。

        V3.08 复杂度拆分后 FA 质量逻辑移入 _annotate_fa_quality 辅助函数，
        因此检查整个模块源码而非仅 transcribe()。"""
        from backend import transcriber_v2
        src = inspect.getsource(transcriber_v2)
        self.assertIn("_fa_quality", src,
                      "transcription pipeline must track FA quality via _fa_quality variable")

    def test_segments_marked_with_fa_quality(self):
        """转录模块必须给结果段标注 _fa_quality 属性（移入辅助函数后检查整模块）。"""
        src_lines = inspect.getsource(
            __import__("backend.transcriber_v2", fromlist=["transcribe"])
        )
        self.assertIn("_fa_quality", src_lines,
                      "transcription pipeline must set seg._fa_quality on result segments")


# ═══════════════════════════════════════════════════════════
# P284: English Qwen primary must keep Whisper boundaries + references
# ═══════════════════════════════════════════════════════════

class TestP284EnglishWhisperReference(unittest.TestCase):
    def test_english_qwen_primary_uses_whisper_boundaries_and_reference_text(self):
        from backend.transcriber_v2 import _smart_assemble

        qwen_chunks = [{
            "text": "Hey Taylor what time is it Oh gosh I don't know I wish I had claw",
            "t_start": 0.0,
            "t_end": 3.4,
            "segments": [
                {"text": "Hey", "start": 0.00, "end": 0.15},
                {"text": "Taylor", "start": 0.16, "end": 0.40},
                {"text": "what", "start": 0.45, "end": 0.60},
                {"text": "time", "start": 0.61, "end": 0.78},
                {"text": "is", "start": 0.79, "end": 0.90},
                {"text": "it", "start": 0.91, "end": 1.05},
                {"text": "Oh", "start": 1.29, "end": 1.40},
                {"text": "gosh", "start": 1.41, "end": 1.65},
                {"text": "I", "start": 1.66, "end": 1.75},
                {"text": "don't", "start": 1.76, "end": 2.00},
                {"text": "know", "start": 2.01, "end": 2.20},
                {"text": "I", "start": 2.21, "end": 2.30},
                {"text": "wish", "start": 2.31, "end": 2.50},
                {"text": "I", "start": 2.51, "end": 2.60},
                {"text": "had", "start": 2.61, "end": 2.76},
                {"text": "claw", "start": 2.77, "end": 3.10},
            ],
        }]
        whisper_segs = [
            _FakeWhisperSeg(0.0, 1.18, "Hey Taylor what time is it?"),
            _FakeWhisperSeg(1.20, 3.40, "Oh gosh I don't know I wish I had clock"),
        ]

        result = _smart_assemble(qwen_chunks, whisper_segs, "en")

        self.assertGreaterEqual(len(result), 2)
        self.assertEqual(result[0].text, "Hey Taylor what time is it")
        self.assertNotIn("Oh", result[0].text)
        self.assertEqual(result[0].whisper_text, "Hey Taylor what time is it?")
        self.assertIn("claw", result[1].text)
        self.assertIn("clock", result[1].whisper_text)

    def test_japanese_qwen_primary_keeps_existing_assembly_path(self):
        from backend.transcriber_v2 import _smart_assemble

        qwen_chunks = [{
            "text": "何するの やめて",
            "t_start": 0.0,
            "t_end": 1.2,
            "segments": [
                {"text": "何するの", "start": 0.0, "end": 0.6},
                {"text": "やめて", "start": 0.8, "end": 1.2},
            ],
        }]
        whisper_segs = [
            _FakeWhisperSeg(0.0, 0.7, "何するの"),
            _FakeWhisperSeg(0.7, 1.3, "やめて"),
        ]

        result = _smart_assemble(qwen_chunks, whisper_segs, "ja")

        self.assertEqual([s.text for s in result], ["何するのやめて"])

    def test_short_fragment_merge_keeps_reference_text(self):
        from backend.transcriber_v2 import Segment, merge_short_subtitle_fragments

        first = Segment(0, 0.0, 0.2, "Oh")
        first.whisper_text = "Oh"
        second = Segment(1, 0.3, 3.0, "gosh I had claw")
        second.whisper_text = "gosh I had clock"

        result = merge_short_subtitle_fragments([first, second], "en")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Oh gosh I had claw")
        self.assertEqual(result[0].whisper_text, "Oh gosh I had clock")

    def test_both_engine_versions_reach_the_model_in_dual_mode(self):
        """P284 的关切：英语路线上副引擎的说法要能到模型面前。

        G2/B1+B3+B5：原来靠段上的 `whisper_text` + `[CONFLICT]` 块，
        那套按时间重叠拼接已删（边界对不齐，实测 68% 的「冲突」是纯错位）。
        现在主引擎的 claw 在 A 块、副引擎的 clock 在 B 块，两个都送到。
        """
        from types import SimpleNamespace

        from backend.worker import _build_refiner_prompt, _choose_refiner_mode

        seg = _make_seg(id=3, start=0.0, end=2.0, text="I wish I had claw")
        receipt = SimpleNamespace(
            reference_text="I wish I had clock", reference_usable=True,
        )

        mode = _choose_refiner_mode(receipt)
        user_prompt, _system_prompt = _build_refiner_prompt(
            [seg], mode, reference_text=receipt.reference_text,
        )

        self.assertEqual(mode, "dual")
        self.assertIn("段落 3: I wish I had claw", user_prompt)
        self.assertIn("clock", user_prompt)
        self.assertNotIn("CONFLICT", user_prompt)


class TestJobStatusPersistence(unittest.TestCase):
    """Completed reruns must not keep stale error text in meta.json."""

    def test_done_status_clears_stale_error(self):
        import json
        import backend.main as main

        old_jobs_dir = main.JOBS_DIR
        old_global = dict(main.GLOBAL_HASH_INDEX)
        old_reverse = dict(main.JOB_ID_TO_HASH)
        try:
            with tempfile.TemporaryDirectory() as td:
                main.JOBS_DIR = Path(td)
                jid = "job-with-old-error"
                job_dir = Path(td) / jid
                job_dir.mkdir()
                meta_path = job_dir / "meta.json"
                meta_path.write_text(
                    json.dumps({"status": "error", "error": "old crash"},
                               ensure_ascii=False),
                    encoding="utf-8",
                )

                main._persist_job_status(jid, "done")

                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                self.assertEqual(meta["status"], "done")
                self.assertNotIn("error", meta)
        finally:
            main.JOBS_DIR = old_jobs_dir
            main.GLOBAL_HASH_INDEX.clear()
            main.GLOBAL_HASH_INDEX.update(old_global)
            main.JOB_ID_TO_HASH.clear()
            main.JOB_ID_TO_HASH.update(old_reverse)


if __name__ == "__main__":
    unittest.main()
