"""G12 第三批 B · L-17：翻译结果统一转简体（排版方案 §13.3）。

真实问题：新西兰片 31:26 译文出现「開心」。翻译回来之后、第 22 件断句之前统一转一次；
函数带「目标写法」参数（本批只传简体）。转换包加载失败 → 原文不动、只记一次日志、任务不失败。
"""

import logging

from backend.core import zh_script


class _Seg:
    def __init__(self, sid, zh):
        self.id, self.zh = sid, zh


def test_traditional_characters_become_simplified():
    assert zh_script.to_script("但我很開心，這裡的風景真美") == "但我很开心，这里的风景真美"


def test_simplified_text_is_left_alone():
    text = "总的来说，我们运气非常好。《指环王》里的皇后镇著名景点。"
    assert zh_script.to_script(text) == text


def test_segments_and_pre_splits_are_both_converted():
    segs = [_Seg(1, "我很開心"), _Seg(2, "[翻译失败]")]
    splits = {1: ["我很", "開心"]}
    zh_script.convert_segments(segs, splits, target="zh-hans")
    assert [s.zh for s in segs] == ["我很开心", "[翻译失败]"]
    assert splits == {1: ["我很", "开心"]}


def test_load_failure_keeps_text_and_logs_once(monkeypatch, caplog):
    def _boom():
        raise ImportError("no zhconv")

    monkeypatch.setattr(zh_script, "_converter", None)
    monkeypatch.setattr(zh_script, "_load_failed", False)
    monkeypatch.setattr(zh_script, "_import_zhconv", _boom)
    segs = [_Seg(1, "我很開心"), _Seg(2, "這裡")]
    with caplog.at_level(logging.WARNING):
        zh_script.convert_segments(segs, {}, target="zh-hans")
        zh_script.convert_segments(segs, {}, target="zh-hans")
    assert [s.zh for s in segs] == ["我很開心", "這裡"]
    assert caplog.text.count("繁简转换不可用") == 1


def test_subtitle_step_converts_before_sentence_split(monkeypatch, tmp_path):
    """生产调用点：_step_subtitle 在 llm_split_segments 之前转写法。"""
    from backend import worker

    seen = {}

    def _fake_split(segments, llm_backend=None, pre_splits=None, on_wait=None, on_resume=None):
        seen["zh"] = [s.zh for s in segments]
        seen["splits"] = pre_splits
        return segments

    monkeypatch.setattr(worker, "llm_split_segments", _fake_split)
    monkeypatch.setattr(worker, "build_display_timeline", lambda segs, report=None: [])
    monkeypatch.setattr(worker, "build_srt", lambda entries, path: None)
    segs = [_Seg(1, "我很開心")]
    worker._step_subtitle(segs, {}, {1: ["我很", "開心"]}, tmp_path / "a.srt",
                          tmp_path / "a.ass", False, "no", None, 1920, 1080)
    assert seen["zh"] == ["我很开心"]
    assert seen["splits"] == {1: ["我很", "开心"]}
