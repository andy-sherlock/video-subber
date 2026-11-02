"""G11 方向变更（2026-08-29）：分票不再是失败，**多数说了算**。

推翻的旧规则：任何一个采样点意见不同 → 整个任务失败。
推翻的理由：多点采样存在的目的就是不让单点误判说了算；
让单点拥有否决权，等于把要防的东西请回来当裁判。

唯一还没有答案的情况：**一票都没有**——听不到任何东西，
说明这个文件我们根本读不了。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sample(position_s, language, valid=True):
    from backend.core.whisper_runner import LanguageSample
    return LanguageSample(
        position_s, language,
        {language: 0.9} if language else {},
        valid=valid,
        invalid_reason=None if valid else "probe_failed")


def test_six_to_one_majority_wins():
    """七点里六点听到英语、一点听到日语 → 答案是英语，可以交付。

    这就是用户给的原始判据：七段里六段说英文、一段说日文，按英文处理。
    """
    from backend.core.whisper_runner import resolve_language_decision

    samples = [_sample(i * 100.0, "en") for i in range(6)]
    samples.append(_sample(600.0, "ja"))
    decision = resolve_language_decision(samples, expected_count=7, backend="mlx")

    assert decision.language == "en"
    assert decision.locked is True


def test_split_is_recorded_but_never_fatal():
    """分歧仍要如实记进证据（柜子背面也要做好），但它不再阻止交付。"""
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision(
        [_sample(0.0, "en"), _sample(50.0, "en"), _sample(100.0, "ja")],
        expected_count=3, backend="mlx")

    assert decision.split is True          # 记下来
    assert decision.locked is True         # 但不致命
    assert decision.language == "en"


def test_unanimous_still_locks():
    """全票一致的正常路径不受影响。"""
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision(
        [_sample(0.0, "en"), _sample(50.0, "en"), _sample(100.0, "en")],
        expected_count=3)

    assert decision.language == "en"
    assert decision.locked is True
    assert decision.split is False


def test_incomplete_denominator_no_longer_blocks():
    """凑不齐计划点数不再是失败——能听到的那几点照样能选出多数。

    旧规则要求分母凑齐，否则拒绝锁定。那条规则的存在理由是
    「不许把七点缩成四点后仍称全票」；改成多数票之后「全票」这个概念
    本身就没了，这条约束跟着消失。
    """
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision(
        [_sample(0.0, "en"), _sample(100.0, "en"),
         _sample(200.0, None, valid=False)],
        expected_count=7)

    assert decision.language == "en"
    assert decision.locked is True
    assert decision.valid_count == 2
    assert decision.expected_count == 7


def test_no_samples_at_all_is_not_locked():
    """零采样点（读不出时长）→ 没有答案。这是唯一剩下的失败情形。"""
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision([], expected_count=0)

    assert decision.language is None
    assert decision.locked is False


def test_all_probes_failed_is_not_locked():
    """采样点都在，但一个都没听出东西 → 同样没有答案。"""
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision(
        [_sample(0.0, None, valid=False), _sample(100.0, None, valid=False)],
        expected_count=2)

    assert decision.language is None
    assert decision.locked is False


def test_tie_goes_to_the_earliest_language():
    """平票 → 取最早出现的那一种。

    七/五/三都是奇数，两种语言之间不可能平票；只有无效样本把有效数
    拉成偶数、或出现三种以上语言时才会走到这里。规则必须确定，
    不能依赖字典顺序。
    """
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision(
        [_sample(0.0, "en"), _sample(100.0, "ja")], expected_count=2)

    assert decision.language == "en"
    assert decision.locked is True


def test_valid_sample_without_language_does_not_vote():
    """标为有效但没听出语言的点，不投票，也不算分歧。"""
    from backend.core.whisper_runner import resolve_language_decision

    decision = resolve_language_decision(
        [_sample(0.0, "en"), _sample(100.0, None)], expected_count=2)

    assert decision.language == "en"
    assert decision.split is False
    assert decision.valid_count == 1


def test_decision_remembers_which_backend_produced_it(monkeypatch):
    """决定必须记住是哪个引擎判的：Mac 的 MLX，还是 Windows 的 faster-whisper。

    原理由（分票率不能把两个系统混算成一个平均数）随影子观测一起删了，
    但字段本身仍然是回溯证据：出了错要知道当时是哪个引擎在听。
    """
    from backend.core import whisper_runner as wr

    class _Prober:
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def __call__(self, _p, _s, _n): return "en", {"en": 0.9}

    monkeypatch.setattr(wr, "_open_language_prober", lambda _m: _Prober())
    monkeypatch.setattr(wr, "choose_whisper_backend", lambda: "faster")

    decision = wr.sample_language_multipoint("/tmp/x.wav", "large-v2", 1860.0)
    assert decision.backend == "faster", "决定必须记住是哪个后端产生的"
