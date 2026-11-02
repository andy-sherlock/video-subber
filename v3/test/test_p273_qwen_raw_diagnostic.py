"""P273：保存 Qwen 原始转录证据的行为测试。"""

def _Outcome(**kw):
    """惰性构造真的 QwenAsrOutcome。

    **不能在顶层 `from backend.core.qwen_asr_runner import ...`**：
    多个测试用 `sys.modules` 把该模块换成假模块；本文件若在那期间被导入
    （例如被别的测试 `from test.xxx import _stub_environment` 拉进来），
    顶层 import 会拿到假模块并炸在 `(unknown location)`。
    `transcriber_v2` 顶层持有真类的引用，不受 sys.modules 替换影响。
    """
    from backend.transcriber_v2 import QwenAsrOutcome
    return QwenAsrOutcome(**kw)
import json


def test_qwen_raw_diagnostic_refuses_a_symlink_target_without_touching_victim(
    tmp_path, caplog
):
    from backend.core import qwen_asr_runner

    victim = tmp_path / "victim.json"
    victim.write_text('{"keep": true}', encoding="utf-8")
    target = tmp_path / "qwen_raw.json"
    target.symlink_to(victim)

    with caplog.at_level("WARNING", logger="qwen_asr_runner"):
        qwen_asr_runner._write_qwen_raw_diagnostic(
            str(tmp_path),
            full_text="Do not overwrite this link.",
            word_timestamps=[],
            audio_duration=1.0,
            model_call_count=1,
            input_ranges=[{"start": 0.0, "end": 1.0}],
        )

    assert target.is_symlink()
    assert victim.read_text(encoding="utf-8") == '{"keep": true}'
    assert "Qwen 原始诊断保存失败" in caplog.text


def test_short_qwen_run_saves_raw_evidence_and_keeps_raw_timestamp_payload(
    monkeypatch, tmp_path
):
    from backend.core import qwen_asr_runner

    raw_text = "Hello, world. How are you?"
    word_timestamps = [
        {"text": "Hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.5, "end": 0.9},
        {"text": "How", "start": 1.2, "end": 1.5},
        {"text": "are", "start": 1.6, "end": 1.8},
        {"text": "you", "start": 1.9, "end": 2.2},
    ]

    monkeypatch.setattr(qwen_asr_runner, "is_qwen_asr_available", lambda: True)
    monkeypatch.setattr(qwen_asr_runner, "_load_qwen_model", lambda: object())
    monkeypatch.setattr(
        qwen_asr_runner,
        "_get_audio_duration",
        lambda _audio_path: 12.5,
    )
    monkeypatch.setattr(
        qwen_asr_runner,
        "_qwen_transcribe",
        lambda _model, _audio_path, _language: (raw_text, word_timestamps),
    )

    result = qwen_asr_runner.run_qwen_asr(
        "/fake/input.wav",
        language="en",
        cache_dir=str(tmp_path),
    )

    assert result.chunks[0]["text"] == raw_text  # P464：返回 QwenAsrOutcome，块在 .chunks 上
    assert result.chunks[0]["t_start"] == 0.0
    assert result.chunks[0]["t_end"] == 12.5
    assert [item["text"] for item in result.chunks[0]["segments"]] == [
        "Hello,", "world.", "How", "are", "you?"
    ]

    raw_path = tmp_path / "qwen_raw.json"
    assert raw_path.exists()
    evidence = json.loads(raw_path.read_text(encoding="utf-8"))
    assert evidence["full_text"] == raw_text
    assert evidence["word_timestamps"] == word_timestamps
    assert evidence["audio_duration"] == 12.5
    assert evidence["model_call_count"] == 1
    assert evidence["input_ranges"] == [{"start": 0.0, "end": 12.5}]
    assert evidence["punctuation_counts"] == {
        "total": 3,
        "sentence_enders": 2,
        "by_character": {",": 1, ".": 1, "?": 1},
    }


def test_full_text_punctuation_is_attached_to_matching_timestamp_words(
    monkeypatch, tmp_path
):
    from backend.core import qwen_asr_runner

    raw_text = "Hello, world. How are you?"
    word_timestamps = [
        {"text": "Hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.5, "end": 0.9},
        {"text": "How", "start": 1.2, "end": 1.5},
        {"text": "are", "start": 1.6, "end": 1.8},
        {"text": "you", "start": 1.9, "end": 2.2},
    ]

    monkeypatch.setattr(qwen_asr_runner, "is_qwen_asr_available", lambda: True)
    monkeypatch.setattr(qwen_asr_runner, "_load_qwen_model", lambda: object())
    monkeypatch.setattr(
        qwen_asr_runner, "_get_audio_duration", lambda _audio_path: 12.5
    )
    monkeypatch.setattr(
        qwen_asr_runner,
        "_qwen_transcribe",
        lambda _model, _audio_path, _language: (raw_text, word_timestamps),
    )

    result = qwen_asr_runner.run_qwen_asr(
        "/fake/input.wav", language="en", cache_dir=str(tmp_path)
    ).chunks  # P464：返回 QwenAsrOutcome，块在 .chunks 上

    assert [item["text"] for item in result[0]["segments"]] == [
        "Hello,", "world.", "How", "are", "you?"
    ]
    evidence = json.loads(
        (tmp_path / "qwen_raw.json").read_text(encoding="utf-8")
    )
    assert [item["text"] for item in evidence["word_timestamps"]] == [
        "Hello", "world", "How", "are", "you"
    ]


def test_long_qwen_run_records_each_call_and_keeps_raw_chunk_words(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from backend.core import qwen_asr_runner

    results = [
        SimpleNamespace(
            text="Hello, world.",
            segments=[
                {"text": "Hello", "start": 0.0, "end": 0.4},
                {"text": "world", "start": 0.5, "end": 0.9},
            ],
        ),
        SimpleNamespace(
            text="How are you?",
            segments=[
                {"text": "How", "start": 0.0, "end": 0.3},
                {"text": "are", "start": 0.4, "end": 0.6},
                {"text": "you", "start": 0.7, "end": 1.0},
            ],
        ),
    ]
    calls = []

    monkeypatch.setattr(qwen_asr_runner, "is_qwen_asr_available", lambda: True)
    monkeypatch.setattr(qwen_asr_runner, "_USE_MLX", True)
    monkeypatch.setattr(qwen_asr_runner, "_USE_PYTORCH", False)
    monkeypatch.setattr(qwen_asr_runner, "_LONG_AUDIO_THRESHOLD_S", 10.0)
    monkeypatch.setattr(qwen_asr_runner, "_AUDIO_PAD_S", 0.0)
    monkeypatch.setattr(qwen_asr_runner, "_load_qwen_model", lambda: object())
    monkeypatch.setattr(
        qwen_asr_runner, "_get_audio_duration", lambda _audio_path: 12.0
    )
    monkeypatch.setattr(
        qwen_asr_runner,
        "_mlx_transcribe",
        lambda *_args, **_kwargs: calls.append(True) or results[len(calls) - 1],
    )
    monkeypatch.setattr(
        qwen_asr_runner, "_extract_audio_chunk", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        qwen_asr_runner,
        "_chunk_by_time",
        lambda _duration: [(0.0, 5.0), (5.0, 12.0)],
    )

    result = qwen_asr_runner.run_qwen_asr(
        "/fake/input.wav", language="en", cache_dir=str(tmp_path)
    ).chunks  # P464：返回 QwenAsrOutcome，块在 .chunks 上

    assert len(result) == 2
    evidence = json.loads(
        (tmp_path / "qwen_raw.json").read_text(encoding="utf-8")
    )
    assert evidence["full_text"] == "Hello, world. How are you?"
    assert evidence["model_call_count"] == 2
    assert evidence["input_ranges"] == [
        {"start": 0.0, "end": 5.0},
        {"start": 5.0, "end": 12.0},
    ]
    # P393（G7）起，分块路径的诊断词流不再是「模型原始未加工词」，而是
    # 「与 full_text 配对、已加全局偏移、已裁重叠、已挂标点」的词流。
    # 原因是三者不可兼得：要让 full_text 与词流逐字配对（下游 sentence_groups
    # 的硬闸门），又要保住标点，就只能让词走完挂标点这一步——标点只存在于整串
    # raw_text 里，映射到词上的唯一手段就是 _attach_full_text_punctuation。
    # 这是 G7 ② 双签的明确裁决，并在 ③ 登记为「明确接受的残留」；
    # 模型原始词仍完整保留在 qwen_chunks.json 的 raw_segments 中。
    #
    # 本测试（P273）的原始关切没有改变，且下面逐条仍然成立：
    #   · 每次模型调用都记账      → model_call_count == 2（上面已断言）
    #   · 每块的词都不丢          → 5 个词全在，顺序不变
    #   · 每块的输入范围都留痕    → input_ranges（上面已断言）
    assert [item["text"] for item in evidence["word_timestamps"]] == [
        "Hello,", "world.", "How", "are", "you?"
    ]
    # 去掉标点后必须与模型原始输出逐字一致——「词没丢、只是带了标点」
    assert [item["text"].rstrip(",.?!") for item in evidence["word_timestamps"]] == [
        "Hello", "world", "How", "are", "you"
    ]


def test_no_punctuation_keeps_timestamp_words_without_inventing_boundaries():
    from backend.core import qwen_asr_runner

    timestamp_words = [
        {"text": "Hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.5, "end": 0.9},
    ]

    result = qwen_asr_runner._attach_full_text_punctuation(
        "Hello world", timestamp_words
    )

    assert [item["text"] for item in result] == ["Hello", "world"]
    assert qwen_asr_runner._punctuation_counts("Hello world")["total"] == 0


def test_unmatched_full_text_keeps_raw_words_and_does_not_add_boundaries(caplog):
    from backend.core import qwen_asr_runner

    timestamp_words = [{"text": "Hello", "start": 0.0, "end": 0.4}]

    with caplog.at_level("WARNING", logger="qwen_asr_runner"):
        result = qwen_asr_runner._attach_full_text_punctuation(
            "Hello, world.", timestamp_words
        )

    assert result == timestamp_words
    assert "未被词级时间覆盖" in caplog.text


def _punctuated_qwen_chunk():
    return [{
        "text": "Hello, world. How are you?",
        "segments": [
            {"text": "Hello,", "start": 0.0, "end": 0.4},
            {"text": "world.", "start": 0.5, "end": 0.9},
            {"text": "How", "start": 1.2, "end": 1.5},
            {"text": "are", "start": 1.6, "end": 1.8},
            {"text": "you?", "start": 1.9, "end": 2.2},
        ],
    }]


def test_standard_mac_qwen_route_uses_full_text_sentence_boundaries(monkeypatch):
    from types import SimpleNamespace

    from backend import transcriber_v2
    from backend.core import qwen_asr_runner

    from backend.core.whisper_runner import LanguageDecision
    decision = LanguageDecision(
        language="en", locked=True, split=False, expected_count=3, valid_count=3)
    monkeypatch.setattr(
        transcriber_v2, "choose_standard_primary_engine", lambda *a: "qwen"
    )
    monkeypatch.setattr(transcriber_v2, "release_whisper_model", lambda: None)
    # P463：路由判据现在还看**后端能不能给词级时间**（`bool(_USE_MLX)`）。
    # 这条测试的名字里就写着 mac——它测的是 MLX 后端那条路，必须显式声明，
    # **不能靠「跑测试的机器碰巧是 Mac」**。不声明的后果实测过：
    # 同一份代码，Mac 上绿、Windows/Linux 上红，全量测试的颜色取决于谁在跑。
    monkeypatch.setattr(qwen_asr_runner, "_USE_MLX", True)
    monkeypatch.setattr(qwen_asr_runner, "is_qwen_asr_available", lambda: True)
    monkeypatch.setattr(
        qwen_asr_runner, "run_qwen_asr",
        # P464：run_qwen_asr 现在返回 QwenAsrOutcome，不再是裸列表。
        lambda *a, **k: _Outcome(complete=True, chunks=_punctuated_qwen_chunk()),
    )

    holder = {}
    # G11：路由函数不再探测也不再返回语言，只按已锁定的决定选主引擎。
    primary = transcriber_v2._run_standard_language_router(
        "/fake/input.wav", decision, True, True,
        "/fake/cache", None, holder,
    )
    route = transcriber_v2._resolve_routing(
        None, holder, "standard", True, "/fake/input.wav", decision.language,
        "/fake/cache", primary, False, [],
    )

    assert route.which_primary == "qwen"
    assert [segment.text for segment in route.qwen_l1_segments] == [
        "Hello, world.", "How are you?"
    ]
    assert [(segment.start, segment.end) for segment in route.qwen_l1_segments] == [
        (0.0, 0.9), (1.2, 2.2)
    ]


def test_precise_qwen_route_keeps_sentence_boundary_regression():
    from types import SimpleNamespace

    from backend import transcriber_v2

    finished_qwen_thread = SimpleNamespace(join=lambda: None)
    route = transcriber_v2._resolve_routing(
        finished_qwen_thread,
        {"data": _Outcome(complete=True, chunks=_punctuated_qwen_chunk())},
        "precise", True,
        "/fake/input.wav", "en", "/fake/cache", None, True, [],
    )

    assert route.which_primary == "qwen"
    assert [segment.text for segment in route.qwen_l1_segments] == [
        "Hello, world.", "How are you?"
    ]
