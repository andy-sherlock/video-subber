"""P390：Worker 必须把原始词交给 LLM 主裁，再按索引重建句子组。"""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from test import transcription_envelope_from_raw, locked_decision
from backend.transcriber_v2 import (
    ArtifactLanguageMismatch as _real_artifact_mismatch,
    TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY as _real_word_sources,
)


def _words(texts, *, step=0.4):
    return [
        {
            "word": text,
            "text": text,
            "start": round(index * step, 3),
            "end": round(index * step + 0.3, 3),
        }
        for index, text in enumerate(texts)
    ]


def _load_worker_without_audio_models(monkeypatch):
    class _Segment:
        def __init__(self, id, start, end, text):
            self.id = id
            self.start = start
            self.end = end
            self.text = text
            self.words = []
            self.zh = ""
            self.speaker_change = False
            self.uncertain = False

    class _Receipt:
        def __init__(self, routed_primary, actual_primary, word_source,
                     source_text, source_words, source_fingerprint=None,
                     reference_text="", reference_usable=False,
                     language_decision=None, engine_received_language=None,
                     engines_in_text_chain=None,
                     artifact_language_check="ok",
                     transcription_request_fingerprint="",
                     observation_evidence=None):
            self.routed_primary = routed_primary
            self.actual_primary = actual_primary
            self.word_source = word_source
            self.source_text = source_text
            self.source_words = source_words
            self.source_fingerprint = source_fingerprint or "test-source"
            # G2/B9+B10：收据带上副引擎的完整转录与可用性判定。
            self.reference_text = reference_text
            self.reference_usable = reference_usable
            # G11：语言决定与各引擎实收语言，随收据一起穿过存档往返。
            if language_decision is None:
                from test import locked_decision
                language_decision = locked_decision()
            self.language_decision = language_decision
            self.engine_received_language = dict(engine_received_language or {})
            # G11-B：本次哪些引擎的文字进了正式链路——缓存复用检查的依据。
            self.engines_in_text_chain = tuple(
                engines_in_text_chain
                if engines_in_text_chain is not None
                else self.engine_received_language.keys())
            self.artifact_language_check = artifact_language_check
            self.transcription_request_fingerprint = transcription_request_fingerprint
            self.observation_evidence = dict(observation_evidence or {})

    class _Envelope:
        def __init__(self, segments, receipt):
            self.segments = list(segments)
            self.receipt = receipt

        def __iter__(self):
            return iter(self.segments)

        def __len__(self):
            return len(self.segments)

        def __getitem__(self, index):
            return self.segments[index]

    monkeypatch.setitem(
        sys.modules,
        "backend.transcriber_v2",
        SimpleNamespace(
            Segment=_Segment,
            TranscriptionEnvelope=_Envelope,
            TranscriptionReceipt=_Receipt,
            transcribe=lambda *args, **kwargs: [],
            _whisper_is_usable=lambda *args, **kwargs: False,
            # G11：worker 会 import 这两个异常类型；替身模块必须提供**真的**那两个，
            # 否则 except 捕获的是另一个类，测试会假绿。
            ArtifactLanguageMismatch=_real_artifact_mismatch,
            TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY=_real_word_sources,
        ),
    )
    # 这里重新 import 一个绑着假 transcriber_v2 的 worker，本测试文件用完必须
    # 还原干净，否则后面的文件会拿到它：那个 worker 的 TranscriptionEnvelope
    # 不是真的那个类，受害文件里的 isinstance 会全线失败，而且报错完全看不出
    # 跟本文件有关。原来的写法漏了两处，**两处都只在 worker 尚未被 import 过
    # 时才发作**——所以字母序一变就爆一片：
    #   ① `delitem(..., raising=False)` 在键本来就不存在时什么也不记录，
    #      teardown 于是不会删掉 import_module 新建的那个模块；
    #   ② `import_module` 还会把新模块挂到**父包属性** `backend.worker` 上，
    #      而 `from backend import worker` 优先读父包属性，monkeypatch 不管它。
    import backend

    # 先登记恢复点（monkeypatch 会记住「原本有/原本没有」），再动手。
    monkeypatch.setitem(
        sys.modules, "backend.worker", sys.modules.get("backend.worker")
    )
    monkeypatch.setattr(
        backend, "worker", getattr(backend, "worker", None), raising=False
    )
    sys.modules.pop("backend.worker", None)
    return importlib.import_module("backend.worker")


def _empty_transcription_envelope(worker):
    segment = worker.Segment(0, 0.0, 1.0, "")
    receipt = worker.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="",
        source_words=[],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    return worker.TranscriptionEnvelope([segment], receipt)


class _RecordingBoundaryLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        return self.response


class _FlakyBoundaryLLM:
    def __init__(self, responses, *, raise_on):
        self.responses = list(responses)
        self.raise_on = set(raise_on)
        self.calls = []

    def translate_raw_json(self, prompt, system_prompt=None):
        index = len(self.calls)
        self.calls.append((prompt, system_prompt))
        if index in self.raise_on:
            raise ConnectionError("模拟断句窗口网络失败")
        return self.responses[index]


def test_worker_uses_llm_indices_to_build_groups_from_qwen_raw(
    monkeypatch,
    tmp_path,
):
    """P390：真实 Worker 入口不能再让 P389 先决定整片断点。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _words(["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "We found a hidden gem. It was great.",
        "word_timestamps": words,
    }), encoding="utf-8")
    llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')

    groups, _reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 1
    assert [(group.start_word_index, group.end_word_index) for group in groups] == [
        (0, 4),
        (5, 7),
    ]
    assert [word["text"] for group in groups for word in group.words] == [
        "We", "found", "a", "hidden", "gem.", "It", "was", "great.",
    ]


def test_worker_reuses_safe_boundary_cache_without_storing_raw_text(
    monkeypatch,
    tmp_path,
):
    """P390：相同原始词可复用索引缓存，缓存不应泄露原文。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _words(["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "We found a hidden gem. It was great.",
        "word_timestamps": words,
    }), encoding="utf-8")
    first_llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')

    first_groups, _reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=first_llm,
    )

    cache_path = tmp_path / "sentence_boundaries.json"
    assert len(first_llm.calls) == 1
    assert cache_path.exists()
    cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    # P395：窗口没了，缓存里那两个窗口字段一并删除
    assert set(cache_payload) == {
        "version",
        "source_fingerprint",
        "hint_fingerprint",
        "language",
        "break_after_indices",
        "group_fingerprint",
    }
    assert "hidden gem" not in cache_path.read_text(encoding="utf-8")

    second_llm = _FlakyBoundaryLLM([], raise_on={0})
    second_groups, _reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=second_llm,
    )

    assert second_llm.calls == []
    assert [group.text for group in second_groups] == [
        group.text for group in first_groups
    ]


def test_worker_rechecks_boundary_cache_when_source_language_changes(
    monkeypatch,
    tmp_path,
):
    """P390 + CI-08 #1：**receipt 中的语言决定**变化后，旧断点收据不能直接复用。

    G11 改合同：触发源从 config 的 `sourceLanguage` 换成收据里的语言决定——
    行为本身（换语言就得重新断句）不变，只是语言的来源变了。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _words(["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "We found a hidden gem. It was great.",
        "word_timestamps": words,
    }), encoding="utf-8")

    first_llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=first_llm,
    )

    # 同一份原始词，但收据里的语言决定换成了 ja
    second_llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="ja"),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=second_llm,
    )

    assert len(first_llm.calls) == 1
    assert len(second_llm.calls) == 1, (
        "收据里的语言决定变了，旧断点缓存必须失效并重新断句")


def test_worker_rejects_damaged_source_mismatched_and_group_mismatched_cache(
    monkeypatch,
    tmp_path,
):
    """P390：坏缓存或身份不符缓存必须重新走 LLM，不能静默复用。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _words(["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "We found a hidden gem. It was great.",
        "word_timestamps": words,
    }), encoding="utf-8")
    cache_path = tmp_path / "sentence_boundaries.json"

    def run_and_expect_one_llm_call():
        llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')
        worker._prepare_sentence_groups(
            transcription_envelope_from_raw(worker, raw_path),
            boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
            llm_boundary_backend=llm,
        )
        assert len(llm.calls) == 1

    run_and_expect_one_llm_call()
    cache_path.write_text("{broken json", encoding="utf-8")
    run_and_expect_one_llm_call()

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["source_fingerprint"] = "0" * 64
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    run_and_expect_one_llm_call()

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["group_fingerprint"] = "0" * 64
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    run_and_expect_one_llm_call()


def test_worker_rejects_boundary_cache_symlink_without_touching_target(
    monkeypatch,
    tmp_path,
):
    """P390：缓存名若是软链接，不能读取或覆盖其外部目标。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    words = _words(["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "We found a hidden gem. It was great.",
        "word_timestamps": words,
    }), encoding="utf-8")
    cache_path = tmp_path / "sentence_boundaries.json"
    outside_path = tmp_path / "outside.json"
    outside_path.write_text("outside sentinel", encoding="utf-8")
    cache_path.symlink_to(outside_path)
    llm = _RecordingBoundaryLLM('{"break_after_indices": [4]}')

    worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 1
    assert cache_path.is_symlink()
    assert outside_path.read_text(encoding="utf-8") == "outside sentinel"


class _FakeCheckpoint:
    def save(self, *args, **kwargs):
        return None


def test_translation_cache_with_different_sentence_identity_is_retranslated(
    monkeypatch,
    tmp_path,
):
    """P390：旧译文不能按列表位置错贴到新的最终句子组。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    segments = [
        SimpleNamespace(
            id=-1,
            start=0.0,
            end=1.0,
            text="First complete thought.",
            zh="",
        ),
        SimpleNamespace(
            id=-2,
            start=1.0,
            end=2.0,
            text="Second complete thought.",
            zh="",
        ),
    ]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {
            "id": -99,
            "start": 0.0,
            "end": 1.0,
            "text": "old fragment",
            "zh": "旧碎片译文",
        },
        {
            "id": -98,
            "start": 1.0,
            "end": 2.0,
            "text": "another old fragment",
            "zh": "旧碎片译文",
        },
    ]), encoding="utf-8")
    received = []

    def fake_translate(*, texts, **kwargs):
        received.extend(texts)
        return ["第一句新译文", "第二句新译文"], [], None, {}

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )
    monkeypatch.setattr(worker, "_reflection_review", lambda *args: args[3])

    worker._step_translate(
        "subtitle",
        segments,
        translated_path,
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        len(segments),
        _FakeCheckpoint(),
    )

    assert received == ["First complete thought.", "Second complete thought."]
    assert [segment.zh for segment in segments] == ["第一句新译文", "第二句新译文"]
    saved = json.loads(translated_path.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    assert [item["id"] for item in saved] == [-1, -2]
    assert [item["text"] for item in saved] == [
        "First complete thought.",
        "Second complete thought.",
    ]
    assert [item["zh"] for item in saved] == ["第一句新译文", "第二句新译文"]


def test_translation_cache_with_matching_sentence_identity_is_reused(
    monkeypatch,
    tmp_path,
):
    """P390：身份完全一致的译文缓存仍应直接复用。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    segments = [
        SimpleNamespace(
            id=-1,
            start=0.0,
            end=1.0,
            text="First complete thought.",
            zh="",
        ),
        SimpleNamespace(
            id=-2,
            start=1.0,
            end=2.0,
            text="Second complete thought.",
            zh="",
        ),
    ]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {
            "id": -1,
            "start": 0.0,
            "end": 1.0,
            "text": "First complete thought.",
            "zh": "第一句缓存译文",
        },
        {
            "id": -2,
            "start": 1.0,
            "end": 2.0,
            "text": "Second complete thought.",
            "zh": "第二句缓存译文",
        },
    ]), encoding="utf-8")
    calls = []

    def fake_translate(*, texts, **kwargs):
        calls.append(texts)
        raise AssertionError("身份匹配的翻译缓存不应重新调用翻译器")

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )

    worker._step_translate(
        "subtitle",
        segments,
        translated_path,
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        len(segments),
        _FakeCheckpoint(),
    )

    assert calls == []
    assert [segment.zh for segment in segments] == [
        "第一句缓存译文",
        "第二句缓存译文",
    ]


def test_bare_list_translation_cache_is_current_format_and_not_rewritten(
    monkeypatch,
    tmp_path,
):
    """裸列表就是当前正式格式：命中后原样复用，不因缺 version 字段被删或被重写。

    合并前的旧协议要求外层带 {"version": 1, "translations": [...]}，裸数组会被
    当作过期协议删掉。主线把格式统一成裸列表后，这个前提整个反过来了。
    这条与「身份匹配就复用」那条的分工：那条盯翻译接口的调用次数，
    这条盯文件系统副作用——缓存文件必须还在，而且一个字节都没被改写。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    segment = SimpleNamespace(
        id=-1,
        start=0.0,
        end=1.0,
        text="A complete thought.",
        zh="",
    )
    translated_path = tmp_path / "translated.json"
    original_bytes = json.dumps([{
        "id": -1,
        "start": 0.0,
        "end": 1.0,
        "text": "A complete thought.",
        "zh": "缓存译文",
    }])
    translated_path.write_text(original_bytes, encoding="utf-8")

    def fake_translate(*, texts, **kwargs):
        raise AssertionError("裸列表是当前正式格式，不该被当旧协议丢弃后重译")

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )

    worker._step_translate(
        "subtitle",
        [segment],
        translated_path,
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        1,
        _FakeCheckpoint(),
    )

    assert segment.zh == "缓存译文"
    assert translated_path.exists()
    assert translated_path.read_text(encoding="utf-8") == original_bytes


def test_legacy_versioned_envelope_translation_cache_is_rejected_and_retranslated(
    monkeypatch,
    tmp_path,
):
    """带 version 外层信封的旧缓存不是当前格式，必须拒绝复用并重新翻译。

    不再依赖已删除的版本常量或旧读取函数：走真实的 _step_translate 入口，
    由 _translation_cache_matches_segments 判定「不是裸列表」而整包否掉，
    最后被正常的裸列表写回覆盖。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    segment = SimpleNamespace(
        id=-1,
        start=0.0,
        end=1.0,
        text="A complete thought.",
        zh="",
    )
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps({
        "version": 999,
        "translations": [{
            "id": -1,
            "start": 0.0,
            "end": 1.0,
            "text": "A complete thought.",
            "zh": "旧信封译文",
        }],
    }), encoding="utf-8")
    received = []

    def fake_translate(*, texts, **kwargs):
        received.extend(texts)
        return ["新译文"], [], None, {}

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )
    monkeypatch.setattr(worker, "_reflection_review", lambda *args: args[3])

    worker._step_translate(
        "subtitle",
        [segment],
        translated_path,
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        1,
        _FakeCheckpoint(),
    )

    assert received == ["A complete thought."]
    assert segment.zh == "新译文"
    saved = json.loads(translated_path.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    assert saved == [{
        "id": -1,
        "start": 0.0,
        "end": 1.0,
        "text": "A complete thought.",
        "zh": "新译文",
    }]


def test_reflection_rewrites_the_same_bare_list_translation_cache_shape(
    monkeypatch,
    tmp_path,
):
    """质量反思重写缓存时必须和主翻译写入使用完全相同的裸列表格式。

    替身签名必须跟上 _reflection_review 的真实调用
    （parse_reflection_result(raw, source_texts=...)）。签名对不上会抛 TypeError，
    而 _reflection_review 外面包着 except Exception——异常被吞掉后文件根本不会被写，
    测试只看得到「文件不存在」，看不到真正的原因。parsed_with 记录真实入参，
    用来证明确实执行到了这一步，而不是中途被兜底跳过。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    segment = SimpleNamespace(
        id=-1,
        start=0.0,
        end=1.0,
        text="A complete thought.",
        zh="旧译文",
    )
    translated_path = tmp_path / "translated.json"
    parsed_with = []

    def fake_parse_reflection_result(raw, source_texts=None):
        parsed_with.append((raw, source_texts))
        return {0: "修正译文"}

    monkeypatch.setitem(
        sys.modules,
        "backend.translation.reflection",
        SimpleNamespace(
            build_reflection_prompt=lambda *args, **kwargs: "review",
            parse_reflection_result=fake_parse_reflection_result,
            apply_reflection=lambda values, corrections: ["修正译文"],
        ),
    )

    class _ReflectionLLM:
        def translate_raw(self, prompt, max_tokens=4096):
            return SimpleNamespace(translated=['{"0": "修正译文"}'])

    import backend.translation.orchestrator as orchestrator

    monkeypatch.setattr(
        orchestrator,
        "_build_llm_backends",
        lambda api_keys: [_ReflectionLLM()],
    )

    failed = worker._reflection_review(
        [segment],
        [segment.text],
        [segment.zh],
        [],
        translated_path,
        [{"platform": "deepseek", "key": "x"}],
        "quality",
        {"reflection": True},
    )

    saved = json.loads(translated_path.read_text(encoding="utf-8"))
    assert failed == []
    assert parsed_with == [('{"0": "修正译文"}', ["A complete thought."])]
    assert saved == [{
        "id": -1,
        "start": 0.0,
        "end": 1.0,
        "text": "A complete thought.",
        "zh": "修正译文",
    }]


def _recording_delivery(monkeypatch, worker):
    """把最终交付入口换成「记账 + 判定自己不是当前轮」。

    _deliver_outputs 返回 False 表示本轮不宣告完成、用户目录零改动。它位于
    main() 全流程的最后一步（worker.py:2855），所以不会跳过前面任何被观察的环节。

    返回的列表用来断言主流程真的走到了这里：main() 的兜底 except Exception
    也会让函数返回 None，光看 `main() is None` 分不出「跑完了」和「中途崩了被接住」。
    """
    delivered = []

    def fake_deliver(*args, **kwargs):
        delivered.append(1)
        return False

    monkeypatch.setattr(worker, "_deliver_outputs", fake_deliver)
    return delivered


def test_model_change_removes_qwen_raw_and_boundary_cache_before_prepare(
    monkeypatch,
    tmp_path,
):
    """P390：模型或精准度变化时，旧原始词和断点收据不能留给新运行。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    video_path = job_dir / "input.mp4"
    video_path.write_bytes(b"video")
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps({
        "video_path": str(video_path),
        "video_filename": "input.mp4",
        "outputMode": "srt",
        "model": "large-v3",
        "language": "en",
        "transcriptionTier": "standard",
    }), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    transcript_path = cache / "transcript.json"
    raw_path = cache / "qwen_raw.json"
    boundary_path = cache / "sentence_boundaries.json"
    for path in (transcript_path, raw_path, boundary_path):
        path.write_text("old cache", encoding="utf-8")
    (cache / "checkpoint.json").write_text(json.dumps({
        "meta": {
            "model": "large-v2",
            "transcriptionTier": "standard",
        },
    }), encoding="utf-8")
    observed = []

    class _Checkpoint:
        def __init__(self, *args, **kwargs):
            pass

        def load(self):
            return "burn"

        def save(self, *args, **kwargs):
            return None

    def fake_prepare(segments, **kwargs):
        observed.append((raw_path.exists(), boundary_path.exists()))
        return segments, None

    monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "p390-model-change")
    monkeypatch.setattr(worker, "Checkpoint", _Checkpoint)
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(worker, "_download_source_if_url", lambda config, directory, path: path)
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache, "hash"),
    )
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(
        worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    monkeypatch.setattr(
        worker,
        "_step_transcribe",
        lambda *args: _empty_transcription_envelope(worker),
    )
    monkeypatch.setattr(worker, "_prepare_sentence_groups", fake_prepare)
    monkeypatch.setattr(worker, "_run_transcript_refiner", lambda *args, **kwargs: None)
    # G12 第三批 B：主流程新增「润色后并组」一步；这里的分组替身不返回真实组，一并替掉。
    monkeypatch.setattr(
        worker, "_merge_groups_after_refiner", lambda segments, **kwargs: segments)
    monkeypatch.setattr(
        worker, "_step_translate", lambda *args, **kwargs: ([], {}, 0, {}, None))
    monkeypatch.setattr(worker, "_step_subtitle", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_step_burn_or_mux", lambda *args, **kwargs: None)
    delivered = _recording_delivery(monkeypatch, worker)

    assert worker.main() is None
    assert observed == [(False, False)]
    assert delivered == [1]



# CI-08 #2：原 test_source_language_change_removes_translation_cache_before_translate
# 测的是「config 的 sourceLanguage 变化 → 清翻译缓存」。
# 该用户配置行为已被产品裁决砍掉（不做源语言选择器），测试**整条删除**——
# 没有对象可测了，留着改断言就是给一个不存在的合同发绿灯。

def test_main_standard_and_precise_modes_both_enter_p390_raw_word_path(
    monkeypatch,
    tmp_path,
):
    """P390：标准和精准模式都必须从原始词进入同一断句主裁。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    from backend.core import sound_map

    cache_by_job = {}
    observed = []
    map_sources = []

    class _Checkpoint:
        def __init__(self, *args, **kwargs):
            pass

        def load(self):
            return "start"

        def save(self, *args, **kwargs):
            return None

    def fake_find_versioned_cache(base, path, **_kwargs):
        return cache_by_job[Path(base).parent.name], "hash"

    def fake_transcribe(stage, audio_path, transcript_path, model, speaker_detect,
                        transcription_tier, cache, config, checkpoint):
        observed.append(("transcribe", transcription_tier, Path(cache)))
        return _empty_transcription_envelope(worker)

    def fake_prepare(transcription, **kwargs):
        # G11：断句不再接收 language 实参，语言只从收据读。
        observed.append((
            "prepare",
            transcription.receipt.language_decision.language,
            transcription.receipt.actual_primary,
            kwargs["api_keys"],
        ))
        return transcription.segments, None

    def fake_acquire_sound_map(
        media_path, _cache_dir, *, source_content_sha256=None
    ):
        map_sources.append(Path(media_path))
        current_hash = source_content_sha256 or worker.get_file_hash(media_path)
        identity = {
            "source_content_sha256": current_hash,
            "track_identity": {
                "index": 0,
                "codec": "aac",
                "sample_rate": 48000,
                "channels": 2,
            },
            "vad_asset_sha256": "b" * 64,
            "vad_parameters": sound_map.canonical_vad_parameters(),
        }
        return sound_map.make_map_record(
            identity,
            [],
            total_samples=0,
            map_status="unavailable",
            reason_code="map_compute_failed",
        ), "unavailable"

    monkeypatch.setattr(worker, "Checkpoint", _Checkpoint)
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(worker, "_download_source_if_url", lambda config, directory, path: path)
    monkeypatch.setattr(worker, "find_versioned_cache", fake_find_versioned_cache)
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(
        worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    monkeypatch.setattr(worker, "_step_transcribe", fake_transcribe)
    monkeypatch.setattr(
        worker, "_acquire_task_sound_map", fake_acquire_sound_map
    )
    monkeypatch.setattr(worker, "_prepare_sentence_groups", fake_prepare)
    monkeypatch.setattr(worker, "_run_transcript_refiner", lambda *args, **kwargs: None)
    # G12 第三批 B：主流程新增「润色后并组」一步；这里的分组替身不返回真实组，一并替掉。
    monkeypatch.setattr(
        worker, "_merge_groups_after_refiner", lambda segments, **kwargs: segments)
    monkeypatch.setattr(
        worker, "_step_translate", lambda *args, **kwargs: ([], {}, 0, {}, None))
    monkeypatch.setattr(worker, "_step_subtitle", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_step_burn_or_mux", lambda *args, **kwargs: None)
    delivered = _recording_delivery(monkeypatch, worker)

    for tier in ("standard", "precise"):
        job_dir = tmp_path / tier
        job_dir.mkdir()
        video_path = job_dir / "input.mp4"
        video_path.write_bytes(b"video")
        config_path = job_dir / "config.json"
        config_path.write_text(json.dumps({
            "video_path": str(video_path),
            "video_filename": "input.mp4",
            "outputMode": "srt",
            "model": "large-v3",
            "sourceLanguage": "en",
            "transcriptionTier": tier,
            "apiKeys": ["test-key"],
        }), encoding="utf-8")
        cache = job_dir / "cache" / "hash"
        cache.mkdir(parents=True)
        cache_by_job[job_dir.name] = cache
        monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
        monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", f"p390-{tier}")

        assert worker.main() is None

    url_job_dir = tmp_path / "url"
    url_job_dir.mkdir()
    url_video_path = url_job_dir / "downloaded.mp4"
    url_video_path.write_bytes(b"video")
    url_audio_path = url_job_dir / "downloaded.m4a"
    url_audio_path.write_bytes(b"audio")
    url_config_path = url_job_dir / "config.json"
    url_config_path.write_text(json.dumps({
        "video_path": str(url_video_path),
        "video_filename": "downloaded.mp4",
        "source_url": "https://example.invalid/watch",
        "dl_audio_path": str(url_audio_path),
        "outputMode": "srt",
        "model": "large-v3",
        "sourceLanguage": "en",
        "transcriptionTier": "standard",
        "apiKeys": ["test-key"],
    }), encoding="utf-8")
    url_cache = url_job_dir / "cache" / "hash"
    url_cache.mkdir(parents=True)
    cache_by_job[url_job_dir.name] = url_cache
    monkeypatch.setattr(sys, "argv", ["worker", str(url_config_path)])
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "p390-url")

    assert worker.main() is None

    assert observed == [
        ("transcribe", "standard", cache_by_job["standard"]),
        ("prepare", "en", "whisper", ["test-key"]),
        ("transcribe", "precise", cache_by_job["precise"]),
        ("prepare", "en", "whisper", ["test-key"]),
        ("transcribe", "standard", cache_by_job["url"]),
        ("prepare", "en", "whisper", ["test-key"]),
    ]
    assert delivered == [1, 1, 1]
    assert map_sources == [
        tmp_path / "standard" / "input.mp4",
        tmp_path / "precise" / "input.mp4",
        tmp_path / "url" / "downloaded.m4a",
    ]
    assert all(path.name != "audio.wav" for path in map_sources)


def test_p390_display_splitting_never_changes_translation_input(
    monkeypatch,
    tmp_path,
):
    """P390：显示层拆中文后，翻译输入和最终句子组仍保持完整。"""
    worker = _load_worker_without_audio_models(monkeypatch)
    # G5-1：word_align_segments 已并入 display_timeline（同一映射算法）
    from backend.core.display_timeline import build_display_timeline

    words = _words(["One", "sentence.", "Another", "sentence."])
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "One sentence. Another sentence.",
        "word_timestamps": words,
    }), encoding="utf-8")
    groups, _reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path),
        boundary_cache_path=raw_path.with_name("sentence_boundaries.json"),
        llm_boundary_backend=_RecordingBoundaryLLM(
            '{"break_after_indices": [1]}'
        ),
    )
    received = []

    def fake_translate(*, texts, **kwargs):
        received.extend(texts)
        return ["第一句。第二句。", "第三句。"], [], None, {}

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        fake_translate,
    )
    monkeypatch.setattr(worker, "_reflection_review", lambda *args: args[3])

    worker._step_translate(
        "translate",
        groups,
        tmp_path / "translated.json",
        "speed",
        ["google"],
        [],
        False,
        {},
        tmp_path,
        len(groups),
        _FakeCheckpoint(),
    )
    display_segments = build_display_timeline(groups)

    assert received == ["One sentence.", "Another sentence."]
    assert [group.text for group in groups] == [
        "One sentence.",
        "Another sentence.",
    ]
    assert [group.zh for group in groups] == ["第一句。第二句。", "第三句。"]
    # G5-1：显示条目是 DisplayEntry（frozen），译文在 .text 而非 .zh。
    # 断言的行为不变——句末拆条仍把「第一句。第二句。」切成两屏。
    # G5-1：素材是 3 句话挤在约 1 秒内的合成数据。按 §7 的补救阶梯
    # （向后延长 → 合并 → 保留并报告），1 秒窗口里放不下 3 条各 0.84 秒的
    # 字幕，合并成一条正是合同要求的结果。本测试要保的是「翻译输入不被
    # 显示层改变」，那三条断言在上面，未变。
    joined = "".join(e.text for e in display_segments)
    # G12 第三批 B 收尾（P471）：成品按奈飞规范去掉句号，按不带句号的正文检查。
    for piece in ("第一句", "第二句", "第三句"):
        assert piece in joined, f"显示层丢了「{piece}」"
