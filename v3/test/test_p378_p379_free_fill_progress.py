"""P378+P379: 免费补全逐句进度回报（不再钉在 83% 像卡死）。

四必须项：
必1 进度不倒退——逐条进度单调不降、不低于开工值（消除 83%/79% 坐标冲突）。
必2 每失败项恰好结算一次——含"没有免费引擎"边界（不能整个 if 跳过一次不发）。
必3 节流只节流界面——item_result（落盘/结算）每句都调，不被节流。
必4 成功才落盘——由 worker item_result 的 flush 条件保证（此处单测 _fill_gaps 的逐条回调契约）。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import FAILED_TRANSLATION, TranslationResult
from backend.translation.orchestrator import _fill_gaps_with_free


class _FreeBackend:
    """假免费引擎：translations 里有 source→中文译文即成功，缺失→空串即失败。"""

    def __init__(self, name, translations):
        self.name = name
        self.translations = translations

    def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
        out = [self.translations.get(t, "") for t in texts]
        return TranslationResult(translated=out, backend_name=self.name)


_TEXTS = ["the first line here", "the second line here", "the third line here"]
_ZH = {
    "the first line here": "这是第一行",
    "the second line here": "这是第二行",
    "the third line here": "这是第三行",
}


def test_reports_progress_per_item_monotonic():
    """必1：逐条进度——不止开工一次，且单调不降、不低于开工的 96。"""
    translated = [FAILED_TRANSLATION] * 3
    failed = [0, 1, 2]
    backend = _FreeBackend("google", _ZH)
    pcts = []
    _fill_gaps_with_free(_TEXTS, translated, failed, [backend],
                         lambda pct, msg: pcts.append(pct))
    assert len(pcts) >= 2, f"逐条应多次报进度，实际只有 {pcts}"
    assert pcts == sorted(pcts), f"进度必须单调不降，实际 {pcts}"
    assert all(p >= 96 for p in pcts), f"进度不得低于开工值 96，实际 {pcts}"


def test_no_backend_settles_each_item_once():
    """必2 边界：没有免费引擎时，每个失败项仍恰好结算一次（success=False）。"""
    translated = [FAILED_TRANSLATION] * 3
    failed = [0, 1, 2]
    calls = []
    _fill_gaps_with_free(_TEXTS, translated, failed, [], None,
                         lambda idx, text, success, isf: calls.append((idx, success)))
    assert sorted(i for i, _ in calls) == [0, 1, 2], f"每项恰好一次，实际 {calls}"
    assert all(success is False for _, success in calls)


def test_partial_success_settles_and_updates():
    """必2：部分成功——每项恰好一次，成功项写入 translated、失败项保留。"""
    translated = [FAILED_TRANSLATION] * 2
    failed = [0, 1]
    backend = _FreeBackend("google", {"the first line here": "这是第一行"})  # 第二句缺失=失败
    calls = []
    _fill_gaps_with_free(_TEXTS[:2], translated, failed, [backend], None,
                         lambda idx, text, success, isf: calls.append((idx, success)))
    assert translated[0] == "这是第一行"
    assert translated[1] == FAILED_TRANSLATION
    assert dict(calls) == {0: True, 1: False}
    assert len(calls) == 2


def test_item_result_not_throttled():
    """必3：20 项瞬间处理，item_result 每项都被调（落盘不因节流丢句）。"""
    n = 20
    texts = [f"line number {i} here" for i in range(n)]
    zh = {t: f"第{i}行" for i, t in enumerate(texts)}
    translated = [FAILED_TRANSLATION] * n
    failed = list(range(n))
    backend = _FreeBackend("google", zh)
    idxs = []
    _fill_gaps_with_free(texts, translated, failed, [backend], None,
                         lambda idx, text, success, isf: idxs.append(idx))
    assert sorted(idxs) == list(range(n)), "每项恰好一次，无遗漏"


if __name__ == "__main__":
    import unittest
    unittest.main()
