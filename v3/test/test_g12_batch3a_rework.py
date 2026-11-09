"""G12 第三批 A · 外审返工（2026-09-05 CHANGES REQUIRED）。

外审签字前跑了六条真实复现，**六条全部成立**。病根只有一句话：

    东西都做好了，就是没插上电。

完整性检查、失败原因、黄色提示、音视频探测——四样都写好了，
四个插头全都悬空。2258 个测试全绿，因为测试测的是**东西本身好不好**，
没测**插没插上**。

本文件按外审清单逐条补测，每条都必须能红绿验证。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_rework.py -v
"""
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.qwen_asr_runner import (
    _load_qwen_chunk_ckpt, _merge_chunk_seams, _refresh_chunk_delivery,
    _seam_norm, chunks_are_complete,
)


def _chunk(i: int) -> dict:
    return {"text": f"chunk{i}", "raw_text": f"chunk{i}",
            "raw_segments": [{"text": f"w{i}", "start": 0.0, "end": 1.0}],
            "t_start": i * 300.0, "t_end": (i + 1) * 300.0}


def _write_ckpt(tmp: Path, plan: list, results: list, done_idx: int) -> str:
    p = tmp / "qwen_chunks.json"
    p.write_text(json.dumps(
        {"time_chunks": plan, "results": results, "done_idx": done_idx}),
        encoding="utf-8")
    return str(p)


class TestCheckpointLedgerMustMatchReality(unittest.TestCase):
    """外审①：账本说跑完 10 块，实际只存了 6 块——程序报告「完整」。

    **这是 P464 原病换了个入口继续存在**，属于阻断项。

    `_load_qwen_chunk_ckpt` 返回 `(saved_chunks, done_idx)`，
    两个值来自同一个文件的两个字段，**从来没有互相核对过**。
    调用方 `resume_idx >= len(time_chunks)` 一成立就写死 `complete=True`。

    修的是**返回值本身的自洽性**（第 1 层）：
    让这个函数不可能交出「账本和货对不上」的一对值，
    下游任何消费者都不必再自己提防这件事。
    """

    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.plan = [[i * 300.0, (i + 1) * 300.0] for i in range(10)]

    def test_ledger_claims_more_than_stored(self):
        """账本说 10、实际 6 —— 必须按 6 算，从第 7 块继续跑。"""
        ckpt = _write_ckpt(self.tmp, self.plan, [_chunk(i) for i in range(6)], 10)

        chunks, resume = _load_qwen_chunk_ckpt(ckpt, self.plan)

        self.assertEqual(len(chunks), 6)
        self.assertEqual(resume, 6,
                         "账本吹牛不能被采信——按实际存下的块数续跑，"
                         "否则 resume_idx=10 会跳过全部转录，直接把 6 块当成品交出去")
        self.assertFalse(chunks_are_complete(resume, chunks, self.plan),
                         "6/10 不是完整")

    def test_stored_more_than_ledger_claims(self):
        """账本说 6、实际存了 10 —— 多出来的没验证过，按 6 算。

        写入顺序是先写 results 再更新 done_idx，中途被打断就是这个形状。
        """
        ckpt = _write_ckpt(self.tmp, self.plan, [_chunk(i) for i in range(10)], 6)

        chunks, resume = _load_qwen_chunk_ckpt(ckpt, self.plan)

        self.assertEqual(resume, 6)
        self.assertEqual(len(chunks), 6,
                         "超出账本的块没有经过完成确认，不能当成已完成")

    def test_honest_ledger_is_untouched(self):
        """账本和货对得上时，一个字节都不许动。"""
        ckpt = _write_ckpt(self.tmp, self.plan, [_chunk(i) for i in range(10)], 10)

        chunks, resume = _load_qwen_chunk_ckpt(ckpt, self.plan)

        self.assertEqual((len(chunks), resume), (10, 10))
        self.assertTrue(chunks_are_complete(resume, chunks, self.plan))


class TestSeamMergeHandlesCJKPunctuation(unittest.TestCase):
    """外审④：接缝比对只去英文标点，中文日文句号去不掉。

    `_seam_norm` 的 `strip(" ,.!?\\"'")` 里没有 `。`、`、`、`，`、`！`、`？`。
    于是正式词流里的 `好。` 和原始词流里的 `好` 对不上，重复词删不掉。

    **⑩ 修的是同一个 bug 的英文那一半。** 挂标点会改写文字这件事想到了，
    但补的标点集合只有英文——**而这个项目的主力语言是中文和日文**
    （`QWEN_PRIMARY_LANGS` 里中日韩三种全在）。
    """

    @staticmethod
    def _pair(t_start, t_end, words, raw_text):
        es = max(0.0, t_start - 5.0)
        return {"t_start": t_start, "t_end": t_end, "raw_text": raw_text,
                "raw_segments": [{"text": t, "start": a - es, "end": b - es}
                                 for (t, a, b) in words]}

    def _run(self, lang, left, right, dup):
        out = _refresh_chunk_delivery([left, right], lang)
        texts = ([s["text"] for s in out[0]["segments"]]
                 + [s["text"] for s in out[1]["segments"]])
        return sum(1 for t in texts if t.strip(" ,.!?。、，！？「」") == dup), texts

    def test_chinese_sentence_final_duplicate_is_removed(self):
        left = self._pair(300.0, 600.0,
                          [("这", 598.6, 598.9), ("个", 598.9, 599.2),
                           ("很", 599.5, 599.7), ("好", 599.9, 600.3)], "这个很好。")
        right = self._pair(600.0, 900.0,
                           [("很", 599.5, 599.8), ("好", 599.95, 600.35),
                            ("然", 600.5, 600.8), ("后", 600.8, 601.0)], "很好。然后")
        n, texts = self._run("zh", left, right, "好")
        self.assertEqual(n, 1, f"中文句末重复词必须删掉一个，实际：{' '.join(texts)}")

    def test_japanese_sentence_final_duplicate_is_removed(self):
        left = self._pair(300.0, 600.0,
                          [("これ", 598.6, 599.0), ("は", 599.0, 599.3),
                           ("いい", 599.5, 600.3)], "これはいい。")
        right = self._pair(600.0, 900.0,
                           [("は", 599.4, 599.7), ("いい", 599.9, 600.35),
                            ("です", 600.5, 600.9)], "はいい。です")
        n, texts = self._run("ja", left, right, "いい")
        self.assertEqual(n, 1, f"日文句末重复词必须删掉一个，实际：{' '.join(texts)}")

    def test_english_still_works(self):
        """回归：补中日文标点不能把英文弄坏。"""
        left = self._pair(300.0, 600.0,
                          [("that", 598.6, 599.0), ("is", 599.0, 599.3),
                           ("nice", 599.9, 600.3)], "that is nice.")
        right = self._pair(600.0, 900.0,
                           [("is", 599.4, 599.7), ("nice", 599.95, 600.35),
                            ("yeah", 600.5, 600.9)], "is nice. yeah")
        n, texts = self._run("en", left, right, "nice")
        self.assertEqual(n, 1, f"英文不能被改坏，实际：{' '.join(texts)}")

    def test_norm_strips_cjk_but_keeps_word_internal_chars(self):
        """句末标点要去掉；**词内的字一个都不许动**。"""
        self.assertEqual(_seam_norm("好。"), _seam_norm("好"))
        self.assertEqual(_seam_norm("はい。"), _seam_norm("はい"))
        self.assertEqual(_seam_norm("对，"), _seam_norm("对"))
        self.assertNotEqual(_seam_norm("we're"), _seam_norm("were"),
                            "词内标点是真实内容，去掉会把两个不同的词拉成同一个")
        self.assertNotEqual(_seam_norm("好"), _seam_norm("坏"))


if __name__ == "__main__":
    unittest.main()


class TestProbeHappensAfterDownload(unittest.TestCase):
    """外审⑥：URL 任务在**下载之前**探测媒体流，那时文件还不存在。

    真实复现：
        probe_media_streams('')                          → {None, None}
        resolve_delivery_flags({'outputMode':'burn'}, '') → ('burn', True, False)

    探测失败按设计返回「不知道」并保持用户选择——**这是对的**，
    擅自降级会把正常视频的烧录悄悄取消掉。但它意味着
    **一个纯音频的 URL 仍然会走烧录，仍然会在 ffmpeg 那步炸**。
    P462 只修好了本地文件那一半。

    ⚠️ **证据等级**：下面第一条是 AST 结构检查，**不是行为验证**。
    它守的是「谁先谁后」，而顺序正是这个 bug 的全部内容。
    行为证据在 `test_real_mp3_*`（真 ffprobe、真 mp3 文件，不 mock）。
    """

    @staticmethod
    def _stmt_order(func_name: str, callees: tuple):
        """返回 callees 在指定函数体里首次出现的语句序号。"""
        import ast as _ast
        tree = _ast.parse(
            (Path(__file__).resolve().parent.parent / "backend" / "worker.py")
            .read_text(encoding="utf-8"))
        fn = next(n for n in _ast.walk(tree)
                  if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                  and n.name == func_name)
        found = {}
        for node in _ast.walk(fn):
            if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name) \
                    and node.func.id in callees and node.func.id not in found:
                found[node.func.id] = node.lineno
        return found

    def test_delivery_flags_resolved_after_download(self):
        order = self._stmt_order(
            "main", ("resolve_delivery_flags", "_download_source_if_url"))
        self.assertIn("resolve_delivery_flags", order, "主流程 main() 必须调用收敛函数")
        self.assertIn("_download_source_if_url", order)
        self.assertGreater(
            order["resolve_delivery_flags"], order["_download_source_if_url"],
            "探测必须发生在下载之后——URL 任务下载前 video_path 是空的，"
            f"探测等于没做（实测返回 ('burn', True, False)）。"
            f"实际行号：探测 {order['resolve_delivery_flags']}、"
            f"下载 {order['_download_source_if_url']}")

    def test_real_mp3_collapses_to_subtitle_only(self):
        """行为证据：**真 ffprobe、真 mp3 文件，不 mock**。"""
        import subprocess, tempfile
        from backend.core.extractor import probe_media_streams
        from backend.worker import resolve_delivery_flags

        with tempfile.TemporaryDirectory() as td:
            mp3 = Path(td) / "real.mp3"
            r = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:a", "libmp3lame", str(mp3)],
                capture_output=True, text=True)
            if r.returncode != 0 or not mp3.exists():
                self.skipTest(f"ffmpeg 不可用: {r.stderr[:120]}")

            self.assertEqual(probe_media_streams(str(mp3)),
                             {"has_audio": True, "has_video": False})
            for cfg in ({"outputMode": "burn"}, {"outputMode": "mux"},
                        {"burn": True}):
                mode, burn, mux = resolve_delivery_flags(cfg, str(mp3))
                self.assertEqual((mode, burn, mux), ("srt", False, False),
                                 f"{cfg} 拖进一个真 mp3，必须只出字幕文件")


# ── 前端：黄条什么时候出现、会不会被别人清掉 ──────────────────────
import json as _json
import re as _re
import subprocess as _sp

_HTML = (Path(__file__).resolve().parent.parent
         / "frontend" / "index.html").read_text(encoding="utf-8")


def _js_const(name: str) -> str:
    """抽出 `const NAME=[...];` 那一行——fileKind 依赖这几张清单。"""
    m = _re.search(rf"const\s+{_re.escape(name)}\s*=.*?;", _HTML, _re.S)
    assert m, f"找不到常量 {name}"
    return m.group(0)


def _js_function(name: str) -> str:
    m = _re.search(
        rf"(?:async\s+)?function\s+{_re.escape(name)}\([^)]*\)\{{.*?\n\}}",
        _HTML, _re.S)
    assert m, f"找不到 JS 函数 {name}"
    return m.group(0)


def _run_shortfall(calls: list) -> dict:
    """在 node 里**真跑** showTranscriptionShortfall，返回提示栏最终状态。

    `calls` 是 [(msg, keepIfClean), ...]，模拟连续处理多个任务。
    """
    source = f"""
const bar = {{textContent:'', className:'', _cls:new Set(),
  classList:{{
    remove(...xs){{ for(const x of xs) bar._cls.delete(x); }},
    add(...xs){{ for(const x of xs) bar._cls.add(x); }}
  }}}};
Object.defineProperty(bar, 'className', {{
  get(){{ return [...bar._cls].join(' '); }},
  set(v){{ bar._cls = new Set(String(v).split(/\\s+/).filter(Boolean)); }}
}});
globalThis.$ = (id) => (id === 'shortfallWarn' ? bar : null);
{_js_function('showTranscriptionShortfall')}
for (const [msg, keep] of {_json.dumps(calls)}) showTranscriptionShortfall(msg, keep);
console.log(JSON.stringify({{text: bar.textContent, cls: bar.className}}));
"""
    r = _sp.run(["node", "-e", source], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout.strip().splitlines()[-1])


_SHORTFALL = {"transcription_shortfall": {
    "severity": "error",
    "message": "语音识别未完成：已处理到 30:00，未生成字幕。模型加载失败，请完成运行环境准备后重试。",
    "completed_until": 1800.0}}
_CLEAN = {"transcription_shortfall": None}


class TestShortfallBannerIsWiredAndSurvives(unittest.TestCase):
    """外审③：黄条**跑完那一刻不出现**，而且**会被后一个任务清掉**。

    两个独立的毛病：

    1. 实时完成路径 `applyTaskState` 的 `done` 分支只叫了 `showQmWarning`，
       没叫 `showTranscriptionShortfall`。用户坐着等任务跑完 → 什么都看不到；
       **刷新一下页面才看得到**。
       这重复了本项目第一批那个坑——「消息写了，但用户完成后看不见」。
       P464 的文档里还专门写了那条教训，然后在同一批里用另一种方式又栽一次。

    2. 页面只有一个全局提示栏。恢复多个任务时逐个调用，
       后一个没有欠账的正常任务会执行 `classList.remove('active',...)`，
       **把前一个任务的警告擦掉**。
       旁边的 `showQmWarning` 早就有 `keepIfClean` 参数处理这件事，
       `showTranscriptionShortfall` 抄结构的时候没抄这个参数。
    """

    def test_warning_shows_for_a_failed_job(self):
        out = _run_shortfall([[_SHORTFALL, False]])
        self.assertIn("识别未完成", out["text"])
        self.assertIn("active", out["cls"])
        self.assertIn("red", out["cls"], "severity=error 必须是红色")

    def test_clean_job_must_not_erase_previous_warning(self):
        """先恢复一个有欠账的任务，再恢复一个正常任务——警告必须还在。"""
        out = _run_shortfall([[_SHORTFALL, True], [_CLEAN, True]])
        self.assertIn("识别未完成", out["text"],
                      "后一个正常任务把前一个任务的警告擦掉了——"
                      "全局只有一条提示栏，恢复多任务时必须像 showQmWarning 那样保留")
        self.assertIn("active", out["cls"])

    def test_clean_job_still_clears_when_not_restoring(self):
        """回归：不是恢复场景时，正常任务照旧清掉旧提示。"""
        out = _run_shortfall([[_SHORTFALL, False], [_CLEAN, False]])
        self.assertNotIn("active", out["cls"])

    def test_live_done_path_calls_the_banner(self):
        """⚠️ **结构检查**（不是行为验证）：实时完成分支里必须出现这次调用。

        行为证据在 ⑨ 界面 QA——真跑一个会欠账的任务，看黄条在
        **跑完的那一刻**出现，而不是刷新之后。
        """
        fn = _js_function("applyTaskState")
        done_block = fn.split("if(status==='done'){")[1].split("if(status===")[0]
        self.assertIn("showTranscriptionShortfall", done_block,
                      "实时跑完的那条路没叫黄条——用户只有刷新页面才看得见，"
                      "这正是第一批栽过的那个坑")

    def test_live_error_path_calls_the_banner(self):
        """红色失败卡同理：`severity=error` 的欠账说明就是靠它显示的。"""
        fn = _js_function("applyTaskState")
        err_block = fn.split("if(status==='error'){")[1].split("if(status===")[0]
        self.assertIn("showTranscriptionShortfall", err_block,
                      "没有完整识别结果时要报红并说明处理到哪里，"
                      "这条路不叫黄条，用户就只会看到一句通用失败")


class TestFailureReasonSurvivesTheEmptyGate(unittest.TestCase):
    """外审②：失败原因造出来了，**但在装进最终结果之前就被异常打断了**。

    真实路径：模型加载失败 → 半部结果按合同丢弃 → `results` 为空
    → `_finalize_segments` 立刻 `raise RuntimeError("未检测到任何语音内容")`
    → worker 的 `except Exception` 只拿到这句话。

    于是用户等了半小时，看到的是「视频可能为纯音乐或无声」——
    **又一句谎话，只是跟修之前那句不一样。**

    而 P464 的文档里写着：

        系统是知道原因的——它知道模型加载失败、知道只处理到哪一秒，
        它就是不说。

    修完之后它还是不说。`transcription_shortfall` 只在**转录成功返回**时
    才经由收据流到 worker；抛异常这条路一个字都传不出来。
    """

    def test_empty_result_with_known_reason_says_the_reason(self):
        from backend.transcriber_v2 import _finalize_segments

        shortfall = {
            "severity": "error",
            "message": "语音识别未完成：已处理到 30:00，未生成字幕。"
                       "模型加载失败，请完成运行环境准备后重试。",
            "completed_until": 1800.0,
            "reason": "模型加载失败",
        }
        with self.assertRaises(RuntimeError) as ctx:
            _finalize_segments([], False, "/tmp/a.wav", shortfall)

        self.assertIn("30:00", str(ctx.exception),
                      "已经知道处理到哪一秒了，不能再说「可能为纯音乐或无声」")
        self.assertEqual(getattr(ctx.exception, "shortfall", None), shortfall,
                         "异常必须**带着**结构化说明，否则刷新之后又没了——"
                         "第一批那条警告就是这样丢的")

    def test_empty_result_without_reason_keeps_the_old_message(self):
        """回归：真的是无声视频时，原来那句话是对的，不许改。"""
        from backend.transcriber_v2 import _finalize_segments

        with self.assertRaises(RuntimeError) as ctx:
            _finalize_segments([], False, "/tmp/a.wav", None)
        self.assertIn("纯音乐或无声", str(ctx.exception))
        self.assertIsNone(getattr(ctx.exception, "shortfall", None))

    def test_shortfall_argument_is_mandatory(self):
        """**结构性防护**：少传这个参数就 TypeError，不会静默走回老路。

        这批栽的五个坑全是「写了没接上」。让它接不上就崩，
        比写一句「记得传」有用。
        """
        from backend.transcriber_v2 import _finalize_segments
        import inspect

        params = inspect.signature(_finalize_segments).parameters
        self.assertIn("shortfall", params)
        self.assertIs(params["shortfall"].default, inspect.Parameter.empty,
                      "必填。给了默认值就等于允许「忘了传」，那条路会静默退回老消息")


class TestWorkerCarriesShortfallOnCrash(unittest.TestCase):
    """外审②下半段：worker 的异常出口要把结构化说明带给前端。

    `except Exception` 里组装的 `error_fields` 原来只有翻译摘要和声音字段，
    **没有 `transcription_shortfall`**。红色失败卡因此拿不到
    「处理到 30:00、模型加载失败」，刷新页面也重建不出来。
    """

    def test_reason_from_exception_wins(self):
        from backend.worker import _error_shortfall

        sf = {"severity": "error", "message": "语音识别未完成：已处理到 30:00。"}
        exc = RuntimeError("boom")
        exc.shortfall = sf
        self.assertEqual(_error_shortfall(exc, None), sf)

    def test_falls_back_to_what_the_run_already_knew(self):
        """转录成功、后面某步炸了——之前算出来的欠账说明仍要带上。"""
        from backend.worker import _error_shortfall

        known = {"severity": "info", "message": "识别增强未完成，已使用完整的语音识别结果。"}
        self.assertEqual(_error_shortfall(RuntimeError("boom"), known), known)

    def test_nothing_known_stays_none(self):
        from backend.worker import _error_shortfall
        self.assertIsNone(_error_shortfall(RuntimeError("boom"), None))

    def test_error_exit_actually_attaches_it(self):
        """⚠️ 结构检查：异常出口必须真的调用它，否则又是一个悬空插头。"""
        import ast as _ast
        src = (Path(__file__).resolve().parent.parent
               / "backend" / "worker.py").read_text(encoding="utf-8")
        fn = next(n for n in _ast.walk(_ast.parse(src))
                  if isinstance(n, _ast.FunctionDef) and n.name == "main")
        called = {n.func.id for n in _ast.walk(fn)
                  if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
        self.assertIn("_error_shortfall", called,
                      "main() 的异常出口没叫它——函数写了没人用，本批第六个悬空插头")


class TestErrorHandlerCannotCrashItself(unittest.TestCase):
    """返工过程中**自己差点造出来的**一个更严重的 bug，锁死它。

    我给 `except Exception` 加了一行 `_error_shortfall(e, _transcription_shortfall)`。
    但 `_transcription_shortfall` 直到转录成功那一步（`main()` 中段）才赋值——
    **异常如果发生在那之前**（下载失败、音频提取失败、模型加载失败……），
    这一行会 `UnboundLocalError`，**异常处理器自己崩掉**，
    用户连那句笼统的失败提示都看不到，worker 直接死。

    旁边的 `translation_summary` / `sound_reference_summary` 早就在 `try` 之前
    初始化成 `None`，**就是为了这个**。抄接线的时候没抄这条规矩。

    原 bug 是「少给用户一条信息」，我这个是「把报错本身弄没了」——**更重**。
    """

    @staticmethod
    def _first_assign_and_try(name: str):
        """返回 (变量首次赋值行, **异常出口所在的那个 try** 的行号)。

        `main()` 里有三个顶层 `try`，只有包住主流程的那一个才有
        `except Exception` 出口。拿最早的那个比会得到假红——
        所以按「哪个 try 的 handler 里调用了 `_error_shortfall`」来认。
        """
        import ast as _ast
        src = (Path(__file__).resolve().parent.parent
               / "backend" / "worker.py").read_text(encoding="utf-8")
        fn = next(n for n in _ast.walk(_ast.parse(src))
                  if isinstance(n, _ast.FunctionDef) and n.name == "main")
        assigns = [n.lineno for n in _ast.walk(fn)
                   if isinstance(n, _ast.Assign)
                   for t in n.targets
                   if isinstance(t, _ast.Name) and t.id == name]
        guarded = [
            t.lineno for t in fn.body if isinstance(t, _ast.Try)
            for h in t.handlers
            for c in _ast.walk(h)
            if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)
            and c.func.id == "_error_shortfall"
        ]
        return (min(assigns) if assigns else None,
                min(guarded) if guarded else None)

    def test_shortfall_var_initialized_before_the_try_block(self):
        first, try_ln = self._first_assign_and_try("_transcription_shortfall")
        self.assertIsNotNone(first, "main() 里没有这个变量了？改名要同步改本测试")
        self.assertIsNotNone(try_ln, "找不到调用 _error_shortfall 的异常出口——它被删了？")
        self.assertLess(
            first, try_ln,
            f"必须在 try 之前初始化（实际首次赋值 {first} 行、try 在 {try_ln} 行）。"
            "否则早期异常会让 except 里的引用 UnboundLocalError，"
            "报错处理器自己崩掉——比原 bug 严重")

    def test_same_rule_holds_for_its_neighbours(self):
        """对照：旁边两个字段早就守着这条规矩，别把它们改坏。"""
        for name in ("translation_summary", "sound_reference_summary"):
            first, try_ln = self._first_assign_and_try(name)
            self.assertLess(first, try_ln, f"{name} 必须在 try 之前初始化")


class TestNoAudioFailsBeforeExtracting(unittest.TestCase):
    """外审第二轮①：**「有画面但没有声音」的视频，程序知道，然后还是撞上去。**

    真实复现（自造无音轨 MP4，走完整 worker）：

    ```
    probe_media_streams  →  {'has_audio': False, 'has_video': True}   ← 知道了
    resolve_delivery_mode →  只用 has_video，把 has_audio 扔了
    _step_extract_audio  →  又跑一次 ffprobe，emit "no_audio"          ← 又知道了一次
    然后还是调 extract_audio  →  ffmpeg 炸
    用户看到：处理过程中断。错误详情：音频提取失败: ...Error opening output file...
    ```

    **P462 的合同是这么写的**：

        有没有声音？ → **决定这活能不能干**（没声音 = 什么都做不了）
        有没有画面？ → 决定成品长什么样（没画面 = 烧不了录）
        一次 ffprobe 同时给出两个答案，成本不变。

    **第一句话，一行代码都没有。** `has_audio` 探测出来之后全项目零消费点。
    这正是真实事故 `9110c09770` 的路径，所以不是额外加固，是 P462 本体还差最后一步。

    **拦在 `resolve_delivery_flags` 里**：它已经是主流程绕不过去的打包出口
    （绕过它就拿不到 flag），而且它在抽音频之前。在这里拦 = 第 1 层防护，
    不需要再多一个「记得调用」的新函数——这批栽过的六个坑全是那种。
    """

    @staticmethod
    def _flags(streams, cfg=None):
        from backend import worker
        with mock.patch.object(worker, "probe_media_streams", lambda _p: streams):
            return worker.resolve_delivery_flags(cfg or {"outputMode": "burn"},
                                                 "/tmp/x.mp4")

    def test_video_without_audio_fails_with_a_human_sentence(self):
        """有画面、没声音 → 当场失败，而且说人话。"""
        with self.assertRaises(Exception) as ctx:
            self._flags({"has_audio": False, "has_video": True})
        msg = str(ctx.exception)
        self.assertIn("没有声音", msg,
                      f"要告诉用户这个视频没有声音，而不是甩一句 ffmpeg 的话。实际：{msg}")
        self.assertNotIn("ffmpeg", msg.lower())
        self.assertNotIn("Error opening output file", msg)

    def test_audio_only_file_still_works(self):
        """mp3 有声音没画面——这活干得了，只是只出字幕。不许拦。"""
        mode, burn, mux = self._flags({"has_audio": True, "has_video": False})
        self.assertEqual((mode, burn, mux), ("srt", False, False))

    def test_normal_video_untouched(self):
        mode, burn, _ = self._flags({"has_audio": True, "has_video": True})
        self.assertEqual((mode, burn), ("burn", True))

    def test_unknown_probe_does_not_block(self):
        """探测不出来是「不知道」，不是「没有」。

        擅自拦下会把一个正常视频挡在门外——比它要防的那个 bug 更常见。
        """
        mode, burn, _ = self._flags({"has_audio": None, "has_video": None})
        self.assertEqual((mode, burn), ("burn", True))

    def test_the_sentence_survives_friendly_error(self):
        """**这条才是真能抓 bug 的**：说人话的消息不能被错误翻译器改回技术腔。

        `_friendly_error` 用子串匹配（`"403" in raw` 之类），
        一句正常的中文消息很容易被它误伤成「API Key 验证失败」。
        """
        from backend import worker
        with mock.patch.object(worker, "probe_media_streams",
                               lambda _p: {"has_audio": False, "has_video": True}):
            try:
                worker.resolve_delivery_flags({"outputMode": "burn"}, "/tmp/x.mp4")
                self.fail("应该抛异常")
            except Exception as exc:
                friendly = worker._friendly_error(str(exc))
        self.assertIn("没有声音", friendly,
                      f"错误翻译器把它改坏了：{friendly}")

    def test_it_blocks_before_audio_extraction(self):
        """⚠️ 结构检查：拦截必须发生在抽音频**之前**，否则等于没拦。"""
        import ast as _ast
        src = (Path(__file__).resolve().parent.parent
               / "backend" / "worker.py").read_text(encoding="utf-8")
        fn = next(n for n in _ast.walk(_ast.parse(src))
                  if isinstance(n, _ast.FunctionDef) and n.name == "main")
        pos = {}
        for n in _ast.walk(fn):
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) \
                    and n.func.id in ("resolve_delivery_flags", "_step_extract_audio") \
                    and n.func.id not in pos:
                pos[n.func.id] = n.lineno
        self.assertLess(pos["resolve_delivery_flags"], pos["_step_extract_audio"],
                        "拦在抽音频之后 = ffmpeg 已经炸过一次了，拦了也白拦")


class TestFlagsResolutionIsInsideTheErrorHandler(unittest.TestCase):
    """**同一个形状的错误，我在同一批里犯了第二次**，锁死它。

    第一次：`_error_shortfall(e, _transcription_shortfall)` 引用了一个
    转录成功才赋值的变量，早期异常会让 `except` 块自己 `UnboundLocalError`。

    第二次（本次）：给 `resolve_delivery_flags` 加了「没声音就抛异常」之后，
    真跑无音轨 MP4 的结果是 **退出码 1、用户屏幕上什么都没有**——
    因为那个调用落在 `main()` 的顶层 `try` **之外**，异常直接掀翻进程，
    一条终态消息都发不出去。**比它要修的那个 bug 更严重**：
    原来至少还有一句难懂的错误，现在是一片空白。

    两次都是同一个疏忽：**加了会抛异常的代码，没检查它在不在异常保护范围内。**

    本测试同时守三件事：下载 → 探测 → 抽音频 的顺序，以及探测在保护范围内。
    """

    @staticmethod
    def _positions():
        import ast as _ast
        src = (Path(__file__).resolve().parent.parent
               / "backend" / "worker.py").read_text(encoding="utf-8")
        fn = next(n for n in _ast.walk(_ast.parse(src))
                  if isinstance(n, _ast.FunctionDef) and n.name == "main")
        want = ("_download_source_if_url", "resolve_delivery_flags",
                "_step_extract_audio")
        pos = {}
        for n in _ast.walk(fn):
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) \
                    and n.func.id in want and n.func.id not in pos:
                pos[n.func.id] = n.lineno
        guarded = [t.lineno for t in fn.body if isinstance(t, _ast.Try)
                   for h in t.handlers for c in _ast.walk(h)
                   if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)
                   and c.func.id == "_error_shortfall"]
        return pos, min(guarded)

    def test_probe_is_inside_the_guarded_try(self):
        pos, guard = self._positions()
        self.assertGreater(
            pos["resolve_delivery_flags"], guard,
            f"它会抛 MediaHasNoAudioError，必须落在带 except 的 try 里面"
            f"（try 在 {guard} 行，调用在 {pos['resolve_delivery_flags']} 行）。"
            "在外面 = 异常掀翻进程 = 用户屏幕上一片空白，比原 bug 更严重")

    def test_order_download_probe_extract(self):
        pos, _ = self._positions()
        self.assertLess(pos["_download_source_if_url"], pos["resolve_delivery_flags"],
                        "URL 任务必须先下载完再探测，否则探的是一个空路径")
        self.assertLess(pos["resolve_delivery_flags"], pos["_step_extract_audio"],
                        "没声音要在抽音频之前拦下，拦在后面等于 ffmpeg 已经炸过了")


def _run_quality_summary(msg: dict) -> str:
    """在 node 里**真跑** renderTranslationSummary，返回任务卡质量区的文字。"""
    box = {"text": "", "cls": ""}
    source = f"""
const q = {{textContent:'', _cls:new Set(),
  classList:{{add(...x){{x.forEach(c=>q._cls.add(c))}},
             remove(...x){{x.forEach(c=>q._cls.delete(c))}}}}}};
globalThis.$ = (id) => (id === 'quality-J' ? q : null);
{_js_function('renderTranslationSummary')}
renderTranslationSummary('J', {_json.dumps(msg)});
console.log(JSON.stringify({{text:q.textContent, cls:[...q._cls].join(' ')}}));
"""
    r = _sp.run(["node", "-e", source], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout.strip().splitlines()[-1])


class TestShortfallLivesInTheTaskCard(unittest.TestCase):
    """外审第二轮②：提示只在**全局**提示栏，不在对应任务卡里。

    后果：后面再跑一个正常任务，可能把前一个任务的提示清掉；
    而且任务卡自己的「最终质量摘要」里没有这条最重要的信息。

    **任务卡里早就有一块质量摘要区**（`quality-${jid}`，
    由 `renderTranslationSummary` 渲染翻译档位、声音参考、存疑句数、字幕质量四样），
    而 `question_mark_count` 已经是「全局栏醒目提示 + 卡内持久记录」双写的既有模式。
    转录欠账按同一个模式补进去即可，不需要新造结构。
    """

    _SHORTFALL = {"severity": "error", "completed_until": 1200.0,
                  "message": "语音识别未完成：已处理到 20:00，未生成字幕。"
                             "语音识别模型加载失败，请完成运行环境准备后重试。"}

    _SOUND = {"status": "unavailable", "message": "声音参考未使用，字幕仍已生成。"}

    def test_shortfall_appears_in_the_card_summary(self):
        out = _run_quality_summary({
            "transcription_shortfall": self._SHORTFALL,
            "question_mark_count": 0, "segment_count": 0})
        self.assertIn("已处理到 20:00", out["text"],
                      f"任务卡的质量摘要里必须有这条——它是用户唯一的行动线索。"
                      f"实际：{out['text']!r}")
        self.assertIn("show", out["cls"], "摘要区必须显示出来")

    def test_shortfall_comes_first(self):
        """它是用户唯一的行动线索，排在最前。（D-3：存疑句数已不显示，换声音参考那一行对照。）"""
        out = _run_quality_summary({
            "transcription_shortfall": self._SHORTFALL,
            "sound_reference": self._SOUND})
        text = out["text"]
        self.assertLess(text.index("已处理到 20:00"), text.index("声音参考未使用"))

    def test_no_shortfall_changes_nothing(self):
        """回归：没有欠账时，原来那几样一个字都不许变。（D-3：存疑句数已不显示。）"""
        out = _run_quality_summary({
            "transcription_shortfall": None, "sound_reference": self._SOUND})
        self.assertEqual(out["text"], "声音参考未使用，字幕仍已生成。")


class TestFailedJobWithShortfallSurvivesRefresh(unittest.TestCase):
    """外审第二轮②下半段：**刷新页面，任务和提示一起消失。**

    真实操作是这样的：
        等半小时 → 看见「只处理到 20:00，模型加载失败」→ 刷新页面 → 什么都没了。

    两头都把它扔了：
      · 前端 `applyTaskState` 的 error 分支：没有成品、没有可恢复动作 →
        `removeActiveJobId(jid)`，本地记录删掉；
      · 后端 `_is_recoverable_active_job`：`status == "error"` 时
        `return bool(owned["result_srt"])`——**没有字幕的失败任务直接排除**。

    于是刷新时 `restoreActiveJobsAtStartup` 两头都捞不到。

    **⚠️ 我上一轮验错了入口**：当时用 `GET /api/jobs/{id}` 按 ID 查，那条路确实能重建，
    并据此写下「刷新后仍在 ✅」。但用户刷新页面走的是 `/api/active-jobs` **列表**，
    根本不知道 ID。**验了一条用户不走的路。**

    修的是「该不该显示」，不是「能不能续跑」：这类任务确实没什么可续的
    （要先装好模型再重来），但那句话是用户唯一的行动线索，不能跟着卡片一起消失。
    """

    def _active_ids(self, meta: dict):
        import tempfile
        from fastapi.testclient import TestClient
        from backend import main

        tmp = Path(tempfile.mkdtemp())
        jid = "failjob001"
        (tmp / jid).mkdir()
        (tmp / jid / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
        try:
            with mock.patch.object(main, "JOBS_DIR", tmp), \
                 mock.patch.dict(main.JOBS, {}, clear=True), \
                 mock.patch.dict(main.GLOBAL_HASH_INDEX, {}, clear=True), \
                 mock.patch.dict(main.JOB_ID_TO_HASH, {}, clear=True):
                main._scan_jobs_into_index()
                r = TestClient(main.app).get("/api/active-jobs")
                jobs = r.json().get("jobs", [])
                return [j["state"]["job_id"] for j in jobs if j.get("state")], jobs
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    _SHORTFALL = {"severity": "error", "completed_until": 1200.0,
                  "message": "语音识别未完成：已处理到 20:00，未生成字幕。"
                             "语音识别模型加载失败，请完成运行环境准备后重试。"}

    def test_failed_job_with_shortfall_is_listed_after_refresh(self):
        ids, jobs = self._active_ids({
            "status": "error", "file_hash": "h1", "config": {},
            "run_id": "r1", "transcription_shortfall": self._SHORTFALL})
        self.assertIn("failjob001", ids,
                      "带欠账说明的失败任务必须能被刷新后重新发现——"
                      "否则用户等了半小时看到的那句话，刷一下就没了")

    def test_the_sentence_comes_back_with_it(self):
        """光把任务捞回来不够，那句话得跟着回来。"""
        ids, jobs = self._active_ids({
            "status": "error", "file_hash": "h1", "config": {},
            "run_id": "r1", "transcription_shortfall": self._SHORTFALL})
        self.assertTrue(jobs, "任务没被捞回来，谈不上文案")
        quality = jobs[0]["state"]["result"]["quality"]
        self.assertEqual(quality.get("transcription_shortfall"), self._SHORTFALL,
                         "任务回来了但说明没回来，卡片上还是一片空白")

    def test_plain_failure_without_shortfall_stays_out(self):
        """回归：没有欠账说明的普通失败，行为一个字不许变。

        它没有任何可展示的线索，留在列表里只是噪音。
        """
        ids, _ = self._active_ids({
            "status": "error", "file_hash": "h2", "config": {}, "run_id": "r2"})
        self.assertEqual(ids, [], "不该把所有失败任务都留下来")

    def test_restore_path_shows_the_banner_too(self):
        """⚠️ 结构检查：刷新恢复的 error 卡片也要点亮全局提示栏。

        卡内摘要由 `renderCardError → renderTranslationSummary` 负责（已通），
        全局栏是「醒目」那一半——done 恢复分支早就调了，error 分支漏了。
        行为证据在 ⑨ 界面 QA。
        """
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        branch = html.split("else if(state.status==='error')")[1].split("else if(")[0]
        self.assertIn("showTranscriptionShortfall", branch,
                      "刷新恢复失败任务时没点亮提示栏——和 done 分支不一致")


class TestAudioSourceIsTheOneActuallyUsed(unittest.TestCase):
    """外审第三轮①（**我上一轮返工造的回归，P1**）：
    **正常的 YouTube 分轨任务被判成「没有声音」。**

    真实任务 `1a1b9f8145`——它**已经成功生成过字幕和成品**——跑新代码的结果：
    ```
    探测 _video.mp4  →  {'has_audio': False, 'has_video': True}
    resolve_delivery_flags → ❌ MediaHasNoAudioError「这个视频没有声音轨道」
    ```

    YouTube 分轨下载：画面在 `_video.mp4`，声音在 `_audio.m4a`。
    **下载模块知道**（`worker.py:1767` 写 `config["dl_audio_path"]`），
    **抽音频模块知道**（`:1843` 用 `dl_audio_path or video_path`），
    **中间我新加的那道检查不知道**——它只看 `video_path`。

    **为了拦住一个罕见的坏输入，拦死了一类最常见的好输入。**
    这正是 CLAUDE.md「防护优先于补救」第三问要防的：
    引入的新病比旧病更常见还是更罕见？——无音轨视频罕见，YouTube 分轨是日常。

    **修法（沿用现有结构，不加新机制）**：
    声音看**实际会被抽音频的那个源**（和 `_step_extract_audio` 同一个规则），
    画面看**视频源**。两者是同一个文件时只探一次——合同「一次 ffprobe」的精神保住。
    """

    @staticmethod
    def _flags(cfg, streams_by_path):
        from backend import worker
        seen = []

        def _probe(path):
            seen.append(path)
            return streams_by_path[path]

        with mock.patch.object(worker, "probe_media_streams", _probe):
            try:
                return worker.resolve_delivery_flags(cfg, cfg["video_path"]), seen, None
            except Exception as exc:
                return None, seen, exc

    # ── 形态 4：URL 分轨（我上一轮挡死的就是它）──────────────────
    def test_url_split_download_is_not_blocked(self):
        cfg = {"outputMode": "burn", "video_path": "/j/v_video.mp4",
               "dl_audio_path": "/j/v_audio.m4a"}
        flags, seen, exc = self._flags(cfg, {
            "/j/v_video.mp4": {"has_audio": False, "has_video": True},
            "/j/v_audio.m4a": {"has_audio": True, "has_video": False}})
        self.assertIsNone(exc, f"分轨下载的正常视频被挡死了：{exc}")
        self.assertEqual(flags, ("burn", True, False),
                         "画面在 mp4 里，用户选的烧录必须保住")

    def test_url_split_download_with_truly_silent_audio_still_blocked(self):
        """独立音频文件本身没声音——那才是真的干不了。"""
        cfg = {"outputMode": "burn", "video_path": "/j/v_video.mp4",
               "dl_audio_path": "/j/v_audio.m4a"}
        _, _, exc = self._flags(cfg, {
            "/j/v_video.mp4": {"has_audio": False, "has_video": True},
            "/j/v_audio.m4a": {"has_audio": False, "has_video": False}})
        self.assertIsNotNone(exc)
        self.assertIn("没有声音", str(exc))

    # ── 形态 1/2/3：本地文件三种 ────────────────────────────
    def test_local_video_with_sound_untouched(self):
        cfg = {"outputMode": "burn", "video_path": "/j/m.mp4"}
        flags, seen, exc = self._flags(
            cfg, {"/j/m.mp4": {"has_audio": True, "has_video": True}})
        self.assertIsNone(exc)
        self.assertEqual(flags, ("burn", True, False))
        self.assertEqual(len(seen), 1,
                         "同一个文件不许探两次——合同写着「一次 ffprobe」")

    def test_local_mp3_still_collapses_to_srt(self):
        cfg = {"outputMode": "burn", "video_path": "/j/a.mp3"}
        flags, _, exc = self._flags(
            cfg, {"/j/a.mp3": {"has_audio": True, "has_video": False}})
        self.assertIsNone(exc)
        self.assertEqual(flags, ("srt", False, False))

    def test_local_video_without_audio_still_blocked(self):
        cfg = {"outputMode": "burn", "video_path": "/j/silent.mp4"}
        _, _, exc = self._flags(
            cfg, {"/j/silent.mp4": {"has_audio": False, "has_video": True}})
        self.assertIsNotNone(exc, "本地无音轨视频仍然要拦")

    # ── 形态 6/7：恢复任务的残留字段、探测失败 ──────────────────
    def test_empty_dl_audio_path_falls_back_to_video(self):
        """`dl_audio_path` 为空字符串/None 时回落到视频本身——
        与 `_step_extract_audio` 的 `config.get(...) or video_path` 逐字一致。"""
        for junk in (None, "", 0):
            cfg = {"outputMode": "burn", "video_path": "/j/m.mp4",
                   "dl_audio_path": junk}
            flags, _, exc = self._flags(
                cfg, {"/j/m.mp4": {"has_audio": True, "has_video": True}})
            self.assertIsNone(exc, f"dl_audio_path={junk!r} 时不该炸")
            self.assertEqual(flags, ("burn", True, False))

    def test_unknown_audio_probe_does_not_block(self):
        """独立音频探测不出来是「不知道」，不是「没有」——不许拦。"""
        cfg = {"outputMode": "burn", "video_path": "/j/v_video.mp4",
               "dl_audio_path": "/j/v_audio.m4a"}
        flags, _, exc = self._flags(cfg, {
            "/j/v_video.mp4": {"has_audio": False, "has_video": True},
            "/j/v_audio.m4a": {"has_audio": None, "has_video": None}})
        self.assertIsNone(exc, "探测不出来就拦，会挡死正常任务")
        self.assertEqual(flags, ("burn", True, False))


class TestRanAndFailedIsNotTheSameAsNeverRan(unittest.TestCase):
    """外审第三轮②：**已经运行过但失败，被当成「压根没运行」，于是一个字都不说。**

    短音频加载模型失败的真实返回：
    ```
    chunks=[]  complete=False  completed_until=None  failure_reason="语音识别模型加载失败"
    ```
    而判据是 `if not chunks and completed_until is None: return None`（当成没跑过）。

    我在那段注释里写着**「两个条件缺一不可」**——两个条件都对，
    **第三个信号 `failure_reason` 就在旁边，没被读**。
    P464 只修好了长视频断点续跑，**短视频首次加载失败这条路没盖到**。

    后果：标准模式下用户可能被误导成「视频是纯音乐或无声」。

    **修法（沿用现有结构，不加字段）**：用已有的 `completed_until` 表达
    「动手了没有」——动过手就是 `0.0`（跑了，完成到 0 秒），没动手才是 `None`。
    判据一个字不改。

    **划分依据**：用户提交任务之后，我们对这个音频动手了吗？
      · 引擎未安装 / 线程没跑 → 没动手 → 正常路由，不提示
      · 连时长都读不出来、模型加载失败、模型没返回结果 → **动手了** → 是故障，要说
    """

    @staticmethod
    def _describe(outcome, whisper_ok):
        from backend.transcriber_v2 import describe_transcription_shortfall
        return describe_transcription_shortfall(outcome, whisper_ok=whisper_ok)

    def _run_short_audio(self, monkey_target, boom):
        """真跑短音频路径（不分块），让指定环节失败，返回 outcome。"""
        from backend.core import qwen_asr_runner as Q
        with mock.patch.object(Q, "is_qwen_asr_available", lambda: True), \
             mock.patch.object(Q, "_resolve_qwen_duration", lambda *a: 25.0), \
             mock.patch.object(Q, monkey_target, boom):
            return Q.run_qwen_asr("/tmp/a.wav", language="en")

    def test_model_load_failure_is_reported(self):
        def _boom():
            raise RuntimeError("模型未就绪")
        outcome = self._run_short_audio("_load_qwen_model", _boom)

        self.assertFalse(outcome.complete)
        self.assertEqual(outcome.failure_reason, "语音识别模型加载失败")
        self.assertIsNotNone(
            self._describe(outcome, whisper_ok=False),
            "跑过、失败了，却一个字都不说——用户会被误导成「视频是纯音乐或无声」")
        info = self._describe(outcome, whisper_ok=True)
        self.assertIsNotNone(info, "主识别完整时也要说一句，只是不报红")
        self.assertEqual(info["severity"], "info")

    def test_engine_not_installed_stays_silent(self):
        """回归：引擎没装是正常路由，**不是故障**，一个字都不许说。"""
        from backend.core import qwen_asr_runner as Q
        with mock.patch.object(Q, "is_qwen_asr_available", lambda: False):
            outcome = Q.run_qwen_asr("/tmp/a.wav", language="en")
        self.assertIsNone(self._describe(outcome, whisper_ok=True))
        self.assertIsNone(self._describe(outcome, whisper_ok=False))

    def test_never_ran_stays_silent(self):
        from backend.core.qwen_asr_runner import QwenAsrOutcome
        self.assertIsNone(
            self._describe(QwenAsrOutcome.not_run(), whisper_ok=True))

    def test_duration_probe_failure_is_reported(self):
        """连时长都读不出来——**动手了**，是故障，不是正常路由。"""
        from backend.core import qwen_asr_runner as Q
        with mock.patch.object(Q, "is_qwen_asr_available", lambda: True), \
             mock.patch.object(Q, "_resolve_qwen_duration", lambda *a: None):
            outcome = Q.run_qwen_asr("/tmp/a.wav", language="en")
        self.assertEqual(outcome.failure_reason, "无法确定音频时长")
        self.assertIsNotNone(self._describe(outcome, whisper_ok=False))

    def test_empty_model_output_is_reported(self):
        """模型跑了但没返回结果——同样是动过手的故障。"""
        from backend.core import qwen_asr_runner as Q
        with mock.patch.object(Q, "is_qwen_asr_available", lambda: True), \
             mock.patch.object(Q, "_resolve_qwen_duration", lambda *a: 25.0), \
             mock.patch.object(Q, "_load_qwen_model", lambda: object()), \
             mock.patch.object(Q, "_qwen_transcribe", lambda *a: None):
            outcome = Q.run_qwen_asr("/tmp/a.wav", language="en")
        self.assertEqual(outcome.failure_reason, "语音识别未返回结果")
        self.assertIsNotNone(self._describe(outcome, whisper_ok=False))

    def test_zero_progress_says_it_never_started(self):
        """一秒都没成时别说「已处理到 00:00」——那句话读起来像个 bug。

        `completed_until=0.0` 是内部用来区分「动手了 / 没动手」的标记，
        **不该原样翻译给用户看**。
        """
        from backend.core.qwen_asr_runner import QwenAsrOutcome
        out = QwenAsrOutcome(chunks=[], complete=False, completed_until=0.0,
                             failure_reason="语音识别模型加载失败")
        msg = self._describe(out, whisper_ok=False)["message"]
        self.assertNotIn("00:00", msg, f"实际：{msg}")
        self.assertIn("语音识别模型加载失败", msg, "原因还是要说")

    def test_long_audio_partial_still_reports_real_position(self):
        """回归：长音频断点的位置信息不许被这次改动冲掉。"""
        from backend.core.qwen_asr_runner import QwenAsrOutcome
        out = QwenAsrOutcome(chunks=[{"text": "x"}], complete=False,
                             completed_until=1200.0,
                             failure_reason="语音识别模型加载失败")
        msg = self._describe(out, whisper_ok=False)
        self.assertIn("20:00", msg["message"], "已处理到哪一秒不能丢")


def _run_dismiss(card_classes: list):
    """在 node 里**真跑** dismissTerminalCard，返回它发出的 DELETE 请求。"""
    source = f"""
const calls = [];
const card = {{ _cls: new Set({_json.dumps(card_classes)}),
  classList: {{ contains: c => card._cls.has(c) }}, remove(){{}} }};
globalThis.$ = (id) => (id === 'card-J' ? card
  : id === 'shortfallWarn' ? {{classList:{{remove(){{}}}}}} : null);
globalThis.fetch = (url, opt) => {{ calls.push({{url, method: (opt||{{}}).method}});
  return Promise.resolve({{ok:true}}); }};
globalThis.removeCard = () => {{}};
globalThis.refreshStartBtn = () => {{}};
globalThis.document = {{ querySelectorAll: () => [] }};
{_js_function('refreshShortfallBanner')}
{_js_function('dismissTerminalCard')}
dismissTerminalCard('J');
console.log(JSON.stringify(calls));
"""
    r = _sp.run(["node", "-e", source], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout.strip().splitlines()[-1])


class TestDismissedFailureDoesNotComeBack(unittest.TestCase):
    """外审第三轮③（**我上一轮返工的副作用**）：
    **用户点 × 关掉失败卡，刷新一下它又回来了。**

    我上一轮让「带欠账说明的失败任务刷新后能重新发现」——那一半是对的，
    **但没管用户主动关掉它之后怎么办**：
      · 实时失败后前端已把任务从 `activeJobs` 删掉；
      · 用户点 × → `dismissTerminalCard` **只删界面，不通知后端**；
      · 而后端现在规定这类任务必须出现在刷新列表里。
    → 下次刷新，同一张失败卡又回来。

    旁边的 `dismissRestoredCard`（恢复来的卡片）**早就会发 DELETE**，
    只有实时终态这条路漏了。照它补齐即可，后端 `DELETE /api/jobs/{id}` 一直都在。

    **done 卡片不许删**：成品还要能「在文件夹中显示」，
    这也是 `dismissRestoredCard` 的既有规则（`restoredStatus !== 'done'` 才删）。
    """

    def test_closing_a_failure_card_deletes_the_backend_record(self):
        calls = _run_dismiss(["job-card", "job-card-error-terminal"])
        deletes = [c for c in calls if c.get("method") == "DELETE"]
        self.assertEqual(len(deletes), 1,
                         "关掉失败卡没通知后端——刷新一下它就回来了")
        self.assertIn("/api/jobs/J", deletes[0]["url"])

    def test_closing_a_done_card_keeps_the_record(self):
        """回归：成功任务的记录不许删——成品还要能在文件夹里找到。"""
        calls = _run_dismiss(["job-card", "job-card-done"])
        self.assertEqual([c for c in calls if c.get("method") == "DELETE"], [],
                         "删掉成功任务的记录，用户就找不到自己的成品了")


class TestCompletionCredentialRecordsWhatWasActuallyMade(unittest.TestCase):
    """外审第四轮①：**完成凭据记的是用户「要什么」，不是我们「做了什么」。**

    拖 mp3 + 选烧录 → 后端正确降级成只出字幕 → 做完了。
    但完成凭据里存的仍是 config 的 `outputMode: burn`，
    于是 `_recover_completed_checkpoint` 去找一个**本来就不会生成**的视频成品，
    找不到 → 判定「没做完」→ 用户重开软件后要白跑一遍（字幕其实早就在硬盘上）。

    **触发条件**：音频输入 + 选烧录 + **完成消息丢失** + 重启。
    第三个条件窗口只有零点几秒，所以**低频**——但这套恢复机制存在本身
    就说明它发生过（`worker.py` 注释：「完成消息若丢失，恢复端只认这份证据」）。
    **修它一行，防的是白跑半小时。**

    **为什么走独立字段而不是改 meta 里的 config**：`_read_checkpoint_meta`
    要拿 meta 做配置变更检测。把 `outputMode` 从 burn 改成 srt 写进去，
    下次跑会被判成「配置变了」→ 作废缓存 → **又白跑一遍转录**。
    那是修一个低频问题换一个高频问题，这个项目栽过两次。
    """

    def _recover(self, cfg_mode, ckpt_output_mode):
        import tempfile
        from backend import main as M   # noqa: F401  (下面构造哈希要用)

        tmp = Path(tempfile.mkdtemp())
        jid = "donejob01"
        job_dir = tmp / jid
        job_dir.mkdir(parents=True)
        src = job_dir / "a.mp3"
        src.write_bytes(b"fake audio bytes")
        # `_matching_checkpoint` 有三道校验：input_hash 16 位、
        # 缓存目录名 = hash 前 12 位、文件真实哈希对得上。用真值构造。
        digest = M._hash_local_file(str(src))
        cache_dir = job_dir / "cache" / digest[:12]
        cache_dir.mkdir(parents=True)
        srt = job_dir / "out.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n\n", encoding="utf-8")
        ckpt = {"input_file": str(src), "input_hash": digest[:16], "stage": "done",
                "meta": {"outputMode": cfg_mode}, "run_id": "r1",
                "result_srt": str(srt), "result_video": None}
        if ckpt_output_mode is not None:
            ckpt["output_mode"] = ckpt_output_mode
        (cache_dir / "checkpoint.json").write_text(
            _json.dumps(ckpt), encoding="utf-8")
        meta = {"status": "running", "file_hash": digest,
                "run_id": "r1", "config": {"outputMode": cfg_mode}}
        (job_dir / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
        try:
            with mock.patch.object(M, "JOBS_DIR", tmp):
                return M._recover_completed_checkpoint(job_dir, meta)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_downgraded_audio_job_still_recovers(self):
        """用户选 burn、实际出 srt —— 必须能恢复成「已完成」。"""
        got = self._recover(cfg_mode="burn", ckpt_output_mode="srt")
        self.assertIsNotNone(
            got, "凭据里记了实际做的是 srt，就不该再去找那个不存在的视频")
        self.assertEqual(got["status"], "done")
        self.assertIsNone(got["result_video"])

    def test_real_burn_job_still_needs_its_video(self):
        """回归：真烧录任务少了视频，仍然不许判「已完成」。"""
        self.assertIsNone(self._recover(cfg_mode="burn", ckpt_output_mode="burn"))

    def test_old_checkpoint_without_the_field_falls_back(self):
        """向后兼容：老凭据没有这个字段时，沿用 config 的判断。"""
        self.assertIsNone(self._recover(cfg_mode="burn", ckpt_output_mode=None))
        self.assertIsNotNone(self._recover(cfg_mode="srt", ckpt_output_mode=None))

    def test_worker_passes_the_resolved_mode(self):
        """⚠️ 结构检查：worker 的 done 凭据必须传实际 mode，不是 config 里的。"""
        import ast as _ast
        src = (Path(__file__).resolve().parent.parent
               / "backend" / "worker.py").read_text(encoding="utf-8")
        fn = next(n for n in _ast.walk(_ast.parse(src))
                  if isinstance(n, _ast.FunctionDef) and n.name == "main")
        for node in _ast.walk(fn):
            if (isinstance(node, _ast.Call)
                    and isinstance(node.func, _ast.Attribute)
                    and node.func.attr == "save"
                    and node.args and isinstance(node.args[0], _ast.Constant)
                    and node.args[0].value == "done"):
                kw = {k.arg for k in node.keywords}
                self.assertIn("output_mode", kw,
                              "done 凭据没记实际输出方式——恢复端只能去猜")
                return
        self.fail("找不到 done 凭据的写入点")


class TestSuccessCardDropsTheFailureMark(unittest.TestCase):
    """外审第四轮②：**一张卡同时挂着「失败」和「完成」两个标记。**

    `renderCardError` 加 `job-card-error-terminal`，**全项目没有一处移除它**；
    `renderCardDone` 只 add `job-card-done`。于是同一张卡可以两个都有。

    **可达路径**（要凑齐五步）：失败 → 不关卡片 → 不刷新页面 →
    重提同一文件 → 这次成功。第一次失败第二次成功是真实的
    （模型没装好、网络断了、内存不够，处理完再试），
    但大部分人失败后会顺手关掉红卡或刷一下页面——所以**低频**。

    **后果比外审判的轻**：误发的 DELETE 只删「上传的原文件副本 + 任务记录」，
    **字幕和成品视频一个都不会丢**，而且是用户自己点的 ×。
    所以这是 **P2 不是 P1**——记在这里，免得后人翻记录以为放过了一个 P1。

    **仍然要修**：不是为了那五步场景，而是
    **「一张卡同时是失败和成功」本身就是个状态错误**。修法一行。
    """

    def test_done_removes_the_error_class(self):
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        fn = _js_function("renderCardDone")
        self.assertIn("job-card-error-terminal", fn,
                      "变成功的时候必须把失败标记摘掉，否则关闭时会被当成失败卡删记录")
        self.assertRegex(fn, r"remove\([^)]*job-card-error-terminal")


class TestBannerFollowsRemainingCards(unittest.TestCase):
    """外审第四轮③：**关掉唯一那张失败卡，顶部红条还留着**，指向一个已经没有的任务。

    修的时候不能无条件清空——页面上可能还有**另一张**失败卡，
    那条提示还得留着。**按剩余卡片重算**。
    """

    @staticmethod
    def _run(remaining_error_cards: int):
        src = f"""
const bar = {{textContent:'旧的失败提示', _cls:new Set(['qm-warn','red','active']),
  classList: {{ remove(...x){{x.forEach(c=>bar._cls.delete(c))}},
                add(...x){{x.forEach(c=>bar._cls.add(c))}} }} }};
globalThis.$ = (id) => (id === 'shortfallWarn' ? bar : null);
globalThis.document = {{ querySelectorAll: () => new Array({remaining_error_cards}).fill({{}}) }};
{_js_function('refreshShortfallBanner')}
refreshShortfallBanner();
console.log(JSON.stringify({{cls:[...bar._cls].join(' ')}}));
"""
        r = _sp.run(["node", "-e", src], text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
        return _json.loads(r.stdout.strip().splitlines()[-1])

    def test_last_failure_card_gone_clears_the_banner(self):
        self.assertNotIn("active", self._run(0)["cls"],
                         "最后一张失败卡关掉了，红条还指着一个不存在的任务")

    def test_another_failure_card_keeps_it(self):
        self.assertIn("active", self._run(1)["cls"],
                      "还有别的失败卡，不能把提示一起清掉")

    def test_dismiss_recomputes_the_banner(self):
        """⚠️ 结构检查：关闭卡片的两条路都要重算。"""
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        for fn_name in ("dismissTerminalCard", "dismissRestoredCard"):
            self.assertIn("refreshShortfallBanner", _js_function(fn_name),
                          f"{fn_name} 关掉卡片后没重算顶部提示")


def _run_effective_mode(video_name, local_path, source_url, settings_mode):
    """在 node 里真跑 inputIsAudioOnly / effectiveOutputMode。"""
    src = f"""
globalThis.State = {{
  videoFile: {_json.dumps(video_name)} ? {{name: {_json.dumps(video_name)}}} : null,
  localPath: {_json.dumps(local_path)},
  sourceUrl: {_json.dumps(source_url)},
  settings: {{outputMode: {_json.dumps(settings_mode)}}} }};
{_js_const('VIDEO_EXTS')}
{_js_const('AUDIO_EXTS')}
{_js_const('SUBTITLE_EXTS')}
{_js_function('fileKind')}
{_js_function('inputIsAudioOnly')}
{_js_function('effectiveOutputMode')}
console.log(JSON.stringify({{audio: inputIsAudioOnly(), mode: effectiveOutputMode()}}));
"""
    r = _sp.run(["node", "-e", src], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout.strip().splitlines()[-1])


class TestUiPromisesOnlyWhatItCanDeliver(unittest.TestCase):
    """外审第四轮④（**唯一 100% 会发生的一条**）：
    **拖 mp3 进来，按钮上写着「开始翻译并烧录」，点下去只得到一个字幕文件。**

    ```
    index.html  默认 outputMode: "burn"
    index.html  _modeLabels = {burn:'开始翻译并烧录', ...}
    ```
    后端行为是对的（没画面就只出字幕），**但产品当着用户的面承诺了做不到的事**。
    **干净安装的新用户默认就是烧录**，比老用户更容易撞上——
    这一条不依赖 Mac/Windows，也不依赖有没有缓存。

    **修法不加新状态字段**：从现有的文件名算出来。
    加一个 `State.inputKind` 就要在 selectFile / lpi / uri / 清空 / 重置
    五个地方同步，**其中一处忘了改就是下一个 bug**——这个项目反复栽在这个形状上。
    """

    def test_audio_file_collapses_the_promise(self):
        got = _run_effective_mode("song.mp3", None, None, "burn")
        self.assertTrue(got["audio"])
        self.assertEqual(got["mode"], "srt", "拖 mp3 时界面只能承诺字幕文件")

    def test_video_file_keeps_user_choice(self):
        got = _run_effective_mode("movie.mp4", None, None, "burn")
        self.assertFalse(got["audio"])
        self.assertEqual(got["mode"], "burn", "换回视频要恢复用户原来的选择")

    def test_local_audio_path_also_collapses(self):
        """本地路径输入框是独立的一条入口，同样要算。"""
        got = _run_effective_mode(None, "/music/a.m4a", None, "mux")
        self.assertEqual(got["mode"], "srt")

    def test_local_video_path_keeps_choice(self):
        got = _run_effective_mode(None, "/movies/a.mkv", None, "mux")
        self.assertEqual(got["mode"], "mux")

    def test_url_keeps_user_choice(self):
        """URL 下载前不知道里面是什么——**不许猜**，保持用户选择，后端会收敛。"""
        got = _run_effective_mode(None, None, "https://youtu.be/x", "burn")
        self.assertFalse(got["audio"])
        self.assertEqual(got["mode"], "burn")

    def test_nothing_selected_is_not_audio(self):
        got = _run_effective_mode(None, None, None, "burn")
        self.assertFalse(got["audio"])

    def test_button_label_uses_effective_mode(self):
        """⚠️ 结构检查：按钮文案必须用收敛后的方式，不是 State.settings 的原值。"""
        fn = _js_function("refreshStartBtn")
        self.assertIn("effectiveOutputMode()", fn,
                      "按钮还在念用户原来的选择——那正是它说谎的地方")

    def test_output_select_reflects_it_too(self):
        """下拉框也要跟着收敛，否则界面自相矛盾。"""
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("syncOutputModeUI", html)
        fn = _js_function("syncOutputModeUI")
        self.assertIn("effectiveOutputMode()", fn)
        self.assertIn("disabled", fn, "音频输入时不该让用户还能选烧录")


class TestAlbumArtIsNotAPicture(unittest.TestCase):
    """外审第五轮①（**新问题**）：**带专辑封面的 MP3 被当成视频，去烧录了。**

    很多 mp3 内嵌一张专辑封面。ffprobe 把它报成一路 `video` 流，
    于是「有没有画面」判成有，`resolve_output_mode` 不降级，用户选的烧录被保留。

    真实复现（同一段声音，只差一张封面图）：
    ```
    plain.mp3    →  {'has_video': False}  →  ('srt',  False, False)  ✅
    withart.mp3  →  {'has_video': True }  →  ('burn', True,  False)  ❌
    ```

    **封面不是画面。** ffmpeg 自己就标了这件事：封面流带 `attached_pic=1`，
    真视频流是 `0`。判据从「有没有 video 流」改成「有没有**不是封面**的 video 流」。

    **状态份数（第四问）**：不新增任何状态，只改一处判据 —— 探测函数仍然
    一次 ffprobe 回答两问，调用方一个字不用改。
    """

    @staticmethod
    def _make(kind: str, path: Path):
        import subprocess
        if kind == "plain":
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=440:duration=3", str(path)]
        elif kind == "withart":
            art = path.with_suffix(".jpg")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "color=c=blue:s=200x200:d=1", "-frames:v", "1",
                            str(art)], check=True)
            plain = path.with_name("tmp_plain.mp3")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=3", str(plain)], check=True)
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(plain),
                   "-i", str(art), "-map", "0:a", "-map", "1:v", "-c", "copy",
                   "-id3v2_version", "3",
                   "-metadata:s:v", "comment=Cover (front)", str(path)]
        else:  # real video
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
                   "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:d=2",
                   "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode == 0 and path.exists()

    def test_cover_art_does_not_count_as_video(self):
        import tempfile
        from backend.core.extractor import probe_media_streams
        from backend.worker import resolve_delivery_flags

        with tempfile.TemporaryDirectory() as td:
            art = Path(td) / "withart.mp3"
            if not self._make("withart", art):
                self.skipTest("ffmpeg 造不出带封面的 mp3")
            streams = probe_media_streams(str(art))
            self.assertTrue(streams["has_audio"])
            self.assertFalse(streams["has_video"],
                             "专辑封面不是画面——把它当画面就会去烧录一张静态图")
            self.assertEqual(
                resolve_delivery_flags({"outputMode": "burn"}, str(art)),
                ("srt", False, False))

    def test_real_video_still_counts(self):
        import tempfile
        from backend.core.extractor import probe_media_streams

        with tempfile.TemporaryDirectory() as td:
            vid = Path(td) / "real.mp4"
            if not self._make("real", vid):
                self.skipTest("ffmpeg 造不出测试视频")
            streams = probe_media_streams(str(vid))
            self.assertTrue(streams["has_video"], "真视频不能被误判成封面")
            self.assertTrue(streams["has_audio"])

    def test_plain_audio_unchanged(self):
        import tempfile
        from backend.core.extractor import probe_media_streams

        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "plain.mp3"
            if not self._make("plain", a):
                self.skipTest("ffmpeg 不可用")
            self.assertEqual(probe_media_streams(str(a)),
                             {"has_audio": True, "has_video": False})


def _run_sync_output_ui(video_name, settings_mode):
    """真跑 syncOutputModeUI，返回四个界面元素的最终状态。"""
    src = f"""
const els = {{}};
for (const id of ['outputModeSelect','audioOnlyHint','burnWarn','muxHint','styleSubSection'])
  els[id] = {{id, value:'', disabled:false, style:{{display:''}}}};
globalThis.$ = (id) => els[id] || null;
globalThis.State = {{ videoFile: {_json.dumps(video_name)} ? {{name:{_json.dumps(video_name)}}} : null,
  localPath: null, sourceUrl: null, settings: {{outputMode: {_json.dumps(settings_mode)}}} }};
{_js_const('VIDEO_EXTS')}
{_js_const('AUDIO_EXTS')}
{_js_const('SUBTITLE_EXTS')}
{_js_function('fileKind')}
{_js_function('inputIsAudioOnly')}
{_js_function('effectiveOutputMode')}
{_js_function('syncOutputModeUI')}
syncOutputModeUI();
console.log(JSON.stringify({{
  sel: els['outputModeSelect'].value, disabled: els['outputModeSelect'].disabled,
  audioHint: els['audioOnlyHint'].style.display,
  burnWarn: els['burnWarn'].style.display,
  muxHint: els['muxHint'].style.display,
  styleSec: els['styleSubSection'].style.display}}));
"""
    r = _sp.run(["node", "-e", src], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout.strip().splitlines()[-1])


class TestAudioUiIsConsistentEverywhere(unittest.TestCase):
    """外审第五轮②：**界面说明没改齐——按钮改了，旁边的烧录说明还在。**

    这不是新问题，是我上一轮**只改了四分之三**：
    按钮文案 ✅、下拉框 ✅、新增提示 ✅、
    **「烧录后字幕将永久固定在视频画面上」这行红字 ❌ 忘了**。

    根源正是我半小时前刚写进 CLAUDE.md 第四问的第一种形状——
    **同一份规则抄了多遍**：烧录相关的界面显隐散在**三处**
    （下拉框 change 监听器、加载配置、syncOutputModeUI），改一处漏两处。

    **修法就是第四问的答案：让它变成一份。** 显隐逻辑全部收进
    `syncOutputModeUI()`，另外两处只调用它，不再各写一遍。
    """

    def test_audio_input_hides_every_burn_related_hint(self):
        got = _run_sync_output_ui("song.mp3", "burn")
        self.assertEqual(got["sel"], "srt")
        self.assertTrue(got["disabled"])
        self.assertEqual(got["audioHint"], "block")
        self.assertEqual(got["burnWarn"], "none",
                         "按钮说「生成字幕」，底下还挂着「烧录后永久固定在画面上」——自相矛盾")
        self.assertEqual(got["styleSec"], "none",
                         "只出字幕时不该还让用户调烧录样式")

    def test_video_input_shows_burn_hints_again(self):
        got = _run_sync_output_ui("movie.mp4", "burn")
        self.assertEqual(got["sel"], "burn")
        self.assertFalse(got["disabled"])
        self.assertEqual(got["audioHint"], "none")
        self.assertEqual(got["burnWarn"], "block", "换回视频要把烧录提示放回来")
        self.assertEqual(got["styleSec"], "block")

    def test_mux_shows_its_own_hint(self):
        got = _run_sync_output_ui("movie.mp4", "mux")
        self.assertEqual(got["muxHint"], "block")
        self.assertEqual(got["burnWarn"], "none")
        self.assertEqual(got["styleSec"], "none")

    def test_the_two_other_places_delegate(self):
        """⚠️ 结构检查：另外两处不许再各写一遍显隐——那正是漏改的来源。"""
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        # 除 syncOutputModeUI 内部外，全项目不应再出现直接改 burnWarn 显隐的代码
        fn = _js_function("syncOutputModeUI")
        outside = html.replace(fn, "")
        self.assertNotIn("$('burnWarn').style.display", outside,
                         "还有地方在自己改烧录提示的显隐——同一件事又变成两份了")


def _run_banner_scenario(steps, remaining_error_cards):
    """真跑：按 steps 依次显示提示，最后关掉某个任务，看红条剩什么。

    steps: [(msg, keepIfClean, jid), ...]，最后一项是 ('close', jid)
    """
    show_calls = []
    close_jid = None
    for st in steps:
        if st[0] == "close":
            close_jid = st[1]
        else:
            show_calls.append(st)
    src = f"""
const bar = {{textContent:'', dataset:{{}}, _cls:new Set(),
  classList:{{ remove(...x){{x.forEach(c=>bar._cls.delete(c))}},
               add(...x){{x.forEach(c=>bar._cls.add(c))}} }} }};
Object.defineProperty(bar,'className',{{get(){{return [...bar._cls].join(' ')}},
  set(v){{bar._cls=new Set(String(v).split(/\\s+/).filter(Boolean))}}}});
globalThis.$ = (id) => (id === 'shortfallWarn' ? bar : null);
globalThis.document = {{ querySelectorAll: () => new Array({remaining_error_cards}).fill({{}}) }};
{_js_function('showTranscriptionShortfall')}
{_js_function('refreshShortfallBanner')}
for (const [msg, keep, jid] of {_json.dumps(show_calls)}) showTranscriptionShortfall(msg, keep, jid);
refreshShortfallBanner({_json.dumps(close_jid)});
console.log(JSON.stringify({{text: bar.textContent, cls: bar.className, owner: bar.dataset.jid}}));
"""
    r = _sp.run(["node", "-e", src], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout.strip().splitlines()[-1])


class TestBannerKnowsWhoItBelongsTo(unittest.TestCase):
    """外审第五轮③：**关掉一张失败卡，顶部红条还留着那张卡的错误。**

    这不是新问题，是我上一轮的**逻辑不够**：`refreshShortfallBanner` 只问了
    「还有没有失败卡」，**没问「红条上现在写的是哪张卡的错误」**。

    真实场景：任务 A 失败（红条显示 A）→ 任务 B 失败（红条改成显示 B）
    → 关掉 B → 页面还剩 A，所以不清空 → **红条继续显示已经被关掉的 B 的错误**。

    根源是我半小时前刚写进 CLAUDE.md 第四问的第二种形状——
    **全局状态 vs 每项状态**：红条只有一条（全局），它描述的错误却属于某一张卡。
    **修法：让红条记住自己属于谁。**
    """

    A = {"transcription_shortfall": {"severity": "error", "message": "任务A：已处理到 10:00"}}
    B = {"transcription_shortfall": {"severity": "error", "message": "任务B：已处理到 20:00"}}

    def test_closing_the_owner_clears_it_even_if_others_remain(self):
        got = _run_banner_scenario(
            [(self.A, False, "a"), (self.B, False, "b"), ("close", "b")],
            remaining_error_cards=1)
        self.assertNotIn("active", got["cls"],
                         f"关掉的就是红条的主人，它必须一起走。实际还留着：{got['text']!r}")

    def test_closing_someone_else_keeps_it(self):
        got = _run_banner_scenario(
            [(self.A, False, "a"), (self.B, False, "b"), ("close", "a")],
            remaining_error_cards=1)
        self.assertIn("active", got["cls"], "关的是别人，B 的提示不该被连累")
        self.assertIn("任务B", got["text"])

    def test_last_card_gone_still_clears(self):
        """回归：一张失败卡都不剩时照旧清空。"""
        got = _run_banner_scenario(
            [(self.B, False, "b"), ("close", "a")], remaining_error_cards=0)
        self.assertNotIn("active", got["cls"])

    def test_banner_records_its_owner(self):
        got = _run_banner_scenario([(self.B, False, "b")], remaining_error_cards=1)
        self.assertEqual(got["owner"], "b", "红条要记住自己属于哪个任务")

    def test_callers_pass_the_job_id(self):
        """⚠️ 结构检查：调用点必须把 jid 传进去，否则红条永远不知道主人是谁。"""
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        calls = _re.findall(r"showTranscriptionShortfall\(([^)]*)\)", html)
        calls = [c for c in calls if "function" not in c]
        self.assertTrue(calls)
        for c in calls:
            self.assertGreaterEqual(
                len(c.split(",")), 3,
                f"这处调用没传 job_id：showTranscriptionShortfall({c})")
