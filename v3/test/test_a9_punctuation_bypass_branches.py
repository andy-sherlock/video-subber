"""合同 A9：三条绕过挂标点的分支，正式词流也必须带标点。

修好匹配器之后有一个陷阱：**诊断路径会重跑挂标点、正式路径直接返回旧缓存**。
用旧缓存重跑会看到「诊断文件 774 个标点、警告归零」，而正式 text 仍是 0 个——
在有旧缓存的机器上验收会拿到假的「已通过」。提高合同版本让 transcript.json 作废、
强制重转录，**恰好把执行推进这条分支**。

三条分支（前两条在 _chunk_words 外面绕过它，第三条是进了函数里面提前返回）：
  ① :786-795  全缓存命中
  ② :800-808  模型加载失败 + 部分缓存
  ③ :588-590  旧格式 checkpoint（缺 raw_segments 键）

全部在自建临时目录里用假任务跑，不碰真实任务 1a1b9f8145。
"""

import json

import pytest

from backend.core import qwen_asr_runner


SENT_END = set(".!?。！？…")


def _count_sentence_punctuation(text):
    return sum(1 for ch in (text or "") if ch in SENT_END)


def _chunk(t_start, t_end, raw_text, tokens):
    """构造一块：raw_text 带标点，词级 token 吞掉了词内标点（真实故障形状）。"""
    return {
        "raw_text": raw_text,
        "raw_segments": [
            {"text": tok, "start": i * 0.5, "end": i * 0.5 + 0.4}
            for i, tok in enumerate(tokens)
        ],
        "text": " ".join(tokens),          # 旧缓存里存的是没挂标点的正文
        "segments": [                       # 旧缓存里存的是没挂标点的词
            {"text": tok, "start": t_start + i * 0.5, "end": t_start + i * 0.5 + 0.4}
            for i, tok in enumerate(tokens)
        ],
        "t_start": t_start,
        "t_end": t_end,
    }


def _two_stale_chunks():
    return [
        _chunk(0.0, 300.0, "out of ten twenty-five. This is fine.",
               ["out", "of", "ten", "twentyfive", "This", "is", "fine"]),
        _chunk(300.0, 600.0, "our whole plan—yes, we had one.",
               ["our", "whole", "planyes", "we", "had", "one"]),
    ]


def _formal_text(chunks):
    """把正式路径交出来的块拼成正文，用来断言标点在不在。"""
    return " ".join(c.get("text", "") for c in chunks)


def test_f1_full_cache_hit_still_delivers_punctuation(tmp_path, monkeypatch):
    """绕过①：十块全在缓存里，跳过转录，正式正文必须也带标点。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    time_chunks = [(0.0, 300.0), (300.0, 600.0)]
    (cache / "qwen_chunks.json").write_text(
        json.dumps({"time_chunks": [list(t) for t in time_chunks],
                    "results": _two_stale_chunks(), "done_idx": 2}),
        encoding="utf-8")

    monkeypatch.setattr(qwen_asr_runner, "_load_qwen_model",
                        lambda: pytest.fail("全缓存命中不该加载模型"))

    out = qwen_asr_runner._run_qwen_asr_chunked(
        str(tmp_path / "audio.wav"), 600.0,
        segments=None, language="en", cache_dir=str(cache))
    out = out.chunks   # P464：返回 QwenAsrOutcome，块在 .chunks 上

    assert _count_sentence_punctuation(_formal_text(out)) > 0, \
        "全缓存命中分支交出的正式正文仍然没有标点"


def test_f2_model_load_failure_with_partial_cache_still_delivers_punctuation(
        tmp_path, monkeypatch):
    """绕过②：部分缓存 + 模型加载失败，返回已有块时正式正文必须带标点。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    time_chunks = [(0.0, 300.0), (300.0, 600.0), (600.0, 900.0)]
    (cache / "qwen_chunks.json").write_text(
        json.dumps({"time_chunks": [list(t) for t in time_chunks],
                    "results": _two_stale_chunks(), "done_idx": 2}),
        encoding="utf-8")

    def boom():
        raise RuntimeError("模型加载失败（构造）")

    monkeypatch.setattr(qwen_asr_runner, "_load_qwen_model", boom)

    out = qwen_asr_runner._run_qwen_asr_chunked(
        str(tmp_path / "audio.wav"), 900.0,
        segments=None, language="en", cache_dir=str(cache))
    out = out.chunks   # P464：返回 QwenAsrOutcome，块在 .chunks 上

    assert _count_sentence_punctuation(_formal_text(out)) > 0, \
        "模型加载失败分支交出的正式正文仍然没有标点"


def test_f3_old_format_checkpoint_is_rejected_not_silently_used(tmp_path):
    """绕过③：缺 raw_segments 键的旧格式 checkpoint 必须被整份丢弃，
    不能被静默拿去用（那样会完全绕过挂标点）。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    time_chunks = [(0.0, 300.0)]
    stale = _two_stale_chunks()[0]
    stale.pop("raw_segments")
    stale.pop("raw_text")
    ckpt = cache / "qwen_chunks.json"
    ckpt.write_text(
        json.dumps({"time_chunks": [list(t) for t in time_chunks],
                    "results": [stale], "done_idx": 1}),
        encoding="utf-8")

    saved, resume_idx = qwen_asr_runner._load_qwen_chunk_ckpt(
        str(ckpt), time_chunks)

    assert (saved, resume_idx) == ([], 0), "旧格式 checkpoint 被静默采用了"


def test_f3b_chunk_without_raw_segments_reports_not_attached():
    """万一真有块走到 raw_segments is None，必须记为「没挂上」，不能算成功。"""
    words, attached = qwen_asr_runner.chunk_words_checked(
        {"segments": [{"text": "hi", "start": 0.0, "end": 0.4}],
         "t_start": 0.0, "t_end": 10.0})

    assert attached is False
    assert words == [{"text": "hi", "start": 0.0, "end": 0.4}]


def test_f4_resumed_run_refreshes_cached_chunks_too(tmp_path, monkeypatch):
    """第四个暴露点（施工时发现）：断点续跑正常跑完时，返回列表里
    **既有新转录的块、也有缓存来的旧块**，旧块同样绕过了挂标点。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    time_chunks = [(0.0, 300.0), (300.0, 600.0), (600.0, 900.0)]
    (cache / "qwen_chunks.json").write_text(
        json.dumps({"time_chunks": [list(t) for t in time_chunks],
                    "results": _two_stale_chunks(), "done_idx": 2}),
        encoding="utf-8")

    monkeypatch.setattr(qwen_asr_runner, "_load_qwen_model", lambda: object())
    monkeypatch.setattr(
        qwen_asr_runner, "_transcribe_chunk",
        lambda *a, **k: _chunk(600.0, 900.0, "a new block here.",
                               ["a", "new", "block", "here"]))

    out = qwen_asr_runner._run_qwen_asr_chunked(
        str(tmp_path / "audio.wav"), 900.0,
        segments=None, language="en", cache_dir=str(cache))
    out = out.chunks   # P464：返回 QwenAsrOutcome，块在 .chunks 上

    stale_part = " ".join(c.get("text", "") for c in out[:2])
    assert _count_sentence_punctuation(stale_part) > 0, \
        "续跑完成后，缓存来的旧块仍然没有标点"


def test_f5_chunk_without_raw_segments_is_counted_as_missing():
    """⑦.5 /review 读代码读出来的缺陷（测试没抓到）。

    `_refresh_chunk_delivery` 对 `raw_segments is None` 的块直接放行、
    **不设 `punctuation_attached` 字段**，于是 `count_chunks_missing_punctuation`
    把它算成「挂上了」，用户不会被告知——尽管这块完全绕过了挂标点。

    而该函数的 docstring 写着「这一点由 chunk_words_checked 记为『没挂上』」，
    **但这条分支根本没调 chunk_words_checked**。注释与代码不符。
    """
    bypassed = {"segments": [{"text": "hi", "start": 0.0, "end": 0.4}],
                "t_start": 0.0, "t_end": 10.0}

    refreshed = qwen_asr_runner._refresh_chunk_delivery([bypassed], "en")

    assert refreshed[0]["punctuation_attached"] is False
    assert qwen_asr_runner.count_chunks_missing_punctuation(refreshed) == 1


def test_f6_non_dict_chunk_is_not_silently_dropped(caplog):
    """⑦.5 测试专家发现：`if not isinstance(chunk, dict): continue` 把一整块
    字幕**静默丢掉**。改动前那行是 `[c for c in qwen_chunks if c.get(...)]`，
    非字典会抛 AttributeError 当场炸——**我把响亮的崩溃换成了无声丢失 5 分钟字幕**。
    至少要留下痕迹。
    """
    with caplog.at_level("WARNING", logger="qwen_asr_runner"):
        out = qwen_asr_runner._refresh_chunk_delivery(
            [_chunk(0.0, 300.0, "hi there.", ["hi", "there"]), "不是字典"], "en")

    assert len(out) == 1, "非字典块应当被剔除"
    assert "丢弃" in caplog.text or "非法" in caplog.text, \
        f"一整块字幕被丢掉却没有任何告警: {caplog.text!r}"
