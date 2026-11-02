"""D-1 补修（2026-10-02）干活程序（worker）合同测试。

A4  换 Key（含换另一家 AI）继续，不删已翻好的句子
D2  「保留未翻译字幕并生成文件」本轮不用付费 AI
D1  汇报写不出去（主程序没了）立即结束整个进程——含翻译线程池子线程
"""

import threading

import pytest

from backend import worker


# ── A4：Key 变化不删翻译缓存 ─────────────────────────────────────────

def _caches(tmp_path):
    translated = tmp_path / "translated.json"
    translated.write_text("[]", encoding="utf-8")
    stage_cache = {stage: [] for stage in worker.STAGE_ORDER}
    stage_cache["translate"] = [str(translated)]
    return translated, stage_cache


class _Ck:
    def __init__(self):
        self.saved = []

    def save(self, stage, meta=None):
        self.saved.append(stage)


def test_api_keys_are_not_a_cache_invalidating_setting():
    assert "apiKeys" not in worker.SETTING_STAGE


def test_switching_api_keys_keeps_translated_sentences(tmp_path):
    translated, stage_cache = _caches(tmp_path)
    ck = _Ck()
    old = {"apiKeys": [{"platform": "deepseek", "key": "fake-a"}], "model": "large-v2"}
    new = {"apiKeys": [{"platform": "gpt", "key": "fake-b"}], "model": "large-v2"}

    stage = worker._invalidate_stale_caches(
        "subtitle", old, new, worker.SETTING_STAGE, worker.STAGE_ORDER, stage_cache, ck)

    assert translated.exists(), "换 Key 继续必须保留用户已付费翻好的句子"
    assert stage == "subtitle"
    assert ck.saved == []


def test_other_translate_settings_still_invalidate(tmp_path):
    translated, stage_cache = _caches(tmp_path)
    old = {"translationMode": "speed"}
    new = {"translationMode": "quality"}

    worker._invalidate_stale_caches(
        "subtitle", old, new, worker.SETTING_STAGE, worker.STAGE_ORDER, stage_cache, _Ck())

    assert not translated.exists(), "翻译方式变了照旧要重翻（不在本批改动范围）"


def test_partial_retry_still_ignores_free_translation_order(tmp_path):
    translated, stage_cache = _caches(tmp_path)
    old = {"translationPriority": ["google"]}
    new = {"translationPriority": ["bing"], "_partial_retry": True}

    worker._invalidate_stale_caches(
        "subtitle", old, new, worker.SETTING_STAGE, worker.STAGE_ORDER, stage_cache, _Ck())

    assert translated.exists()


# ── D2：导出本轮不用付费 AI ──────────────────────────────────────────

def test_accept_partial_runs_without_paid_keys():
    config = {"apiKeys": [{"platform": "deepseek", "key": "fake-a"}],
              "_accept_partial": True}

    assert worker._api_keys_for_run(config) == []


def test_normal_runs_use_configured_keys():
    keys = [{"platform": "deepseek", "key": "fake-a"}]

    assert worker._api_keys_for_run({"apiKeys": keys}) == keys
    assert worker._api_keys_for_run({"apiKeys": keys, "_partial_retry": True}) == keys
    assert worker._api_keys_for_run({}) == []


# ── D1：汇报写不出去即退出 ───────────────────────────────────────────

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
    return codes


def test_emit_exits_when_the_main_program_is_gone(broken_parent):
    with pytest.raises(_Exited):
        worker.emit("progress", message="x")

    assert len(broken_parent) == 1


def test_progress_exits_when_the_main_program_is_gone(broken_parent):
    with pytest.raises(_Exited):
        worker.stderr_progress("translate", 40, "正在等待连接 DeepSeek")

    assert len(broken_parent) == 1


def test_progress_from_a_worker_thread_ends_the_whole_process(broken_parent):
    """原地等待的汇报在翻译线程池子线程里发出；退出必须对整个进程有效。"""
    errors = []

    def wait_reporter():
        try:
            worker._paid_wait_reporter("translate", 40)("connection", "deepseek", 180)
        except _Exited:
            errors.append("exited")

    t = threading.Thread(target=wait_reporter)
    t.start()
    t.join(5)

    assert broken_parent, "子线程汇报写不出去时没有调用 os._exit"
    assert errors == ["exited"]


# ── 终审增量 P516 的一行修法已被第二批 P516 取代：名字不再单独存、不再改名，
#    成品名从原视频地址现算（见 test_d1c_batch2_p516.py：continuing_never_renames、
#    output_name_comes_from_the_address）。
