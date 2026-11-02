"""G12 第三批 B · A-13：旧润色成品（A+ §15.4 + 执行顺序决定 37）。

润色提示词版本 1 → 2。普通运行遇到版本 1 的成品 → 重新润色（混入检查跑得到）；
「重试失败项 / 接受部分结果」遇到版本 1 的成品 → 用现有「重试被拒」出口停下，
提示「这个任务是旧版本做的，请重新处理」，不拿未润色原话悄悄继续；没有成品文件 → 照旧继续。
"""

import json

import pytest

from backend import worker
from backend.core import sentence_groups as sg


def _groups():
    texts = ["We went up the hill.", "It was a long walk."]
    words, breaks, clock = [], [], 0.0
    for text in texts:
        for token in text.split():
            words.append({"word": token, "text": token,
                          "start": round(clock, 3), "end": round(clock + 0.3, 3)})
            clock += 0.4
        breaks.append(len(words) - 1)
    return sg.group_qwen_raw_from_break_indices(
        [], " ".join(w["text"] for w in words), words,
        break_after_indices=breaks[:-1], language="en")


class _CountingRefiner:
    def __init__(self):
        self.calls = 0

    def translate_raw_json(self, user_prompt, system_prompt=None):
        self.calls += 1
        return json.dumps({"corrections": []})


def _write(path, groups, prompt_version):
    payload = worker._build_refined_payload(
        groups, source_fingerprint="", refiner_status="no_change", accepted_count=0)
    payload["prompt_version"] = prompt_version
    assert worker._write_refined_transcript(path, payload)


def test_refiner_prompt_version_is_2():
    assert worker._REFINER_PROMPT_VERSION == 2


def test_old_product_with_normal_run_refines_again(tmp_path):
    groups, path, llm = _groups(), tmp_path / "refined_transcript.json", _CountingRefiner()
    _write(path, groups, 1)
    worker._run_transcript_refiner([{"platform": "stub"}], groups, refined_path=path,
                                   llm_backend=llm, allow_refine=True)
    assert llm.calls > 0
    assert json.loads(path.read_text())["prompt_version"] == 2


def test_old_product_on_retry_stops_with_visible_message(tmp_path):
    groups, path, llm = _groups(), tmp_path / "refined_transcript.json", _CountingRefiner()
    _write(path, groups, 1)
    before = [g.text for g in groups]
    with pytest.raises(worker.PartialActionRejected) as info:
        worker._run_transcript_refiner([{"platform": "stub"}], groups, refined_path=path,
                                       llm_backend=llm, allow_refine=False)
    assert "这个任务是旧版本做的，请重新处理" in str(info.value)
    assert llm.calls == 0
    assert [g.text for g in groups] == before


def test_current_product_is_read_back(tmp_path):
    groups, path, llm = _groups(), tmp_path / "refined_transcript.json", _CountingRefiner()
    _write(path, groups, 2)
    worker._run_transcript_refiner([{"platform": "stub"}], groups, refined_path=path,
                                   llm_backend=llm, allow_refine=False)
    assert llm.calls == 0


def test_missing_product_on_retry_continues_unrefined(tmp_path):
    # 没填 Key 的用户从来没有润色成品：重试照旧继续，不许被当成旧版本拦下
    groups, path, llm = _groups(), tmp_path / "refined_transcript.json", _CountingRefiner()
    worker._run_transcript_refiner([], groups, refined_path=path,
                                   llm_backend=llm, allow_refine=False)
    assert llm.calls == 0
