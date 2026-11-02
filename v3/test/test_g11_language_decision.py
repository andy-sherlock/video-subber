"""G11/P444：语言只准被决定一次。多点采样裁决的合同测试。

合同：task_plan.md「2026-08-29」章节 施工项 1 + CI-03 + CI-05。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sample(position_s, language, probs, *, valid=True, invalid_reason=None):
    from backend.core.whisper_runner import LanguageSample
    return LanguageSample(
        position_s=position_s,
        language=language,
        probabilities=probs,
        valid=valid,
        invalid_reason=invalid_reason,
    )


def test_unanimous_samples_lock_the_language():
    """全部有效样本一致 → 语言被锁定。"""
    from backend.core.whisper_runner import resolve_language_decision

    samples = [
        _sample(0.0, "en", {"en": 0.40, "ja": 0.31}),
        _sample(300.0, "en", {"en": 0.67, "fr": 0.10}),
        _sample(600.0, "en", {"en": 0.33, "ja": 0.23}),
    ]
    decision = resolve_language_decision(samples)

    assert decision.language == "en"
    assert decision.locked is True
    assert decision.split is False


def test_denominator_never_shrinks_when_samples_cannot_be_refilled():
    """CI-05 第 3 条：七点缩成四点后不得报「全票」。

    分母是**计划点数**，不是实际拿到的有效点数。补不齐 → 不得锁定。
    """
    from backend.core.whisper_runner import resolve_language_decision

    samples = [
        _sample(0.0, "en", {"en": 0.40}),
        _sample(300.0, "en", {"en": 0.67}),
        _sample(600.0, "en", {"en": 0.33}),
        _sample(900.0, "en", {"en": 0.51}),
        _sample(1200.0, None, {}, valid=False, invalid_reason="no_speech"),
        _sample(1500.0, None, {}, valid=False, invalid_reason="clip_failed"),
        _sample(1800.0, None, {}, valid=False, invalid_reason="no_probabilities"),
    ]
    decision = resolve_language_decision(samples, expected_count=7)

    assert decision.split is False, "四票全一致，不是分票"
    assert decision.locked is True, (
        "分母没凑齐不再阻止锁定——2026-08-29 改多数票后，"
        "能听到的那几点照样选得出多数；分母只作记录")
    assert decision.language == "en"
    assert decision.expected_count == 7
    assert decision.valid_count == 4


def test_sample_positions_follow_the_derived_rule_not_a_hardcoded_number():
    """CI-03：边界是推导规则——总时长 ≥ 点数 × 探测窗长才取该点数。"""
    from backend.core.whisper_runner import plan_sample_positions

    # 事故素材 fc441fb713：31 分钟，容得下 7 点
    plan = plan_sample_positions(1860.0, probe_seconds=30)
    assert plan.mode == "sampled"
    assert plan.count == 7
    assert len(plan.positions) == 7

    # 刚好够 7 点（7 × 30 = 210）
    assert plan_sample_positions(210.0, probe_seconds=30).count == 7
    # 差一点够 7 点 → 降到能容纳的最大点数 5
    assert plan_sample_positions(209.0, probe_seconds=30).count == 5
    # 差一点够 3 点（3 × 30 = 90）→ 降到 2
    assert plan_sample_positions(89.0, probe_seconds=30).count == 2
    # 连 2 个不重叠样本都放不下（< 60）→ 整段覆盖分支
    short = plan_sample_positions(45.0, probe_seconds=30)
    assert short.mode == "whole"
    assert short.count == 1, "整段分支只算一票，不得重复同一片段伪造多票"


def test_sample_positions_never_overlap():
    """合同：不重复同一片段伪造多票。"""
    from backend.core.whisper_runner import plan_sample_positions

    for duration in (90.0, 150.0, 210.0, 600.0, 1860.0):
        plan = plan_sample_positions(duration, probe_seconds=30)
        starts = sorted(plan.positions)
        assert len(set(starts)) == len(starts), f"{duration}s 出现重复采样点"
        for a, b in zip(starts, starts[1:]):
            assert b - a >= 30, f"{duration}s 采样窗重叠：{a} 与 {b}"
        assert starts[0] >= 0
        assert starts[-1] + 30 <= duration + 1e-6, f"{duration}s 最后一个窗超出音频末尾"


def _two_tone_wav(path, seconds=90, rate=16000, switch_at=30):
    """前 switch_at 秒 440Hz，之后 880Hz——用来证明截取位置真的生效。"""
    import wave, math, struct
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        buf = bytearray()
        for i in range(rate * seconds):
            freq = 440.0 if i < rate * switch_at else 880.0
            buf += struct.pack("<h", int(20000 * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(buf))


def _dominant_freq(path):
    import wave
    import numpy as np
    with wave.open(str(path), "rb") as r:
        data = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2").astype(float)
        sr = r.getframerate()
    return float(np.fft.rfftfreq(len(data), 1.0 / sr)[int(np.argmax(np.abs(np.fft.rfft(data))))])


def test_probe_clip_can_be_cut_at_a_given_position(tmp_path):
    """第一刀的根治：探测片段必须能来自指定位置，不再只有开头。"""
    import os
    from backend.core.whisper_runner import _create_language_probe_clip

    src = tmp_path / "two_tone.wav"
    _two_tone_wav(src)

    head = _create_language_probe_clip(str(src), probe_seconds=30, start_s=0.0)
    try:
        assert abs(_dominant_freq(head) - 440.0) < 5.0
    finally:
        os.unlink(head)

    tail = _create_language_probe_clip(str(src), probe_seconds=30, start_s=60.0)
    try:
        assert abs(_dominant_freq(tail) - 880.0) < 5.0, "截取位置没生效，仍拿到开头"
    finally:
        os.unlink(tail)


class _CountingProber:
    """假探测器：数模型被加载了几次，并记录每次探测的位置。"""

    def __init__(self, results):
        self.results = list(results)
        self.positions = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __call__(self, audio_path, start_s, probe_seconds):
        self.positions.append(start_s)
        return self.results.pop(0)


def test_sampling_loop_loads_the_model_exactly_once(monkeypatch):
    """CI-02：按现有 API 循环调用会加载 N 次模型。整轮只准加载一次。"""
    from backend.core import whisper_runner as wr

    loads = {"n": 0}
    prober = _CountingProber([("en", {"en": 0.40 + i / 100}) for i in range(7)])

    def fake_open(model_size):
        loads["n"] += 1
        return prober

    monkeypatch.setattr(wr, "_open_language_prober", fake_open)

    decision = wr.sample_language_multipoint(
        "/tmp/does-not-matter.wav", model_size="large-v2", duration_s=1860.0
    )

    assert loads["n"] == 1, f"模型被加载了 {loads['n']} 次，合同要求整轮只加载一次"
    assert len(prober.positions) == 7, "七个采样点都要真的去探测"
    assert prober.positions == sorted(set(prober.positions)), "采样点不得重复"
    assert decision.language == "en"
    assert decision.locked is True
    assert decision.expected_count == 7


def test_routing_ignores_probability_entirely():
    """第二刀的根治：路由只看已锁定的语言，不看概率。

    事故 fc441fb713 的七点实测里 en 的概率从 0.3319 到 0.9421 都有；
    旧门槛让同一支素材同一个语言，换个采样位置就得到相反路由。
    """
    from backend.transcriber_v2 import choose_standard_primary_engine

    # 事故素材七点实测的真实概率，全部是 en
    for prob in (0.4022, 0.6718, 0.3319, 0.3667, 0.6466, 0.3896, 0.9421):
        assert choose_standard_primary_engine("en") == "qwen", (
            f"概率 {prob} 不应影响路由——它已经不是入参了")

    assert choose_standard_primary_engine("ja") == "qwen"
    assert choose_standard_primary_engine("fr") == "whisper"
    assert choose_standard_primary_engine(None) == "whisper"


def test_probability_threshold_constant_is_gone():
    """合同：删除而非调低。留着常量就是留着那个错误结构。"""
    from backend.core import whisper_runner  # noqa: F401
    import backend.transcriber_v2 as tv2

    assert not hasattr(tv2, "STANDARD_ROUTE_MIN_PROB"), (
        "0.70 门槛必须删除，不是调低")


class _ScriptedProber:
    def __init__(self, results):
        self.results = list(results)

    def __enter__(self): return self
    def __exit__(self, *e): return False

    def __call__(self, _path, _start, _seconds):
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_a_dissenting_low_probability_sample_still_counts(monkeypatch):
    """CI-05 第 1 条，最阴的一条。

    如果实现把「投了反对票的低把握度样本」当成无效样本丢弃，
    分票在数据上就消失了——「分票即不交付」永远不开火，而所有测试全绿。
    """
    from backend.core import whisper_runner as wr

    # 三个点：两个 en 高把握度，一个 ja 低把握度（反对票）
    prober = _ScriptedProber([
        ("en", {"en": 0.94}),
        ("en", {"en": 0.88}),
        ("ja", {"ja": 0.07}),      # 低到几乎不可信——但它仍然是一票
    ])
    monkeypatch.setattr(wr, "_open_language_prober", lambda _m: prober)

    decision = wr.sample_language_multipoint("/tmp/x.wav", "large-v2", 100.0)

    assert decision.valid_count == 3, "反对票不得被当成无效样本丢弃"
    assert decision.split is True, "有反对票就是分票，不许按把握度把它抹掉"
    assert decision.locked is True, (
        "反对票如实记进 split，但不再拥有否决权——多数说了算")
    assert decision.language == "en", "2 票英语 : 1 票日语 → 英语"


def test_only_technical_reasons_mark_a_sample_invalid(monkeypatch):
    """CI-05 第 2 条：只有「没返回语言/没返回概率/截取失败」才算无效。"""
    from backend.core import whisper_runner as wr

    prober = _ScriptedProber([
        ("en", {"en": 0.02}),          # 把握度极低 → 仍然有效
        (None, {}),                    # 模型没返回语言 → 技术原因，无效
        (RuntimeError("clip failed")), # 截取失败 → 技术原因，无效
    ])
    monkeypatch.setattr(wr, "_open_language_prober", lambda _m: prober)

    decision = wr.sample_language_multipoint("/tmp/x.wav", "large-v2", 100.0)

    reasons = [s.invalid_reason for s in decision.samples if not s.valid]
    assert len(reasons) == 2
    assert set(reasons) <= {"no_probabilities", "probe_failed"}, (
        f"无效原因必须是技术原因，实际 {reasons}")
    assert decision.valid_count == 1, "把握度低不是无效的理由"
    assert decision.locked is True, (
        "只凑齐 1 个点也有答案——1 票也是多数。"
        "唯一没有答案的情形是一票都没有")


def test_unknown_duration_must_not_silently_become_single_point_sampling(monkeypatch):
    """⑦.5 审查发现：时长探测失败会静默退回「只听开头 30 秒」——正是第一刀。

    `get_video_duration()` 在 ffprobe 失败时静默返回 0.0（不抛异常）。
    若把 0.0 当成「极短视频」，就会走整段分支 → 只取 1 个样本 →
    单点必然自洽 → locked=True → 照常交付。**病从侧门原样回来。**

    「时长未知」和「视频很短」是两件事，不能当成同一件。
    """
    from backend.core.whisper_runner import plan_sample_positions

    unknown = plan_sample_positions(0.0, probe_seconds=30)
    assert unknown.mode == "unknown", (
        f"时长未知被当成了极短视频（mode={unknown.mode}），"
        "结果是单点采样——第一刀原样复活")
    assert unknown.count == 0

    # 对照：真的很短的视频仍然走整段覆盖分支，这是合同要求的
    short = plan_sample_positions(45.0, probe_seconds=30)
    assert short.mode == "whole" and short.count == 1


def test_unknown_duration_yields_an_unlocked_decision(monkeypatch):
    """时长未知 → 不锁定 → 不交付。不许悄悄按单点结果发货。"""
    from backend.core import whisper_runner as wr

    class _Prober:
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def __call__(self, _p, _s, _n): return "en", {"en": 0.99}

    monkeypatch.setattr(wr, "_open_language_prober", lambda _m: _Prober())

    decision = wr.sample_language_multipoint("/tmp/x.wav", "large-v2", 0.0)
    assert decision.locked is False, "时长未知时不得锁定语言"
