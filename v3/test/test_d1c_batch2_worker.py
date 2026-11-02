"""D-1 补修第二批（2026-10-03）干活程序与付费请求合同测试。

合同见 pipelines/方案-D1补修-第二批-v2-2026-10-03.md。
"""

import json
import re
from pathlib import Path

import pytest

from backend import worker
from backend.core import sentence_splitter
from backend.translation import orchestrator, paid_request


# ── P514：任意汇报写不出去即结束；只有一个出口 ─────────────────────────

class _BrokenPipe:
    def write(self, _text):
        raise BrokenPipeError("parent gone")

    def flush(self):
        raise BrokenPipeError("parent gone")


class _Exited(Exception):
    pass


@pytest.fixture
def broken_parent(monkeypatch):
    codes = []

    def fake_exit(code):
        codes.append(code)
        raise _Exited()

    monkeypatch.setattr(worker, "_real_stdout", _BrokenPipe())
    monkeypatch.setattr(worker.os, "_exit", fake_exit)
    # 只隔离出被测的那一处：普通进度另有测试（test_d1b_worker.py）
    monkeypatch.setattr(worker, "stderr_progress", lambda *a, **k: None)
    return codes


class _Ck:
    def save(self, *a, **k):
        pass


def _run_translate(tmp_path, monkeypatch, *, config, spy, cached=None):
    translated_path = tmp_path / "translated.json"
    if cached is not None:
        translated_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    segments = [worker.Segment(0, 0.0, 1.0, "one")]
    monkeypatch.setattr("backend.translation.orchestrator.translate_with_orchestrator", spy)
    worker._step_translate(
        "subtitle", segments, translated_path, "speed", ["google"], [],
        False, config, tmp_path, 1, _Ck(),
    )


def test_engine_status_report_exits_when_main_program_is_gone(tmp_path, monkeypatch, broken_parent):
    def spy(**kwargs):
        kwargs["engine_status_callback"]({"engine_category": "api", "phase": "running"})
        return ["译文"], [], None, {}, {}

    with pytest.raises(_Exited):
        _run_translate(tmp_path, monkeypatch, config={}, spy=spy)
    assert broken_parent == [1]


def test_token_usage_report_exits_when_main_program_is_gone(tmp_path, monkeypatch, broken_parent):
    def spy(**kwargs):
        return ["译文"], [], {"input_tokens": 1, "output_tokens": 1}, {}, {}

    with pytest.raises(_Exited):
        _run_translate(tmp_path, monkeypatch, config={}, spy=spy)
    assert broken_parent == [1]


def test_retry_token_usage_report_exits_when_main_program_is_gone(tmp_path, monkeypatch, broken_parent):
    def spy(**kwargs):
        return ["译文"], [], {"input_tokens": 1, "output_tokens": 1}, {}, {}

    cached = [{"id": 0, "start": 0.0, "end": 1.0, "text": "one", "zh": "[翻译失败]"}]
    with pytest.raises(_Exited):
        _run_translate(tmp_path, monkeypatch, config={"_partial_retry": True}, spy=spy, cached=cached)
    assert broken_parent == [1]


def test_retry_progress_report_exits_when_main_program_is_gone(tmp_path, monkeypatch, broken_parent):
    def spy(**kwargs):
        kwargs["progress_callback"](96, "正在重试…")
        return ["译文"], [], None, {}, {}

    with pytest.raises(_Exited):
        _run_translate(tmp_path, monkeypatch, config={"_partial_retry": True}, spy=spy)
    assert broken_parent == [1]


def test_transcription_progress_report_exits_when_main_program_is_gone(tmp_path, monkeypatch, broken_parent):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr("backend.core.extractor.get_video_duration", lambda path: 120.0)

    class _ProgressSwallowed(Exception):
        pass

    def fake_transcribe(*args, **kwargs):
        kwargs["progress_callback"]({"completed_segments": 1, "processed_duration": 10.0})
        raise _ProgressSwallowed()  # 走到这里 = 识别进度写不出去却没退出

    monkeypatch.setattr(worker, "transcribe_v2", fake_transcribe)

    with pytest.raises(_Exited):
        worker._step_transcribe(
            "start", str(audio), tmp_path / "transcript.json", "large-v2", False, "standard",
            tmp_path, {}, _Ck(),
        )
    assert broken_parent == [1]


def test_progress_goes_through_the_single_exit(monkeypatch):
    sent = []
    monkeypatch.setattr(worker, "emit", lambda event_type, **kw: sent.append((event_type, kw)))
    worker.stderr_progress("translate", 40, "x", retry_status="running")
    assert sent == [("progress", {"stage": "translate", "percent": 40, "message": "x",
                                  "detail": "", "retry_status": "running"})]


def test_only_one_place_writes_to_the_pipe():
    # A14-2：合同本身就是「搜代码可验」
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"_real_stdout\.write\(", source)) == 1


# ── P514：付费等待睡醒后先汇报再请求；汇报函数必填 ─────────────────────

class _Down:
    name = "down"


def test_after_waiting_it_reports_before_sending_the_next_paid_request(monkeypatch):
    requests = []
    reports = []

    def request(backend):
        requests.append(backend.name)
        raise ConnectionError("down")  # 会自己好的错：原地等

    def on_wait(kind, name, seconds):
        reports.append(seconds)
        if len(reports) == 2:
            raise _Exited()  # 第二次汇报（睡醒后）发现主程序没了

    with pytest.raises(_Exited):
        paid_request.call_paid([_Down()], request, on_wait=on_wait, sleep=lambda s: None)
    assert len(requests) == 1, "睡醒后汇报不出去，就不得再发付费请求"


def test_paid_request_requires_a_wait_reporter():
    with pytest.raises(TypeError):
        paid_request.call_paid([_Down()], lambda b: "ok")


@pytest.mark.parametrize("func,args", [
    (sentence_splitter.llm_split_segments, ([],)),
    (sentence_splitter._split_long_segments, ([], [])),
    (sentence_splitter._llm_batch_split, ([], None)),
])
def test_sentence_splitter_requires_a_wait_reporter(func, args):
    with pytest.raises(TypeError):
        func(*args, on_resume=None)


def test_translation_requester_refuses_a_missing_progress_callback():
    with pytest.raises(ValueError):
        orchestrator._paid_requester([_Down()], _Down(), None, 50)


# ── P514 A14-3：真进程——等待中主程序没了，之后零付费请求 ───────────────

_CHILD = r'''
import sys, time
from backend import worker
from backend.translation import paid_request

paid_request._wait = lambda s: time.sleep(0.5)   # 把 5 秒等待缩短，流程不变
counter = sys.argv[1]

class Down:
    name = "DeepSeek(x)"

def request(_backend):
    with open(counter, "a") as f:
        f.write("request\n")
    raise ConnectionError("down")

paid_request.call_paid([Down()], request, on_wait=worker._paid_wait_reporter("translate", 40))
'''


def test_real_worker_process_stops_paying_once_main_program_is_gone(tmp_path):
    import subprocess
    import sys

    counter = tmp_path / "requests.txt"
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(counter)],
        cwd=str(Path(worker.__file__).parent.parent),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    first = child.stdout.readline()          # 第一次「正在等待」汇报
    assert '"type": "progress"' in first
    child.stdout.close()                     # 主程序没了：管道断开
    code = child.wait(timeout=20)

    assert code == 1, "汇报写不出去应在出口里 os._exit(1)"
    assert counter.read_text().count("request") == 1, "睡醒后发现主程序没了，不得再发付费请求"


# ── P515：Mac 识别用自管的对齐模型；失败写出真实原因 ─────────────────────

class _MlxResult:
    text = "hello"
    segments = [{"text": "hello", "start": 0.0, "end": 0.5}]
    language = "English"


def _record_mlx(monkeypatch):
    from backend.core import qwen_asr_runner as runner
    seen = []

    def fake(audio, **kwargs):
        seen.append(kwargs)
        return _MlxResult()

    monkeypatch.setattr(runner, "_USE_MLX", True)
    monkeypatch.setattr(runner, "_mlx_transcribe", fake)
    monkeypatch.setattr("backend.model_registry.resolve_model_path",
                        lambda key, *a, **k: f"/managed/{key}")
    return runner, seen


def test_chunk_recognition_uses_the_managed_aligner(monkeypatch, tmp_path):
    runner, seen = _record_mlx(monkeypatch)
    monkeypatch.setattr(runner, "_extract_audio_chunk", lambda *a, **k: None)
    runner._transcribe_chunk("model", str(tmp_path / "a.wav"), 0.0, 10.0, "en")
    assert seen[0]["forced_aligner"] == "/managed/qwen_fa"


def test_whole_recognition_uses_the_managed_aligner(monkeypatch, tmp_path):
    runner, seen = _record_mlx(monkeypatch)
    runner._qwen_transcribe("model", str(tmp_path / "a.wav"), "en")
    assert seen[0]["forced_aligner"] == "/managed/qwen_fa"


def test_recognition_failure_logs_the_real_reason(monkeypatch, tmp_path, caplog):
    from backend.core import qwen_asr_runner as runner

    def boom(audio, **kwargs):
        raise RuntimeError("Failed to load MLX aligner backend: offline")

    monkeypatch.setattr(runner, "_USE_MLX", True)
    monkeypatch.setattr(runner, "_mlx_transcribe", boom)
    monkeypatch.setattr("backend.model_registry.resolve_model_path", lambda key, *a, **k: "/managed/x")
    with caplog.at_level("WARNING"):
        assert runner._qwen_transcribe("model", str(tmp_path / "a.wav"), "en") is None
    assert "RuntimeError" in caplog.text
    assert "Failed to load MLX aligner backend" in caplog.text


def test_mac_hands_the_managed_model_directory_to_the_recognizer(monkeypatch):
    # Z6：交「目录」而不是已加载的模型——识别包按目录加载，分词表也从同一目录读
    from backend.core import qwen_asr_runner as runner
    monkeypatch.setattr(runner, "_USE_MLX", True)
    monkeypatch.setattr("backend.model_registry.resolve_model_path", lambda key, *a, **k: f"/managed/{key}")

    assert runner._load_qwen_model() == "/managed/qwen_asr", "不得先自己加载成对象（否则分词表会去公共缓存找）"


# ── P519：断句 / 润色请求走各家自己的格式 ────────────────────────────────

class _FakeHTTP:
    """替身：截下真正要发出去的请求，回一份按官方格式写的回答。"""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def __call__(self, req, timeout=None):
        import io
        self.requests.append({
            "url": req.full_url,
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": json.loads(req.data.decode()),
        })
        body = json.dumps(self.reply).encode()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp(body)


_SYSTEM = "你是连接测试。只输出一个 JSON 对象，不要输出其他文字。"
_PROMPT = 'Return exactly this JSON object: {"ok": true}'


def _send(monkeypatch, backend, reply):
    fake = _FakeHTTP(reply)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    out = backend.translate_raw_json(_PROMPT, system_prompt=_SYSTEM)
    return out, fake.requests


def test_claude_json_request_uses_claudes_own_format(monkeypatch):
    from backend.translation.claude import ClaudeBackend

    reply = {"content": [{"type": "thinking", "thinking": ""},
                         {"type": "text", "text": '```json\n{"ok": true}\n```'}]}
    out, reqs = _send(monkeypatch, ClaudeBackend("fake-key", "claude-sonnet-4-6"), reply)

    assert json.loads(out) == {"ok": True}, "代码块外层要去掉；正文取第一块 text，不是第一块"
    req = reqs[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    assert req["headers"]["x-api-key"] == "fake-key"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    body = req["body"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["max_tokens"] == 8192
    assert set(body) == {"model", "max_tokens", "messages"}, "不带 temperature / response_format / system"
    [message] = body["messages"]
    assert message["role"] == "user"
    assert _SYSTEM in message["content"] and _PROMPT in message["content"], "背景说明并进正文"
    assert "JSON" in message["content"]


def test_gemini_json_request_uses_geminis_own_format(monkeypatch):
    from backend.translation.gemini import GeminiBackend

    reply = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    out, reqs = _send(monkeypatch, GeminiBackend("fake-key", "gemini-2.0-flash"), reply)

    assert json.loads(out) == {"ok": True}
    req = reqs[0]
    assert req["url"] == ("https://generativelanguage.googleapis.com/v1beta/"
                          "models/gemini-2.0-flash:generateContent")
    assert req["headers"]["x-goog-api-key"] == "fake-key"
    body = req["body"]
    assert set(body) == {"contents", "generationConfig"}
    text = body["contents"][0]["parts"][0]["text"]
    assert _SYSTEM in text and _PROMPT in text
    assert body["generationConfig"] == {
        "maxOutputTokens": 8192, "temperature": 0.1, "responseMimeType": "application/json",
    }


def test_claude_translation_reads_the_first_text_block(monkeypatch):
    # 翻译路共用同一处取正文：新模型第一块是思考内容
    from backend.translation.claude import ClaudeBackend
    data = {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "你好"}]}
    assert ClaudeBackend("k")._call_api_parse(data) == "你好"


_OPENAI_FORMAT = [
    ("backend.translation.gpt", "GPTBackend", ("k",), "https://api.openai.com/v1/chat/completions", {}),
    ("backend.translation.deepseek", "DeepSeekBackend", ("k",), "https://api.deepseek.com/v1/chat/completions",
     {"thinking": {"type": "disabled"}, "max_tokens": 8192}),
    ("backend.translation.doubao", "DoubaoBackend", ("k",), "https://ark.cn-beijing.volces.com/api/v3/chat/completions", {}),
    ("backend.translation.glm", "GLMBackend", ("k",), "https://open.bigmodel.cn/api/paas/v4/chat/completions", {}),
    ("backend.translation.custom", "CustomOpenAIBackend", ("k", "https://my.host/v1"), None, {}),
]


@pytest.mark.parametrize("module,cls,args,url,extra", _OPENAI_FORMAT)
def test_openai_format_json_request_is_unchanged(monkeypatch, module, cls, args, url, extra):
    # A19-2：逐字段与改前一致（含 system 消息、temperature 0.1、JSON 开关、DeepSeek 关思考）
    import importlib
    backend = getattr(importlib.import_module(module), cls)(*args)
    reply = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    out, reqs = _send(monkeypatch, backend, reply)

    assert out == '{"ok": true}'
    if url:
        assert reqs[0]["url"] == url
    assert reqs[0]["body"] == {
        "model": backend._model,
        "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": _PROMPT}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        **extra,
    }


# ── ⑦.5 R3（用户定）：识别完让识别包放掉它缓存的模型 ─────────────────────

@pytest.fixture
def recognizer_cache_spy(monkeypatch):
    from backend.core import qwen_asr_runner as runner
    from mlx_qwen3_asr.load_models import _ModelHolder
    cleared = []
    monkeypatch.setattr(_ModelHolder, "clear", classmethod(lambda cls: cleared.append(1)))
    monkeypatch.setattr(runner, "_USE_MLX", True)
    monkeypatch.setattr(runner, "_load_qwen_model", lambda: "/managed/qwen_asr")
    monkeypatch.setattr(runner, "_write_qwen_raw_diagnostic", lambda *a, **k: None)
    return runner, cleared


def test_whole_recognition_releases_the_recognizer_cache(recognizer_cache_spy, monkeypatch, tmp_path):
    runner, cleared = recognizer_cache_spy
    monkeypatch.setattr(runner, "is_qwen_asr_available", lambda: True)
    monkeypatch.setattr(runner, "_qwen_transcribe", lambda m, a, l: None)
    monkeypatch.setattr("backend.core.extractor.get_video_duration", lambda p: 10.0)
    runner.run_qwen_asr(str(tmp_path / "a.wav"), language="en")
    assert cleared == [1], "识别完必须放掉识别包缓存的模型（实测否则多占约 4.7 GB）"


def test_chunked_recognition_releases_the_recognizer_cache(recognizer_cache_spy, monkeypatch, tmp_path):
    runner, cleared = recognizer_cache_spy
    monkeypatch.setattr(runner, "_transcribe_chunk", lambda *a, **k: {
        "text": "hi", "raw_text": "hi", "raw_segments": [], "segments": [], "t_start": 0.0, "t_end": 10.0})
    runner._run_qwen_asr_chunked(str(tmp_path / "a.wav"), 10.0, language="en")
    assert cleared == [1]
