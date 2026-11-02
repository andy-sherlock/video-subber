"""G2 · A1–A9 + B11 + B12 + B14：润色产物落盘，成为翻译的唯一输入。

P400 的病：润色改了原文，但改完只活在内存里。程序重开 → 重跑润色（LLM，
每次结果不一样）→ 新原文；而翻译缓存只比对「编号 + 起止时间」，不比对文本
→ **新原文配旧译文，没有任何报错**。

修法两件事：
1. 润色产出的最终原文落盘成 `refined_transcript.json`，下次直接读回。
2. 翻译缓存和快照的身份都加上文本，文本变了就不认。

**最关键的约束（B14/A3/D1）**：润色阶段之后，`segment.text` 只允许有一个
写入点——装载函数。润色要是顺手就地改了内存里的 segments，内存那份和落盘
那份就是两个源，字幕的 `[未翻译]` 兜底行和 quality 反思读到的仍是内存那份，
「唯一输入」名存实亡。
"""
import sys
from pathlib import Path

from test import locked_decision
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ast
import json
import tempfile
import unittest

from backend.transcriber_v2 import TranscriptionReceipt
from backend.worker import (
    _build_refined_payload,
    _load_refined_into_segments,
    _read_refined_transcript,
    _refined_boundary_fingerprint,
    _REFINED_TRANSCRIPT_VERSION,
    _REFINER_PROMPT_VERSION,
    _run_transcript_refiner,
    _translation_cache_matches_segments,
    _write_refined_transcript,
)


class _Seg:
    def __init__(self, id, text, start=0.0, end=1.0):
        self.id = id
        self.text = text
        self.start = start
        self.end = end
        self.uncertain = False


def _receipt(reference_text="", reference_usable=False):
    return TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text="hello",
        source_words=[{"text": "hello", "start": 0.0, "end": 1.0}],
        reference_text=reference_text,
        reference_usable=reference_usable,
               language_decision=locked_decision(),
        artifact_language_check="ok",
           )


class _ScriptedLLM:
    """按剧本回答；`fail_on` 里的调用序号回一个不是 JSON 的回答（窗口失败）。

    D-1（合同变更）：以前这里抛 ConnectionError 模拟窗口失败；断网现由付费请求
    函数原地等，不再算窗口失败。窗口失败只剩「AI 回复格式有误」这一种。
    """

    def __init__(self, replies=None, fail_on=()):
        self.replies = list(replies or [])
        self.fail_on = set(fail_on)
        self.calls = 0

    def translate_raw_json(self, user_prompt, system_prompt=""):
        index = self.calls
        self.calls += 1
        if index in self.fail_on:
            return "这不是 JSON（窗口失败）"
        if index < len(self.replies):
            return self.replies[index]
        return '{"corrections": []}'


class TestA1WriteOnceAfterAllWindows(unittest.TestCase):
    def test_refiner_writes_once_after_all_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(1, "the fudge stack")]
            llm = _ScriptedLLM([
                '{"corrections": [{"seg_id": 1, "fixed_text": "the fudge sack"}]}',
            ])

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=llm,
            )

            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], _REFINED_TRANSCRIPT_VERSION)
            self.assertEqual(payload["prompt_version"], _REFINER_PROMPT_VERSION)
            self.assertEqual(payload["refiner_status"], "applied")
            self.assertEqual(payload["accepted_count"], 1)
            self.assertEqual(payload["segments"][0]["text"], "the fudge sack")
            self.assertTrue(payload["segments"][0]["text_sha"])
            self.assertTrue(payload["text_fingerprint"])
            self.assertTrue(payload["boundary_fingerprint"])

    def test_window_failure_writes_nothing(self):
        """D3：任一窗口失败 → 一个字也不写。

        半截成品会被下一次当成完整输入读回去，那比重跑一次贵得多。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(i, f"seg{i}", start=float(i), end=float(i) + 1.0)
                        for i in range(4)]
            llm = _ScriptedLLM(fail_on=(1,))

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=llm, window_size=2, context_pad=0,
            )

            self.assertFalse(path.exists())

    def test_window_failure_leaves_segments_untouched(self):
        """没落盘 → 也不许装载 → 原文保持未润色。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(i, f"seg{i}", start=float(i), end=float(i) + 1.0)
                        for i in range(4)]
            llm = _ScriptedLLM(
                ['{"corrections": [{"seg_id": 0, "fixed_text": "CHANGED"}]}'],
                fail_on=(1,),
            )

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=llm, window_size=2, context_pad=0,
            )

            self.assertEqual(segments[0].text, "seg0")

    def test_no_change_status_when_model_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(1, "hello world")]

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=_ScriptedLLM(),
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["refiner_status"], "no_change")
            self.assertEqual(payload["accepted_count"], 0)


class TestA2ExistingFileSkipsLLM(unittest.TestCase):
    def test_existing_refined_file_skips_llm(self):
        """文件在 → 一次 LLM 都不许调。

        重跑不只是白花钱：润色是非确定性的，重算会**覆盖用户已经看到的成品**。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(1, "the fudge stack")]
            receipt = _receipt()
            payload = _build_refined_payload(
                [_Seg(1, "the fudge sack")],
                source_fingerprint=receipt.source_fingerprint,
                refiner_status="applied",
                accepted_count=1,
            )
            _write_refined_transcript(path, payload)
            llm = _ScriptedLLM()

            _run_transcript_refiner(
                ["k"], segments, receipt=receipt, refined_path=path,
                llm_backend=llm,
            )

            self.assertEqual(llm.calls, 0)
            self.assertEqual(segments[0].text, "the fudge sack")


class TestB11PromptVersion(unittest.TestCase):
    def test_prompt_version_mismatch_triggers_rerun(self):
        """提示词改了却复用旧成品 = 改了没效果。P390 已经栽过一次。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            receipt = _receipt()
            payload = _build_refined_payload(
                [_Seg(1, "old text")],
                source_fingerprint=receipt.source_fingerprint,
                refiner_status="applied",
                accepted_count=1,
            )
            payload["prompt_version"] = _REFINER_PROMPT_VERSION - 1
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = _read_refined_transcript(
                path,
                source_fingerprint=receipt.source_fingerprint,
                boundary_fingerprint=payload["boundary_fingerprint"],
            )

            self.assertIsNone(result)

    def test_prompt_version_is_separate_from_format_version(self):
        """两个字段，不是一个。格式没变但提示词变了也必须重跑。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            receipt = _receipt()
            payload = _build_refined_payload(
                [_Seg(1, "old text")],
                source_fingerprint=receipt.source_fingerprint,
                refiner_status="applied",
                accepted_count=1,
            )

            self.assertIn("version", payload)
            self.assertIn("prompt_version", payload)
            self.assertIsNot(
                payload["version"], payload.get("prompt_version_alias")
            )


class TestA6CorruptFileTriggersRerun(unittest.TestCase):
    def test_corrupt_refined_file_triggers_rerun(self):
        """读不出来 = 缺失 = 重跑润色。**绝不静默沿用旧翻译缓存。**"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            path.write_text("{ this is not json", encoding="utf-8")
            segments = [_Seg(1, "the fudge stack")]
            llm = _ScriptedLLM([
                '{"corrections": [{"seg_id": 1, "fixed_text": "the fudge sack"}]}',
            ])

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=llm,
            )

            self.assertEqual(llm.calls, 1)
            self.assertEqual(segments[0].text, "the fudge sack")

    def test_fingerprint_mismatch_triggers_rerun(self):
        """断句变了 → 旧成品的段落身份对不上 → 不许往上套。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            receipt = _receipt()
            payload = _build_refined_payload(
                [_Seg(1, "old text", start=0.0, end=1.0)],
                source_fingerprint=receipt.source_fingerprint,
                refiner_status="applied",
                accepted_count=1,
            )
            _write_refined_transcript(path, payload)

            # 当前这一轮的断句不一样了
            result = _read_refined_transcript(
                path,
                source_fingerprint=receipt.source_fingerprint,
                boundary_fingerprint=_refined_boundary_fingerprint(
                    [_Seg(1, "x", start=0.0, end=7.7)]
                ),
            )

            self.assertIsNone(result)


class TestA8PartialRetryException(unittest.TestCase):
    def test_partial_retry_without_file_skips_refiner(self):
        """重试/接受部分结果时文件缺失 → **不跑润色**，按未润色原文继续。

        这里跑润色 = 改原文 = 已经成功的那些句子的译文当场作废。
        那是在拆掉现有的保护，不是在修 bug。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(1, "the fudge stack")]
            llm = _ScriptedLLM([
                '{"corrections": [{"seg_id": 1, "fixed_text": "CHANGED"}]}',
            ])

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=llm, allow_refine=False,
            )

            self.assertEqual(llm.calls, 0)
            self.assertEqual(segments[0].text, "the fudge stack")
            self.assertFalse(path.exists())

    def test_partial_retry_with_file_still_loads(self):
        """文件在 → 照样走统一装载路径（A8 只豁免「跑」，不豁免「读」）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            receipt = _receipt()
            segments = [_Seg(1, "the fudge stack")]
            payload = _build_refined_payload(
                [_Seg(1, "the fudge sack")],
                source_fingerprint=receipt.source_fingerprint,
                refiner_status="applied",
                accepted_count=1,
            )
            _write_refined_transcript(path, payload)

            _run_transcript_refiner(
                ["k"], segments, receipt=receipt, refined_path=path,
                llm_backend=_ScriptedLLM(), allow_refine=False,
            )

            self.assertEqual(segments[0].text, "the fudge sack")


class TestB14LoaderIsSoleWriter(unittest.TestCase):
    def test_refiner_does_not_mutate_segments(self):
        """润色跑完（但没落盘）→ 入参 segments 的 text 一个字都不许变。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(i, f"seg{i}", start=float(i), end=float(i) + 1.0)
                        for i in range(4)]
            originals = [s.text for s in segments]
            llm = _ScriptedLLM(
                ['{"corrections": [{"seg_id": 0, "fixed_text": "CHANGED"}]}'],
                fail_on=(1,),
            )

            _run_transcript_refiner(
                ["k"], segments, receipt=_receipt(), refined_path=path,
                llm_backend=llm, window_size=2, context_pad=0,
            )

            self.assertEqual([s.text for s in segments], originals)

    def test_loader_is_sole_writer(self):
        """静态扫描 `.text =` 赋值点。

        **这是 D1 裁决的唯一机器手段。** 它防的是把装载实现成「润色函数内部
        顺手读回」——那样赋值点就回到润色里，内存和落盘重新变成两个源，
        整条约束白裁。人是记不住这条的，只有扫描记得住。
        """
        source = Path(__file__).resolve().parent.parent / "backend" / "worker.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        writers = set()
        stack = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_Assign(self, node):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "text":
                        writers.add(stack[-1] if stack else "<module>")
                self.generic_visit(node)

        Visitor().visit(tree)

        self.assertEqual(
            writers,
            {"_apply_refiner_corrections", "_load_refined_into_segments"},
            f"worker.py 里写 .text 的函数变了：{sorted(writers)}。"
            "润色阶段之后只允许装载函数写；"
            "`_apply_refiner_corrections` 只准写副本。",
        )

    def test_loader_is_a_module_level_function(self):
        """装载必须是独立函数，不能是藏在润色里的闭包。"""
        source = Path(__file__).resolve().parent.parent / "backend" / "worker.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        top_level = {
            node.name for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }

        self.assertIn("_load_refined_into_segments", top_level)

    def test_loader_writes_text_by_segment_id(self):
        segments = [_Seg(1, "old one"), _Seg(2, "old two")]
        payload = _build_refined_payload(
            [_Seg(2, "new two"), _Seg(1, "new one")],
            source_fingerprint="fp",
            refiner_status="applied",
            accepted_count=2,
        )

        _load_refined_into_segments(segments, payload)

        self.assertEqual(segments[0].text, "new one")
        self.assertEqual(segments[1].text, "new two")

    def test_loader_ignores_unknown_ids(self):
        segments = [_Seg(1, "old one")]
        payload = _build_refined_payload(
            [_Seg(99, "stranger")],
            source_fingerprint="fp",
            refiner_status="applied",
            accepted_count=1,
        )

        _load_refined_into_segments(segments, payload)

        self.assertEqual(segments[0].text, "old one")


class TestB12Logging(unittest.TestCase):
    def test_refiner_logs_path_and_status(self):
        """落盘之后日志必须写清楚「存哪了、什么状态」。

        没有这条，成品对用户和排查都是不可见的：出问题不知道该去删哪个文件。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refined_transcript.json"
            segments = [_Seg(1, "the fudge stack")]

            with self.assertLogs("worker", level="INFO") as captured:
                _run_transcript_refiner(
                    ["k"], segments, receipt=_receipt(), refined_path=path,
                    llm_backend=_ScriptedLLM(),
                )

            joined = "\n".join(captured.output)
            self.assertIn(str(path), joined)
            self.assertIn("no_change", joined)


class TestA4TranslationCacheIncludesText(unittest.TestCase):
    def test_cache_rejected_when_source_text_changed(self):
        """P400 主症状：编号和时间没变、原文变了，旧缓存必须判不匹配。"""
        segments = [_Seg(1, "new source", start=0.0, end=1.0)]
        cache_rows = [{
            "id": 1, "start": 0.0, "end": 1.0,
            "text": "old source", "zh": "旧译文",
        }]

        self.assertFalse(
            _translation_cache_matches_segments(cache_rows, segments)
        )

    def test_cache_accepted_when_everything_matches(self):
        segments = [_Seg(1, "same source", start=0.0, end=1.0)]
        cache_rows = [{
            "id": 1, "start": 0.0, "end": 1.0,
            "text": "same source", "zh": "译文",
        }]

        self.assertTrue(
            _translation_cache_matches_segments(cache_rows, segments)
        )

    def test_cache_rejected_when_row_has_no_text(self):
        """旧格式行没有 text 字段 → 身份不可验证 → 不认。"""
        segments = [_Seg(1, "source", start=0.0, end=1.0)]
        cache_rows = [{"id": 1, "start": 0.0, "end": 1.0, "zh": "译文"}]

        self.assertFalse(
            _translation_cache_matches_segments(cache_rows, segments)
        )


class TestA5A9SnapshotIdentity(unittest.TestCase):
    def test_snapshot_fingerprint_mismatch_discards_all(self):
        """Worker 在翻译途中崩了 → 重来时快照的原文指纹必须对得上。

        只按编号恢复 → 一份字幕里混着两套原文的译文。
        这是本批最危险的一条路。
        """
        from backend.worker import (
            _read_translation_snapshot, _write_translation_snapshot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".snapshot.json"
            _write_translation_snapshot(
                path, {1: "译文一", 2: "译文二"},
                refined_fingerprint="FINGERPRINT-A",
            )

            items = _read_translation_snapshot(
                path, refined_fingerprint="FINGERPRINT-B"
            )

            self.assertEqual(items, {})

    def test_snapshot_matching_fingerprint_restores(self):
        from backend.worker import (
            _read_translation_snapshot, _write_translation_snapshot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".snapshot.json"
            _write_translation_snapshot(
                path, {1: "译文一"}, refined_fingerprint="FINGERPRINT-A",
            )

            items = _read_translation_snapshot(
                path, refined_fingerprint="FINGERPRINT-A"
            )

            self.assertEqual(items, {1: "译文一"})

    def test_legacy_snapshot_format_rejected(self):
        """旧格式（裸 {id: 译文}）作废，不许被当成有效条目读进来。"""
        from backend.worker import _read_translation_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".snapshot.json"
            path.write_text(
                json.dumps({"1": "旧译文"}), encoding="utf-8"
            )

            items = _read_translation_snapshot(
                path, refined_fingerprint="FINGERPRINT-A"
            )

            self.assertEqual(items, {})


if __name__ == "__main__":
    unittest.main()
