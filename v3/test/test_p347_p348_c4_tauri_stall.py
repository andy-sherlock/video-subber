"""P347/P348: Tauri speech-recognition stall and unresponsive cancel button.

P347 is a Tauri-only stdin inheritance bug. Unit tests cannot reproduce the
WebView hang, so they verify the guardrails on every media subprocess call.
P348 is a frontend contract check: the job card close button must use the page
modal, not native confirm().
"""
import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _run_kwargs(mock_run):
    return mock_run.call_args.kwargs


def _run_cmd(mock_run):
    return mock_run.call_args.args[0]


def _js_function(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    i = html.index("(", start)
    paren = 0
    while i < len(html):
        if html[i] == "(":
            paren += 1
        elif html[i] == ")":
            paren -= 1
            if paren == 0:
                break
        i += 1
    brace = html.index("{", i)
    depth = 0
    for i in range(brace, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    raise AssertionError(f"找不到完整函数 {name}")


class TestP347MediaSubprocessGuards(unittest.TestCase):
    """The 5 same-class media subprocesses must not inherit Tauri stdin."""

    def test_language_probe_ffmpeg_has_stdin_guard_and_timeout(self):
        with patch("backend.core.whisper_runner.tempfile.mkstemp",
                   return_value=(123, "/tmp/p347-probe.wav")), \
             patch("backend.core.whisper_runner.os.close"), \
             patch("backend.core.whisper_runner.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            from backend.core.whisper_runner import _create_language_probe_clip

            self.assertEqual(_create_language_probe_clip("/tmp/audio.wav"), "/tmp/p347-probe.wav")

        cmd = _run_cmd(run)
        kwargs = _run_kwargs(run)
        self.assertIn("-nostdin", cmd)
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("timeout"), 60)
        self.assertTrue(kwargs.get("check"))

    def test_qwen_duration_ffprobe_has_stdin_guard_and_timeout(self):
        with patch("backend.core.qwen_asr_runner.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="12.5\n", stderr="")
            from backend.core.qwen_asr_runner import _get_audio_duration

            self.assertEqual(_get_audio_duration("/tmp/audio.wav"), 12.5)

        kwargs = _run_kwargs(run)
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("timeout"), 30)

    def test_qwen_chunk_ffmpeg_has_stdin_guard_and_timeout(self):
        with patch("backend.core.qwen_asr_runner.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            from backend.core.qwen_asr_runner import _extract_audio_chunk

            _extract_audio_chunk("/tmp/audio.wav", 5.0, 15.0, "/tmp/chunk.wav")

        cmd = _run_cmd(run)
        kwargs = _run_kwargs(run)
        self.assertIn("-nostdin", cmd)
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("timeout"), 300)
        self.assertTrue(kwargs.get("check"))

    def test_mux_subtitles_ffmpeg_has_stdin_guard_and_timeout(self):
        with patch("backend.core.subtitler._find_ffmpeg", return_value="ffmpeg"), \
             patch("backend.core.subtitler.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("backend.core.subtitler.Path.is_file", return_value=True), \
             patch("backend.core.subtitler.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
            from backend.core.subtitler import mux_subtitles

            mux_subtitles("input.mp4", "subs.srt", "output.mp4")

        cmd = _run_cmd(run)
        kwargs = _run_kwargs(run)
        self.assertIn("-nostdin", cmd)
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("timeout"), 1800)

    def test_burn_subtitles_ffmpeg_has_stdin_guard_and_long_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            ass = Path(d) / "subs.ass"
            ass.write_text("[Script Info]\n", encoding="utf-8")
            with patch("backend.core.subtitler._find_ffmpeg", return_value="ffmpeg"), \
                 patch("backend.core.subtitler.Path.is_file", return_value=True), \
                 patch("backend.core.subtitler.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr="")
                from backend.core.subtitler import burn_subtitles

                burn_subtitles("input.mp4", str(ass), "output.mp4")

        cmd = _run_cmd(run)
        kwargs = _run_kwargs(run)
        self.assertIn("-nostdin", cmd)
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertGreaterEqual(kwargs.get("timeout"), 7200)


class TestP347ProbeClipBehavior(unittest.TestCase):
    """Probe clip must be real WAV output, and timeout must clean up temp files."""

    def test_language_probe_timeout_removes_temp_file_and_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.wav"
            probe.write_bytes(b"partial")
            with patch("backend.core.whisper_runner.tempfile.mkstemp",
                       return_value=(123, str(probe))), \
                 patch("backend.core.whisper_runner.os.close"), \
                 patch("backend.core.whisper_runner.subprocess.run",
                       side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)):
                from backend.core.whisper_runner import _create_language_probe_clip

                with self.assertRaises(RuntimeError) as ctx:
                    _create_language_probe_clip("/tmp/audio.wav")

            self.assertFalse(probe.exists())
            self.assertIn("语言探测音频截取超时", str(ctx.exception))

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要本机 ffmpeg")
    def test_detect_language_probe_clip_is_valid_wav_and_removed_after_success(self):
        fake_mlx = types.ModuleType("mlx")
        fake_core = types.ModuleType("mlx.core")
        fake_core.float16 = "float16"
        fake_mlx.core = fake_core

        fake_audio = types.ModuleType("mlx_whisper.audio")
        fake_audio.N_FRAMES = 3000
        fake_audio.N_SAMPLES = 480000
        observed = {}

        def load_audio(path):
            observed["probe_path"] = path
            with wave.open(path, "rb") as w:
                observed["framerate"] = w.getframerate()
                observed["channels"] = w.getnchannels()
                observed["frames"] = w.getnframes()
            return [0.0]

        class _FakeMel:
            def astype(self, _dtype):
                return self

        fake_audio.load_audio = load_audio
        fake_audio.log_mel_spectrogram = lambda *a, **kw: _FakeMel()
        fake_audio.pad_or_trim = lambda mel, *a, **kw: mel

        fake_load_models = types.ModuleType("mlx_whisper.load_models")
        fake_load_models.load_model = lambda *a, **kw: types.SimpleNamespace(
            dims=types.SimpleNamespace(n_mels=128))

        fake_whisper = types.ModuleType("mlx_whisper")
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src.wav"
            gen = subprocess.run([
                "ffmpeg", "-nostdin", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-ac", "1", "-ar", "44100", str(src),
            ], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(gen.returncode, 0, gen.stderr[-300:])

            with patch.dict(sys.modules, {
                "mlx": fake_mlx,
                "mlx.core": fake_core,
                "mlx_whisper": fake_whisper,
                "mlx_whisper.audio": fake_audio,
                "mlx_whisper.load_models": fake_load_models,
            }), patch("backend.core.whisper_runner.resolve_whisper_model_path",
                      return_value="/models/large-v2"):
                from backend.core.whisper_runner import detect_language_with_mlx_whisper

                info = detect_language_with_mlx_whisper(
                    str(src), model_size="large-v2", language="ja", probe_seconds=1)

            self.assertEqual(info.language, "ja")
            self.assertEqual(observed["framerate"], 16000)
            self.assertEqual(observed["channels"], 1)
            self.assertGreater(observed["frames"], 0)
            self.assertFalse(Path(observed["probe_path"]).exists(),
                             "detect_language_with_mlx_whisper 成功后必须清掉 probe 临时文件")


class TestP348CancelCardModal(unittest.TestCase):
    """Running job cancel must use an in-page modal and a bounded DELETE request."""

    @classmethod
    def setUpClass(cls):
        cls.html = Path("frontend/index.html").read_text(encoding="utf-8")
        m = re.search(r"function cancelCard\(jid\)\{(?P<body>.*?)\n\}", cls.html, re.S)
        cls.cancel_body = m.group("body") if m else ""

    def test_cancel_card_does_not_use_native_confirm(self):
        self.assertNotIn("confirm(", self.cancel_body)
        self.assertIn("setInfoModal", self.cancel_body)

    def test_cancel_card_keeps_restored_card_dismiss_path(self):
        self.assertIn("restoredStatus", self.cancel_body)
        self.assertIn("dismissRestoredCard(jid)", self.cancel_body)

    def test_cancel_delete_request_has_timeout_and_human_error(self):
        self.assertIn("AbortController", self.html)
        self.assertIn("fetchWithTimeout", self.html)
        self.assertRegex(self.html, r"fetchWithTimeout\('/api/jobs/'\+jid,\{method:'DELETE'\},\d+\)")
        self.assertIn("取消任务失败", self.html)
        self.assertIn("正在取消", self.html)

    def test_p349_cancel_modal_uses_unambiguous_actions(self):
        """P349: 运行中点取消时，两个按钮不能都带“取消”造成误解。"""
        self.assertIn("取消这个任务？", self.html)
        self.assertIn("已识别的进度会全部丢失，无法恢复。", self.html)
        self.assertIn("继续处理", self.html)
        self.assertIn("取消任务", self.html)
        self.assertNotIn("确定要取消", self.cancel_body)
        self.assertNotIn("确定取消", self.cancel_body)

    def test_p350_terminal_error_card_close_does_not_need_active_job(self):
        """P350: error 卡片右上角关闭应能移除卡片，不再依赖 activeJobs。"""
        compact = self.html.replace(" ", "")
        m = re.search(r"async function requestCancelCard\(jid\)\{(?P<body>.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 requestCancelCard")
        request_body = m.group("body").replace(" ", "")
        self.assertIn("function dismissTerminalCard(jid)", self.html)
        self.assertIn("if(!job){dismissTerminalCard(jid);return}", compact)
        self.assertNotIn("if(!job)return", request_body)

    def test_p351_user_cancel_resets_to_clean_first_scene(self):
        """P351: 用户主动取消后回到初始态，视频框不能残留处理中。"""
        compact = self.html.replace(" ", "")
        self.assertIn("function resetAfterUserCancel()", self.html)
        self.assertIn("setScene('first')", compact)
        self.assertIn("showVideoBox('','')", compact)
        self.assertIn("State.videoFile=null", compact)
        self.assertIn("State.sourceUrl=null", compact)
        self.assertIn("State.localPath=null", compact)
        self.assertIn("resetAfterUserCancel()", compact)

    def test_bf1_terminal_error_or_interrupted_leaves_processing_scene(self):
        """BF-1: error/interrupted 终态后不能留下空的处理中界面。"""
        js = "\n".join([
            r"""
const els = {};
class El {
  constructor(id='') {
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.textContent = '';
    this._html = '';
    const classes = new Set();
    this.classList = {
      add: (...items) => items.forEach(c => classes.add(c)),
      remove: (...items) => items.forEach(c => classes.delete(c)),
      contains: c => classes.has(c),
      toggle: (c, v) => {
        const on = v === undefined ? !classes.has(c) : !!v;
        if (on) classes.add(c); else classes.delete(c);
        return on;
      }
    };
    if (id) this.id = id;
  }
  set id(v) { this._id = v; if (v) els[v] = this; }
  get id() { return this._id; }
  set className(v) {
    this._className = v;
    String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c));
  }
  get className() { return this._className || ''; }
  set innerHTML(v) {
    this._html = v;
    const re = /id="([^"]+)"/g;
    let m;
    while ((m = re.exec(v))) {
      const child = new El(m[1]);
      child.parentNode = this;
      this.children.push(child);
    }
  }
  get innerHTML() { return this._html; }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (child.id) els[child.id] = child;
    return child;
  }
  remove() {
    if (this.id) delete els[this.id];
    for (const child of this.children) {
      if (child.id) delete els[child.id];
    }
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter(c => c !== this);
    }
  }
  querySelectorAll() { return []; }
  addEventListener() {}
  removeAttribute() {}
}
['app','jobCards','vbName','vbSub','urlInput','localPathInput','dzFile','startBtn'].forEach(id => new El(id));
els.app.classList.add('app', 'scene-processing');
const document = {
  querySelector: sel => sel === '.app' ? els.app : null,
  createElement: () => new El()
};
const localStorage = {setItem(){}, getItem(){return '[]';}};
const State = {settings:{},videoFile:null,sourceUrl:null,localPath:null,doneJobConfig:null,forceRerun:false};
const activeJobs = {};
const _MILESTONES = ['准备视频','识别语音','检查字幕','翻译','生成文件'];
let settingsLocked = true;
const sbtn = els.startBtn, uri = els.urlInput, lpi = els.localPathInput, df = els.dzFile;
function $(id){ return els[id] || null; }
function unlockUI(){}
function refreshStartBtn(){}
function showToast(){}
""",
            _js_function(self.html, "hasUserAdvice"),
            _js_function(self.html, "formatJobErrorText"),
            _js_function(self.html, "setScene"),
            _js_function(self.html, "currentScene"),
            _js_function(self.html, "showVideoBox"),
            _js_function(self.html, "addJobCard"),
            _js_function(self.html, "renderErrorActions"),
            _js_function(self.html, "terminalJobReason"),
            _js_function(self.html, "terminalVideoSubtitle"),
            _js_function(self.html, "errorStageText"),
            _js_function(self.html, "failMilestones"),
            _js_function(self.html, "renderOutputFolder"),
            _js_function(self.html, "renderCardError"),
            # G12 第三批 A 返工：error 终态现在也要显示转录欠账说明
            # （「已处理到 30:00、模型加载失败」）。缺这一行桩，
            # 上面那句 applyTaskState 会 ReferenceError——
            # **这条测试当场抓到了它，说明 error 分支真的会走到那一行。**
            _js_function(self.html, "showTranscriptionShortfall"),
            _js_function(self.html, "taskStateView"),
            _js_function(self.html, "shouldApplyState"),
            _js_function(self.html, "applyTaskState"),
            _js_function(self.html, "handleCardMsg"),
            _js_function(self.html, "dismissTerminalCard"),
            # 外审第四轮④：关掉卡片后要按剩余失败卡重算顶部提示。
            # 缺这行桩，上面那句 dismissTerminalCard 会 ReferenceError。
            _js_function(self.html, "refreshShortfallBanner"),
            _js_function(self.html, "resetAfterUserCancel"),
            _js_function(self.html, "cancelCard"),
            _js_function(self.html, "removeCard"),
            _js_function(self.html, "checkAllDone"),
            _js_function(self.html, "removeActiveJobId"),
            r"""
function runCase(jid, message) {
  Object.keys(activeJobs).forEach(k => delete activeJobs[k]);
  els.jobCards.children = [];
  settingsLocked = true;
  setScene('processing');
  showVideoBox('demo.mp4', '处理中');
  addJobCard(jid, 'demo.mp4');
  activeJobs[jid].ws = {close(){}};
  activeJobs[jid].pollTimer = 1;
  handleCardMsg(jid, {type:'state', state:{
    schema_version:1,job_id:jid,run_id:'run-1',revision:1,status:'error',
    phase:'translate',percent:100,
    progress:{total:null,processed:null,succeeded:null,failed:null,remaining:null},
    display_message:message,activity:null,
    actions:{can_resume:false,can_retry:false,resume_action:null},
    result:{output_ready:false,can_show_output_folder:false,quality:{}}
  }});
  const afterTerminal = currentScene();
  const subtitleAfterTerminal = els.vbSub.textContent;
  cancelCard(jid);
  return {
    afterTerminal,
    afterDismiss: currentScene(),
    subtitleAfterTerminal,
    cardExists: !!els['card-' + jid]
  };
}
console.log(JSON.stringify({
  failed: runCase('job_error', '后端失败'),
  interrupted: runCase('job_interrupted', '任务已中断，请重新提交以恢复。')
}));
""",
        ])
        result = subprocess.run(["node", "-e", js], text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        for key in ["failed", "interrupted"]:
            self.assertNotEqual(data[key]["afterTerminal"], "scene-processing")
            self.assertIn(data[key]["afterDismiss"], ["scene-done", "scene-first"])
            self.assertFalse(data[key]["cardExists"])
        self.assertIn("失败", data["failed"]["subtitleAfterTerminal"])
        self.assertIn("中断", data["interrupted"]["subtitleAfterTerminal"])
