"""G12 第三批 A · P462：拖一个 mp3 进去，跑完转录跑完翻译，最后在封装那步炸。

**用户看到的**：
    ffmpeg: Error parsing options for output file .../xxx_zh_sub.m4a
    界面显示：处理失败了，我们没能识别出具体原因。请先重试一次……
等了几十分钟，最后才失败，而且没说为什么。

**不是构造的**——G12 第二批做浏览器端到端验证时真跑撞上的。

**产品裁决（已锁定，`video-subtitle.md`）**：

    只要不是视频文件，最终生成的全部都是字幕文件。

    ⚠️ 不要按「是不是视频文件」分流——那样会漏掉**有画面但没有声音的视频**
    （YouTube 画面/声音分开下载的 `*_video.mp4` 就是）。
    2026-09-04 真跑撞到过：任务 9110c09770 死在「音频提取失败」，
    用户只看到「我们没能识别出具体原因」。

    有没有声音？ → 决定这活能不能干（没声音 = 什么都做不了）
    有没有画面？ → 决定成品长什么样（没画面 = 烧不了录）

    两件独立的事。**一次 ffprobe 同时给出两个答案，成本不变。**

**因此本文件测的是「一次探测回答两问」**，不是两个平行函数——
后者要跑两次 ffprobe，与合同写明的「成本不变」相违。

**扩展名不能当分流依据**：`.mp4` 可能没有视频流，`.mkv` 的 MIME 常常是空。
扩展名只配在前端做提前提示，最终分流必须看**实际的流**。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_media_streams.py -v
"""
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core import extractor


def _ffprobe_returning(stdout: str, returncode: int = 0):
    """替身：让 ffprobe 返回指定的流清单。"""
    return lambda *a, **k: SimpleNamespace(
        stdout=stdout, stderr="", returncode=returncode)


class TestProbeAnswersBothQuestionsInOneCall(unittest.TestCase):
    """D4：一次探测同时回答「有没有声音」和「有没有画面」。"""

    def test_video_with_sound_can_do_everything(self):
        with mock.patch.object(extractor.subprocess, "run",
                               _ffprobe_returning("video\naudio\n")):
            streams = extractor.probe_media_streams("/tmp/movie.mp4")
        self.assertTrue(streams["has_audio"])
        self.assertTrue(streams["has_video"])

    def test_audio_only_file_has_no_picture(self):
        """mp3 —— 最初那个 bug 的输入。能干活，但烧不了录。"""
        with mock.patch.object(extractor.subprocess, "run",
                               _ffprobe_returning("audio\n")):
            streams = extractor.probe_media_streams("/tmp/song.mp3")
        self.assertTrue(streams["has_audio"], "mp3 有声音，这活能干")
        self.assertFalse(streams["has_video"], "没有画面 → 只能出字幕文件")

    def test_video_without_sound_is_the_real_incident_shape(self):
        """有画面没声音——真实事故 9110c09770 的形状。

        YouTube 画面/声音分开下载时的 `*_video.mp4` 就是这样。
        **按「是不是视频文件」分流会漏掉它**，然后死在「音频提取失败」，
        用户只看到「我们没能识别出具体原因」。
        """
        with mock.patch.object(extractor.subprocess, "run",
                               _ffprobe_returning("video\n")):
            streams = extractor.probe_media_streams("/tmp/only_video.mp4")
        self.assertFalse(streams["has_audio"], "没有声音 → 这活根本干不了")
        self.assertTrue(streams["has_video"])

    def test_one_ffprobe_call_not_two(self):
        """合同写明「一次 ffprobe 同时给出两个答案，成本不变」。"""
        calls = []

        def _counting(*a, **k):
            calls.append(a)
            return SimpleNamespace(stdout="video\naudio\n", stderr="", returncode=0)

        with mock.patch.object(extractor.subprocess, "run", _counting):
            extractor.probe_media_streams("/tmp/movie.mp4")
        self.assertEqual(len(calls), 1,
                         "两个答案必须来自同一次探测，不是两个平行函数各跑一次")


class TestProbeFailureIsUnknownNotFalse(unittest.TestCase):
    """探测失败必须是「不知道」，不能默认成「没有」。

    **同形状的教训**（`silent-zero-duration-restores-single-point-sampling`）：
    任何「失败时返回一个合法默认值」的地方，都要检查那个默认值
    会不会正好落进某个看起来正常的分支。
    把探测失败当成「没有声音」，会让一个正常的视频被判成不能处理。
    """

    def test_ffprobe_failure_yields_unknown(self):
        with mock.patch.object(extractor.subprocess, "run",
                               _ffprobe_returning("", returncode=1)):
            streams = extractor.probe_media_streams("/tmp/broken.mp4")
        self.assertIsNone(streams["has_audio"], "探测失败是「不知道」，不是「没有」")
        self.assertIsNone(streams["has_video"])

    def test_ffprobe_crash_yields_unknown(self):
        def _boom(*a, **k):
            raise OSError("ffprobe not found")

        with mock.patch.object(extractor.subprocess, "run", _boom):
            streams = extractor.probe_media_streams("/tmp/x.mp4")
        self.assertIsNone(streams["has_audio"])
        self.assertIsNone(streams["has_video"])


class TestOutputModeFollowsRealStreamsNotExtension(unittest.TestCase):
    """D5：输出方式由**实际的流**决定，扩展名不参与分流。"""

    def test_audio_only_forces_subtitle_only_output(self):
        """用户就算选了烧录，没有画面也只给字幕文件——不报错、不打断、不弹窗问。"""
        self.assertEqual(
            extractor.resolve_output_mode("burn", has_video=False), "srt")
        self.assertEqual(
            extractor.resolve_output_mode("mux", has_video=False), "srt")

    def test_video_keeps_user_choice(self):
        self.assertEqual(
            extractor.resolve_output_mode("burn", has_video=True), "burn")
        self.assertEqual(
            extractor.resolve_output_mode("mux", has_video=True), "mux")
        self.assertEqual(
            extractor.resolve_output_mode("srt", has_video=True), "srt")

    def test_unknown_video_keeps_user_choice(self):
        """探测不出来时不要擅自降级——那会把正常视频的烧录悄悄取消掉。"""
        self.assertEqual(
            extractor.resolve_output_mode("burn", has_video=None), "burn")


class TestFrontendInputGateStructure(unittest.TestCase):
    """D1/D2/D3/D6 的**结构检查**。

    ⚠️ **证据等级说明**：这是源码结构检查，**不是行为验证**。
    按 ⑤.5 的规矩，只检查源码字符串的测试不计入合同落地证据。
    P462 前端部分的真证据在 **⑨ 界面 QA**：三个文件入口 + 本地路径框
    各拖一次非法文件，看它是否当场拒绝。

    这里只守住「关键结构别被后人整块删掉」，外加一条真能抓 bug 的：
    **调用的提示函数必须真实存在**——第一版写了 `toast(...)`，
    JS 语法检查照样通过（语法合法），运行时才会 ReferenceError。
    """

    @staticmethod
    def _html():
        return (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")

    def test_accept_includes_audio(self):
        html = self._html()
        self.assertIn("audio/*", html,
                      "支持 mp3 却不让文件选择框看到它，等于只有会拖放的人能用")

    def test_format_lists_exist_once(self):
        html = self._html()
        for name in ("VIDEO_EXTS", "AUDIO_EXTS", "SUBTITLE_EXTS"):
            self.assertEqual(html.count(f"const {name}="), 1,
                             f"{name} 必须只有一份——两份清单加格式时必然漏改一处")

    def test_local_path_input_has_its_own_check(self):
        """本地路径框不经过 selectFile，必须自己校验一次。"""
        html = self._html()
        lpi_block = html.split("lpi.addEventListener('input'")[1][:400]
        self.assertIn("fileKind(p)", lpi_block,
                      "本地路径入口绕过 selectFile，漏掉它等于留了一扇没锁的门")

    def test_rejection_uses_a_function_that_actually_exists(self):
        """**这条才是真能抓 bug 的**：提示函数必须真的有定义。

        第一版写成 `toast(...)`，项目里根本没有这个函数。
        `node --check` 照样通过——语法是合法的，运行时才 ReferenceError。
        """
        html = self._html()
        called = set(re.findall(r"\b(showToast|toast)\s*\(", html))
        for fn in called:
            self.assertIn(f"function {fn}(", html,
                          f"前端调用了 {fn}() 但项目里没有它的定义")


if __name__ == "__main__":
    unittest.main()


class TestWorkerActuallyUsesTheProbe(unittest.TestCase):
    """D4/D5 的**接线**测试 —— ⑦.5 diff 审查抓到的缺陷。

    **第一版只写了工具函数，没有任何生产代码调用它们。**
    `probe_media_streams` 和 `resolve_output_mode` 的生产调用数是 0，
    而 2252 个测试全绿——因为测试只测了工具函数自己。

    **拖 mp3 进去仍然会跑完几十分钟再在封装那步炸**，
    也就是 P462 那个 bug 根本没修好。

    这正是 CLAUDE.md 写死的那条：**写测试和写代码是同一个脑子**，
    没想到的场景既不会写进代码也不会写进测试，全绿也照样有真缺陷。
    ⑧ 跑测试替代不了 ⑦.5 读代码。
    """

    def test_audio_input_downgrades_burn_to_subtitle_only(self):
        """拖 mp3 进来、用户选了烧录 → 后端必须自己收敛成只出字幕。

        **测 `resolve_delivery_flags`（打包函数），不是 `resolve_delivery_mode`。**
        ⑧ 的红绿验证证明过：测被调用的那个函数，证明不了主流程调用了它——
        把接线注释掉，那种测试照样全绿。打包之后，绕过它就拿不到 flag。
        """
        from backend import worker

        with mock.patch.object(
                worker, "probe_media_streams",
                lambda _p: {"has_audio": True, "has_video": False}):
            mode, burn, mux = worker.resolve_delivery_flags(
                {"outputMode": "burn"}, "/tmp/song.mp3")
            self.assertEqual(mode, "srt")
            self.assertFalse(burn, "没有画面还烧录 → ffmpeg 必炸")
            self.assertFalse(mux)

            mode, _, mux = worker.resolve_delivery_flags(
                {"outputMode": "mux"}, "/tmp/song.mp3")
            self.assertEqual(mode, "srt")
            self.assertFalse(mux)

    def test_legacy_burn_boolean_still_goes_through_the_probe(self):
        """老配置只有 `burn` 布尔，同样必须经过实际流收敛。"""
        from backend import worker

        with mock.patch.object(
                worker, "probe_media_streams",
                lambda _p: {"has_audio": True, "has_video": False}):
            mode, burn, _ = worker.resolve_delivery_flags(
                {"burn": True}, "/tmp/song.mp3")
        self.assertEqual(mode, "srt", "向后兼容路径不能绕过收敛")
        self.assertFalse(burn)

    def test_video_input_keeps_user_choice(self):
        from backend import worker

        with mock.patch.object(
                worker, "probe_media_streams",
                lambda _p: {"has_audio": True, "has_video": True}):
            mode, burn, _ = worker.resolve_delivery_flags(
                {"outputMode": "burn"}, "/tmp/m.mp4")
        self.assertEqual(mode, "burn")
        self.assertTrue(burn)

    def test_probe_failure_keeps_user_choice(self):
        """探测不出来时不擅自降级——那会把正常视频的烧录悄悄取消掉。"""
        from backend import worker

        with mock.patch.object(
                worker, "probe_media_streams",
                lambda _p: {"has_audio": None, "has_video": None}):
            mode, burn, _ = worker.resolve_delivery_flags(
                {"outputMode": "burn"}, "/tmp/x.mp4")
        self.assertEqual(mode, "burn")
        self.assertTrue(burn)
