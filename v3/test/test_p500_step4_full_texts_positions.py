"""P500 施工 ④：翻译入口与 worker 两处入口加 `full_texts`（整片原文）、`positions`（要翻的句子在整片里的位置）。

方案 `pipelines/方案-P500-上下文感知改小批-2026-11-07.md` 第六节 ⓪、第七节 ④、5.2（外审#3，U6）：
- 逐段（context_aware=False，用户默认）：worker 传给翻译的参数与改前一字不变，不收不用这两样。
- 上下文感知：第一次翻 → full_texts = texts，positions = 0..n-1；
  中断后接着翻 / 人工重试失败句 → texts 只是剩下的句子，full_texts 是整片，positions 是它们在整片里的真实位置，
  这样施工 ⑤ 取「前 3 句、后 2 句」时拿到的是原片里真正相邻的句子，而不是筛选后拼在一起的句子。
"""

import json

import pytest

from backend import worker
from backend.translation import orchestrator as orch

TEXTS = ["zero", "one", "two", "three", "four"]

# 改动前 worker 传给 translate_with_orchestrator 的参数名，一个不多一个不少（a07b277）。
OLD_KWARGS = {
    "texts", "api_keys", "context_aware", "engine_status_callback",
    "item_result_callback", "translation_stats_callback",
    "progress_callback", "batch_progress_callback",
}


class _Ck:
    def save(self, *a, **k):
        pass


def _segments():
    return [worker.Segment(i, float(i), float(i) + 1, t) for i, t in enumerate(TEXTS)]


def _spy(captured):
    def spy(**kwargs):
        captured.append(kwargs)
        return ["好的译文"] * len(kwargs["texts"]), [], None, {}, {}
    return spy


def _run_step(tmp_path, monkeypatch, *, context_aware, mode):
    """mode: first（第一次翻）/ resume（快照里已有 1、3 句，接着翻）/ retry（人工重试失败的 0、2、4 句）。"""
    segments = _segments()
    translated_path = tmp_path / "translated.json"
    config = {}
    if mode == "resume":
        worker._write_translation_snapshot(
            translated_path.with_name(".snapshot.json"),
            {1: "第一句译文", 3: "第三句译文"},
            refined_fingerprint=worker._segments_text_fingerprint(segments),
        )
    if mode == "retry":
        translated_path.write_text(json.dumps([
            {"id": i, "start": float(i), "end": float(i) + 1, "text": t,
             "zh": "已有好译文" if i in (1, 3) else "[翻译失败]"}
            for i, t in enumerate(TEXTS)
        ], ensure_ascii=False), encoding="utf-8")
        config = {"_partial_retry": True, "_retry_initial_failed_count": 3}
    captured = []
    monkeypatch.setattr(orch, "translate_with_orchestrator", _spy(captured))
    worker._step_translate(
        "subtitle", segments, translated_path, [], context_aware, config,
        tmp_path, len(segments), _Ck(),
    )
    assert len(captured) == 1, "translate_with_orchestrator 没被调用，测试无效"
    return captured[0]


# ── worker：逐段一字不变 ──────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["first", "resume", "retry"])
def test_worker_per_batch_kwargs_unchanged(tmp_path, monkeypatch, mode):
    kwargs = _run_step(tmp_path, monkeypatch, context_aware=False, mode=mode)
    assert set(kwargs) == OLD_KWARGS


# ── worker：上下文感知把整片原文和真实位置交下去 ─────────────────────────

@pytest.mark.parametrize("mode, expected_positions", [
    ("first", [0, 1, 2, 3, 4]),
    ("resume", [0, 2, 4]),
    ("retry", [0, 2, 4]),
])
def test_worker_context_aware_passes_full_film_and_true_positions(
    tmp_path, monkeypatch, mode, expected_positions,
):
    kwargs = _run_step(tmp_path, monkeypatch, context_aware=True, mode=mode)
    assert kwargs["full_texts"] == TEXTS, "参考必须来自整片，不是剩下的那几句"
    assert kwargs["positions"] == expected_positions
    assert kwargs["texts"] == [TEXTS[p] for p in expected_positions]
    # 「第 2 句」在整片里的邻居是 one / three，不是剩下句子里拼在一起的 zero / four
    i = kwargs["positions"].index(2) if 2 in kwargs["positions"] else None
    if i is not None:
        p = kwargs["positions"][i]
        assert kwargs["full_texts"][p - 1 : p + 2] == ["one", "two", "three"]


# ── 翻译入口：只把这两样交给上下文感知分支 ───────────────────────────────

def _entry(monkeypatch, *, context_aware, **extra):
    seen = {}

    def fake_context_aware(texts, llm_backends, *a, **k):
        seen["context_aware"] = k
        return list(texts), [], None, {}, {}

    def fake_per_batch(*a, **k):
        seen["per_batch"] = (a, k)
        return list(a[0]), [], None, {}, {}

    monkeypatch.setattr(orch, "_build_llm_backends", lambda keys: ["假后端"])
    monkeypatch.setattr(orch, "_translate_llm_context_aware", fake_context_aware)
    monkeypatch.setattr(orch, "_translate_llm_per_batch", fake_per_batch)
    orch.translate_with_orchestrator(
        texts=extra.pop("texts", ["a", "b"]), api_keys=[{}],
        context_aware=context_aware, **extra,
    )
    return seen


def test_entry_context_aware_defaults_to_texts_itself(monkeypatch):
    seen = _entry(monkeypatch, context_aware=True)
    assert seen["context_aware"]["full_texts"] == ["a", "b"]
    assert seen["context_aware"]["positions"] == [0, 1]


def test_entry_context_aware_forwards_full_film(monkeypatch):
    seen = _entry(
        monkeypatch, context_aware=True, texts=["b", "d"],
        full_texts=["a", "b", "c", "d"], positions=[1, 3],
    )
    assert seen["context_aware"]["full_texts"] == ["a", "b", "c", "d"]
    assert seen["context_aware"]["positions"] == [1, 3]


@pytest.mark.parametrize("full_texts, positions", [
    (["a", "b", "c", "d"], [1]),          # 句数和位置数对不上
    (["a", "b", "c", "d"], [1, 9]),       # 位置超出整片
    (["a", "b", "c", "d"], [0, 3]),       # 位置上的原文不是要翻的那句（错位）
    (["a", "b", "c", "d"], None),         # 只给一样
])
def test_entry_rejects_misaligned_positions(monkeypatch, full_texts, positions):
    """位置对不上就是邻句会取错——宁可直接报错，也不带着错的参考去花钱。"""
    with pytest.raises(ValueError):
        _entry(monkeypatch, context_aware=True, texts=["b", "d"],
               full_texts=full_texts, positions=positions)


def test_entry_per_batch_never_receives_them(monkeypatch):
    seen = _entry(
        monkeypatch, context_aware=False, texts=["b", "d"],
        full_texts=["a", "b", "c", "d"], positions=[1, 3],
    )
    args, kwargs = seen["per_batch"]
    assert "full_texts" not in kwargs and "positions" not in kwargs
    assert "reference" not in kwargs
    assert len(args) == 7, "逐段的调用与改前一样：7 个位置参数"
