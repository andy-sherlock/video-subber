"""P393: 长音频分块后，诊断词流必须是全局时间轴且与全文配对。

真实现场（任务 0e843020c8，23.4 分钟日文视频，1402.935 秒，5 块 5 次调用 1917 词）：
`qwen_raw.json` 的 word_timestamps 取自未加全局偏移的 raw_segments，
词流在 4 个接缝处倒退（310: 296.03→0.24，926: 308.88→4.56，
1442: 309.13→1.92，1786: 307.30→6.56），任务在断句前因
「词级时间顺序倒退」中止，长视频完全产不出字幕。

合同 A1-A11 见 pipelines/bug-pipeline-2026-08-16-G7.md ⑤.5。

Run: cd v3 && .venv/bin/python -m unittest test.test_p393_chunked_diagnostic_global_time -v
"""
import json
import os
import sys
import types
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_fake_mlx = types.ModuleType("mlx_qwen3_asr")
_fake_mlx.load_model = lambda *a, **k: (object(), {})
_fake_mlx.transcribe = lambda *a, **k: MagicMock(text="", segments=[])
sys.modules.setdefault("mlx_qwen3_asr", _fake_mlx)

import backend.core.qwen_asr_runner as runner


# ═══════════════════════════════════════════════════════════
# 夹具：两个带 5 秒重叠的分块，复刻真实现场的接缝形状
# ═══════════════════════════════════════════════════════════
#
# 块0 负责 [0, 300]，送模型 [0, 305]，extract_start = 0
# 块1 负责 [300, 600]，送模型 [295, 605]，extract_start = 295
#
# 块1 的模型输出以「块内相对时间」计：
#   "重复A" @0.24  → 全局 295.24 → 落在块0 已覆盖的重叠区 → 应被裁掉
#   "重复B" @1.00  → 全局 296.00 → 同上，应被裁掉
#   "首词"   @13.76 → 全局 308.76 → 块1 真正贡献的第一个词
#   "零时长" @20.00 (start==end) → 全局 315.00 → 必须保留（A4）
#   "尾词"   @300.0 → 全局 595.00 → 正常
#
# 未修复时诊断取 raw_segments，块1 首词是 0.24 秒 → 相对块0 尾词 296.03 倒退。

def _chunk0():
    raw = [
        {"text": "开场", "start": 10.96, "end": 13.44},
        {"text": "中间", "start": 100.0, "end": 101.0},
        {"text": "块零零时长", "start": 150.0, "end": 150.0},
        {"text": "块零末词", "start": 295.0, "end": 296.03},
    ]
    return {
        "text": "开场中间块零零时长块零末词",
        "raw_text": "开场中间块零零时长块零末词",
        "raw_segments": raw,
        "segments": runner._clip_chunk_segments(raw, 0.0, 0.0, 300.0),
        "t_start": 0.0,
        "t_end": 300.0,
    }


def _chunk1():
    raw = [
        {"text": "块零末词", "start": 0.24, "end": 1.03},
        {"text": "重复B", "start": 1.00, "end": 2.00},
        {"text": "首词", "start": 13.76, "end": 14.50},
        {"text": "零时长", "start": 20.0, "end": 20.0},
        {"text": "尾词", "start": 300.0, "end": 301.0},
    ]
    return {
        "text": "块零末词重复B首词零时长尾词",
        "raw_text": "块零末词重复B首词零时长尾词",
        "raw_segments": raw,
        "segments": runner._clip_chunk_segments(raw, 295.0, 300.0, 600.0),
        "t_start": 300.0,
        "t_end": 600.0,
    }


def _two_chunks():
    return [_chunk0(), _chunk1()]


def _payload(chunks, language="zh"):
    """取诊断汇总产物 (full_text, word_timestamps)。

    修复后应存在单一配对函数；修复前退回两个独立选择器，
    以便这个测试在旧代码上真实地跑到断言并失败，而不是 AttributeError 跳过。
    """
    d = runner._chunked_diagnostic_payload(chunks, language)
    return d["full_text"], d["word_timestamps"]


class TestP393GlobalTimeline(unittest.TestCase):
    """A1 / A3 / A4：全局时间、去重叠、保留零时长词。"""

    def test_a1_second_chunk_first_word_does_not_reset_to_zero(self):
        """A1：第二块首词不得回到 0 附近（真实现场 310 号词 296.03→0.24）。"""
        _full, words = _payload(_two_chunks())
        chunk1_words = [w for w in words if float(w["start"]) > 296.03]
        self.assertTrue(chunk1_words, "第二块没有任何词进入诊断词流")
        self.assertGreater(
            float(chunk1_words[0]["start"]), 300.0,
            "第二块首词落回块内相对时间——P393 未修复")

    def test_a1_all_word_starts_are_globally_monotonic(self):
        """A1：全部词的 start 必须单调不减（下游 _copy_and_validate_words 的判据）。"""
        _full, words = _payload(_two_chunks())
        starts = [float(w["start"]) for w in words]
        for i in range(1, len(starts)):
            self.assertGreaterEqual(
                starts[i], starts[i - 1],
                f"第 {i} 个词时间倒退：{starts[i - 1]} → {starts[i]}")

    def test_a1_passes_downstream_monotonic_gate(self):
        """A1：必须能通过断句真正使用的那道闸门，而不只是自测单调。"""
        from backend.core.sentence_groups import _copy_and_validate_words
        _full, words = _payload(_two_chunks())
        _copy_and_validate_words(words)  # 倒退时抛 SentenceGroupingError

    def test_a3_overlap_duplicates_do_not_enter_word_stream(self):
        """A3：重叠缓冲里被块0覆盖的重复词不得再出现。"""
        _full, words = _payload(_two_chunks())
        texts = [w["text"] for w in words]
        self.assertEqual(
            texts.count("块零末词"), 1,
            "重叠区的重复词进入了有效词流")
        self.assertNotIn("重复B", texts, "重叠区的重复词未被裁掉")

    def test_a3_word_text_and_order_preserved_after_dedup(self):
        """A3：去重后剩余词的文本与顺序不变。"""
        _full, words = _payload(_two_chunks())
        self.assertEqual(
            [w["text"] for w in words],
            ["开场", "中间", "块零零时长", "块零末词",
             "首词", "零时长", "尾词"])

    def test_a4_zero_duration_words_are_kept_in_diagnostic(self):
        """A4：零时长词（start==end）必须保留，长视频不得比短视频少字。"""
        _full, words = _payload(_two_chunks())
        zero = [w for w in words if float(w["start"]) == float(w["end"])]
        self.assertEqual(
            len(zero), 2,
            "零时长词被裁掉——长视频会比短视频少字")
        self.assertEqual({w["text"] for w in zero},
                         {"块零零时长", "零时长"})


class TestP393FullTextPairing(unittest.TestCase):
    """A2：full_text 与 word_timestamps 必须同源配对。"""

    def test_a2_full_text_matches_word_stream_char_for_char(self):
        """A2：直接跑下游那道配对闸门（全文与词级文字不一致）。"""
        from backend.core.sentence_groups import build_llm_primary_boundary_prompt
        full, words = _payload(_two_chunks())
        build_llm_primary_boundary_prompt(
            words, language="zh", full_text=full)

    def test_a2_full_text_excludes_overlap_duplicates(self):
        """A2：全文不得含重叠区被裁掉的词，否则与词流对不上。"""
        full, _words = _payload(_two_chunks())
        self.assertNotIn("重复B", full)
        self.assertEqual(full.count("块零末词"), 1)


class TestP393SegmentsUnchanged(unittest.TestCase):
    """A4 后半：L1 消费的 chunk["segments"] 内容必须与改动前逐字相同。"""

    def test_a4_l1_segments_now_keep_zero_duration_words(self):
        """G12 第二批段三：断言翻转——`segments` 现在**保留**零时长词。

        A4 当初的顾虑是「混入零时长词，L1 影响面失控」。
        P430 要求的对照实验（拖了三个月）现在做完了：把零时长词加进 L1 的
        对齐证据，两个真实任务**各 0 条改判**。影响面为零，顾虑不成立。

        真正需要修的是 `_segment_has_alignment` 的严格不等号——零宽区间
        放不进任何东西，导致零时长词组成的段被判成「没有对齐证据」。
        那一条已在同批修复，守着它的是
        `test_g12b_evidence_preservation.py::TestC2AlignmentBoundary`。

        扔掉零时长词的代价：两个真实任务 655 个里 **621 个是正常词**
        （含 airport／bridge／climbing 等 42 个实词），长视频因此永久少字。
        """
        c1 = _chunk1()
        zero = [s for s in c1["segments"]
                if float(s["start"]) == float(s["end"])]
        self.assertTrue(zero, "零时长词被扔掉了；长视频会比短视频少字")

    def test_a4_l1_segments_are_global_and_clipped(self):
        """A4：segments 维持既有语义（全局时间 + 去重叠）。

        G12 第二批段三起零时长词也在其中（见上一条），
        本条守的「全局偏移 + 去重叠」一点没变。
        """
        c1 = _chunk1()
        self.assertEqual(
            c1["segments"],
            [{"text": "首词", "start": 308.76, "end": 309.5},
             {"text": "零时长", "start": 315.0, "end": 315.0},
             {"text": "尾词", "start": 595.0, "end": 596.0}])


class TestP393FailFast(unittest.TestCase):
    """A6：写盘前单调性自检，不通过直接抛异常，坏文件不得产生。"""

    def test_a6_backwards_word_stream_raises_and_writes_no_file(self):
        bad = [
            {"text": "甲", "start": 296.03, "end": 296.5},
            {"text": "乙", "start": 0.24, "end": 1.0},
        ]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Exception) as ctx:
                runner._write_qwen_raw_diagnostic(
                    d, full_text="甲乙", word_timestamps=bad,
                    audio_duration=600.0, model_call_count=2,
                    input_ranges=[{"start": 0.0, "end": 305.0}])
            self.assertFalse(
                os.path.exists(os.path.join(d, "qwen_raw.json")),
                "校验失败却仍写出了坏文件")
            msg = str(ctx.exception)
            # S2：异常消息只允许数字，禁止拼入词文本
            self.assertNotIn("甲", msg)
            self.assertNotIn("乙", msg)

    def test_a6_good_word_stream_still_writes(self):
        good = [
            {"text": "甲", "start": 1.0, "end": 2.0},
            {"text": "乙", "start": 2.0, "end": 3.0},
        ]
        with tempfile.TemporaryDirectory() as d:
            runner._write_qwen_raw_diagnostic(
                d, full_text="甲乙", word_timestamps=good,
                audio_duration=600.0, model_call_count=2,
                input_ranges=[{"start": 0.0, "end": 305.0}])
            self.assertTrue(
                os.path.exists(os.path.join(d, "qwen_raw.json")))


class TestP393CheckpointFieldGuard(unittest.TestCase):
    """A7：缺 raw_segments/raw_text 的旧格式 checkpoint 必须整份丢弃。"""

    def test_a7_legacy_checkpoint_without_raw_fields_is_discarded(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "qwen_chunks.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "time_chunks": [[0.0, 300.0], [300.0, 600.0]],
                    "results": [
                        {"text": "旧", "segments": [],
                         "t_start": 0.0, "t_end": 300.0},
                    ],
                    "done_idx": 1,
                }, f)
            saved, resume = runner._load_qwen_chunk_ckpt(
                path, [(0.0, 300.0), (300.0, 600.0)])
        self.assertEqual(resume, 0, "旧格式 checkpoint 未被丢弃")
        self.assertEqual(saved, [])

    def test_a7_current_format_checkpoint_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "qwen_chunks.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "time_chunks": [[0.0, 300.0], [300.0, 600.0]],
                    "results": [_chunk0()],
                    "done_idx": 1,
                }, f)
            _saved, resume = runner._load_qwen_chunk_ckpt(
                path, [(0.0, 300.0), (300.0, 600.0)])
        self.assertEqual(resume, 1, "当前格式 checkpoint 被误丢弃")


class TestP393AllWritePathsConsistent(unittest.TestCase):
    """A5：正常 / 恢复 / 模型加载失败 三条路径写出的诊断必须一致。"""

    def _run_and_read(self, cache_dir, *, model_loads=True, prefilled=False):
        time_chunks = [(0.0, 300.0), (300.0, 600.0)]
        if prefilled:
            runner._save_qwen_chunk_ckpt(
                os.path.join(cache_dir, "qwen_chunks.json"),
                time_chunks, _two_chunks(), 2)

        def _fake_transcribe(model, audio_path, t_start, t_end, language,
                             chunk_idx=0, total_chunks=1):
            return _two_chunks()[chunk_idx]

        loader = (patch.object(runner, "_load_qwen_model", return_value=object())
                  if model_loads else
                  patch.object(runner, "_load_qwen_model",
                               side_effect=RuntimeError("no model")))
        with loader, \
             patch.object(runner, "_transcribe_chunk", _fake_transcribe), \
             patch.object(runner, "_chunk_by_time", return_value=time_chunks):
            runner._run_qwen_asr_chunked(
                "/fake.wav", 600.0, language="zh", cache_dir=cache_dir)
        with open(os.path.join(cache_dir, "qwen_raw.json"),
                  encoding="utf-8") as f:
            return json.load(f)

    def test_a5_normal_and_resume_and_loadfail_agree(self):
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2, \
             tempfile.TemporaryDirectory() as d3:
            normal = self._run_and_read(d1)
            resume = self._run_and_read(d2, prefilled=True)
            loadfail = self._run_and_read(d3, model_loads=False,
                                          prefilled=True)
        for name, doc in (("恢复路径", resume), ("模型加载失败路径", loadfail)):
            self.assertEqual(
                doc["word_timestamps"], normal["word_timestamps"],
                f"{name}写出的词流与正常路径不一致")
            self.assertEqual(
                doc["full_text"], normal["full_text"],
                f"{name}写出的全文与正常路径不一致")


class TestChunkFailureAbortsRun(unittest.TestCase):
    """G7：某块识别失败就中止，不交付残缺字幕；已成功的块留给重试。

    旧行为是返回空块继续跑，最终给用户一份中间缺 5 分钟、看起来却完整的字幕，
    且失败块照样推进 checkpoint——重跑永远跳过它，那几分钟永久丢失。
    那个行为从来没被决策过，只是 `except` 里「别让程序崩」的默认结果。
    """

    def _plan(self):
        return [(0.0, 300.0), (300.0, 600.0), (600.0, 900.0)]

    def _fake_model_result(self, idx):
        """模型返回值。走真实的 _transcribe_chunk，不绕过它。"""
        return SimpleNamespace(
            text=f"块{idx}",
            segments=[{"text": f"块{idx}", "start": 1.0, "end": 2.0}])

    def test_failed_chunk_aborts_and_names_the_time_range(self):
        """失败必须中止，且消息要指出是哪一段（用户据此判断要不要重试）。"""
        n = {"i": 0}

        def fake_model(*a, **k):
            i = n["i"]; n["i"] += 1
            if i == 1:
                raise RuntimeError("boom")
            return self._fake_model_result(i)

        with tempfile.TemporaryDirectory() as d:
            with patch.object(runner, "_load_qwen_model", return_value=object()), \
                 patch.object(runner, "_USE_MLX", True), \
                 patch.object(runner, "_extract_audio_chunk"), \
                 patch.object(runner, "_mlx_transcribe", fake_model), \
                 patch.object(runner, "_chunk_by_time", return_value=self._plan()):
                with self.assertRaises(runner.ChunkTranscriptionError) as ctx:
                    runner._run_qwen_asr_chunked(
                        "/fake.wav", 900.0, language="zh", cache_dir=d)
            msg = str(ctx.exception)
            self.assertIn("第 2 段", msg)
            self.assertIn("5:00", msg)
            self.assertIn("10:00", msg)
            self.assertFalse(
                os.path.exists(os.path.join(d, "qwen_raw.json")),
                "识别未完成却写出了词流——残缺成品会被交付下去")

    def test_successful_chunks_survive_for_retry(self):
        """断点重试：已成功的块留在 checkpoint，重试从失败块继续。"""
        calls = []
        fail = {"on": True}

        def fake_model(path, **k):
            idx = int(path.split("qwen_chunk_")[1].split("_")[0])
            calls.append(idx)
            if idx == 1 and fail["on"]:
                raise RuntimeError("boom")
            return self._fake_model_result(idx)

        with tempfile.TemporaryDirectory() as d:
            with patch.object(runner, "_load_qwen_model", return_value=object()), \
                 patch.object(runner, "_USE_MLX", True), \
                 patch.object(runner, "_extract_audio_chunk"), \
                 patch.object(runner, "_mlx_transcribe", fake_model), \
                 patch.object(runner, "_chunk_by_time", return_value=self._plan()):
                with self.assertRaises(runner.ChunkTranscriptionError):
                    runner._run_qwen_asr_chunked(
                        "/fake.wav", 900.0, language="zh", cache_dir=d)
                ck = json.load(open(os.path.join(d, "qwen_chunks.json"),
                                    encoding="utf-8"))
                self.assertEqual(ck["done_idx"], 1, "第 1 块成功却没留下")
                self.assertEqual(len(ck["results"]), 1)

                calls.clear()
                fail["on"] = False
                runner._run_qwen_asr_chunked(
                    "/fake.wav", 900.0, language="zh", cache_dir=d)

        self.assertEqual(calls, [1, 2],
                         f"重试应从失败块继续、不重跑第 1 块（实际 {calls}）")


if __name__ == "__main__":
    unittest.main()


class TestP393PyTorchBackend(unittest.TestCase):
    """⑦.5 diff 审查发现：PyTorch 后端没有词级时间，全文不得因此丢失。

    旧实现从 raw_text 取全文，新实现从裁剪后的词拼接。PyTorch 路径
    (`_transcribe_chunk` 的 else 分支令 raw_segments=[]) 一个词都没有，
    按词拼接会得到空串，诊断文件就白存了。22 项 MLX 夹具测试全绿也照不出这条。
    """

    def _pt_chunks(self):
        return [
            {"text": "こんにちは世界", "raw_text": "こんにちは世界",
             "raw_segments": [], "segments": [],
             "t_start": 0.0, "t_end": 300.0},
            {"text": "第二块文字", "raw_text": "第二块文字",
             "raw_segments": [], "segments": [],
             "t_start": 300.0, "t_end": 600.0},
        ]

    def test_pytorch_path_keeps_full_text(self):
        p = runner._chunked_diagnostic_payload(self._pt_chunks(), "ja")
        self.assertEqual(p["full_text"], "こんにちは世界第二块文字",
                         "PyTorch 路径的全文被清空了")
        self.assertEqual(p["word_timestamps"], [])

    def test_mlx_path_still_pairs_strictly(self):
        """回退只在词流整体为空时生效，不得放松 MLX 路径的配对约束。"""
        p = runner._chunked_diagnostic_payload(_two_chunks(), "zh")
        self.assertTrue(p["word_timestamps"])
        self.assertNotIn("重复B", p["full_text"])
