"""R2（2026-11-09，方案 5.4）：「这句算不算翻好了」只有一份规则。

以前两份：worker 的 `_is_valid_saved_translation`（空白、「[翻译失败]」都算没翻好）；
main.py `_snapshot_from_translated_rows` 只认「[翻译失败]」。现在规则原样搬进
`backend/job_state.py`，两处都调用它。规则一个字不改。
"""

from backend import job_state, main, worker

SAMPLES = ["你好，世界", "[翻译失败]", "", "   ", " [翻译失败] ", None, 123]


def test_rule_unchanged():
    """规则本身一个字不改：只有「去掉首尾空白后非空、且不是 [翻译失败]」的字符串算翻好了。"""
    assert [job_state.is_valid_saved_translation(v) for v in SAMPLES] == [
        True, False, False, False, False, False, False,
    ]


def test_worker_uses_the_shared_rule():
    assert worker._is_valid_saved_translation is job_state.is_valid_saved_translation


def test_main_counts_failed_rows_with_the_same_rule():
    """同一组输入喂给两处调用方，判断完全一样。"""
    rows = [{"id": i, "start": float(i), "end": i + 0.5, "zh": v} for i, v in enumerate(SAMPLES)]
    snapshot = main._snapshot_from_translated_rows(rows, {})
    failed_by_main = [key[0] for key in snapshot["failed_keys"]]
    failed_by_worker = [i for i, v in enumerate(SAMPLES) if not worker._is_valid_saved_translation(v)]
    assert failed_by_main == failed_by_worker
    assert snapshot["failed_count"] == len(failed_by_worker)
    assert snapshot["success_count"] == len(SAMPLES) - len(failed_by_worker)
