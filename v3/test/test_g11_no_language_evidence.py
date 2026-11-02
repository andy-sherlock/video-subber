"""G11（2026-08-29 方向变更后）：唯一剩下的语言失败情形——**一票都没有**。

旧规则「任何分歧即失败」已删。分票现在取多数，用户不会知道我们投了几票。
这里只覆盖真正听不了的文件：坏的是他手里的东西，只有他能换一个，所以必须开口。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_worker(monkeypatch):
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    return _load_worker_without_audio_models(monkeypatch)


def _no_evidence_decision():
    """零采样点：读不出时长时 plan_sample_positions 返回 mode="unknown"。"""
    from backend.core.whisper_runner import LanguageDecision
    return LanguageDecision(
        language=None, locked=False, split=False,
        expected_count=0, valid_count=0, samples=(), backend="mlx")


def test_no_evidence_becomes_an_error_terminal_without_a_transcript(
        monkeypatch, tmp_path):
    """不写正式 transcript.json、不交付、进现有 error 终态并带明确 failure_kind。"""
    from backend.core.whisper_runner import LanguageEvidenceMissing
    worker = _load_worker(monkeypatch)

    emitted = []
    monkeypatch.setattr(
        worker, "emit",
        lambda event_type, **kw: emitted.append((event_type, kw)))

    cache = tmp_path / "cache"
    cache.mkdir()

    def boom(*_a, **_k):
        raise LanguageEvidenceMissing(_no_evidence_decision())

    monkeypatch.setattr(worker, "transcribe_v2", boom)

    import pytest
    with pytest.raises(BaseException):
        worker._step_transcribe(
            "transcribe", str(tmp_path / "a.wav"),
            str(cache / "transcript.json"), "large-v2",
            False, "standard", cache, {}, {})

    kinds = [kw.get("failure_kind") for et, kw in emitted if et == "error"]
    assert "language_evidence_missing" in kinds, (
        f"必须进 error 终态并带明确 failure_kind，实际 {emitted}")

    assert not (cache / "transcript.json").exists(), (
        "没有答案时不得写正式转录信封——半份信封会制造「有文件但不能用」的新状态")


def test_message_survives_the_public_text_filter(monkeypatch):
    """⑥ 安全审查：文案里出现路径 → 整句被换成「处理失败」，用户什么都看不到。"""
    from backend import job_state
    worker = _load_worker(monkeypatch)

    for message in (worker._LANGUAGE_EVIDENCE_MISSING_MESSAGE,
                    worker._ARTIFACT_MISMATCH_MESSAGE):
        assert job_state.safe_public_text(message) == message, (
            f"这句话会被安全过滤器整段丢掉，用户看不到任何原因：{message}")


def test_messages_carry_their_own_advice(monkeypatch):
    """文案必须自带一句用户能照做的建议。

    两个理由，缺一不可：
    ① 产品上——这是「必须开口」的情形，只说「失败了」等于没说；
    ② 工程上——前端 formatJobErrorText 见到已有建议就原样显示，
       否则会自己贴一句「请查看日志或重新提交任务」。对一个双语视频来说
       「重新提交」得到的是同一个结果，那是条死循环。
    """
    import re
    worker = _load_worker(monkeypatch)

    # 直接读前端那张表，而不是在这里抄一份——抄一份等于两处会各自漂移。
    html = (Path(__file__).resolve().parent.parent
            / "frontend" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"function hasUserAdvice\(raw\)\{\s*return \[([^\]]+)\]", html)
    assert match, "前端 hasUserAdvice 的关键词表找不到了，这条约定需要重新对齐"
    keywords = re.findall(r"'([^']+)'", match.group(1))
    assert keywords, "关键词表解析为空"

    for message in (worker._LANGUAGE_EVIDENCE_MISSING_MESSAGE,
                    worker._ARTIFACT_MISMATCH_MESSAGE):
        assert any(k in message for k in keywords), (
            f"这句话不含前端认得的任何建议关键词{keywords}，"
            f"前端会自己贴一句「请查看日志或重新提交任务」：{message}")


def test_messages_do_not_leak_internals(monkeypatch):
    """用户不需要知道我们内部投了几票、判定成了哪个语言代码、假名占几成。"""
    worker = _load_worker(monkeypatch)

    for message in (worker._LANGUAGE_EVIDENCE_MISSING_MESSAGE,
                    worker._ARTIFACT_MISMATCH_MESSAGE):
        for leak in ("分票", "采样", "票", "假名", "locked", "en", "ja", "%"):
            assert leak not in message, (
                f"文案泄露了内部实现（{leak!r}）：{message}")
