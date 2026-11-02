"""P411：Qwen 标点搬运、引号归属和字符守恒回归。"""

from copy import deepcopy
import io
import json

from test import locked_decision

import pytest

from test import transcription_envelope_from_raw

from backend.core import qwen_asr_runner


def _segments(words):
    return [
        {
            "text": word,
            "start": index * 0.5,
            "end": index * 0.5 + 0.4,
            "speaker": "A",
        }
        for index, word in enumerate(words)
    ]


def _texts(segments):
    return [segment["text"] for segment in segments]


def test_japanese_unicode_punctuation_is_counted_and_attached_by_direction():
    full_text = "彼は「行かない、」。"
    timestamp_segments = _segments(["彼は", "行かない"])

    counts = qwen_asr_runner._punctuation_counts(full_text)
    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, timestamp_segments
    )

    assert counts["total"] == 4
    assert counts["sentence_enders"] == 1
    assert counts["by_character"] == {"「": 1, "、": 1, "」": 1, "。": 1}
    assert _texts(result) == ["彼は", "「行かない、」。"]


def test_contiguous_punctuation_uses_one_cut_point_without_reordering():
    full_text = "这个API叫fetch()，很好用。"
    timestamp_segments = _segments(["这个", "API", "叫", "fetch", "很好用"])

    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, timestamp_segments
    )

    assert _texts(result) == ["这个", "API", "叫", "fetch", "()，很好用。"]


def test_ascii_quote_and_apostrophe_ownership_stays_with_the_right_word():
    full_text = 'He said "no" to the boys\' plan. It\'s fine, don\'t worry.'
    timestamp_segments = _segments([
        "He", "said", "no", "to", "the", "boys", "plan",
        "It's", "fine", "don't", "worry",
    ])

    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, timestamp_segments
    )

    assert _texts(result) == [
        "He", "said", '"no"', "to", "the", "boys'", "plan.",
        "It's", "fine,", "don't", "worry.",
    ]


@pytest.mark.parametrize(
    ("full_text", "words", "expected"),
    [
        (
            'He said:"no"',
            ["He", "said", "no"],
            ["He", "said:", '"no"'],
        ),
        (
            'He said ("no")',
            ["He", "said", "no"],
            ["He", "said", '("no")'],
        ),
        (
            '他说："不去"。',
            ["他说", "不去"],
            ["他说：", '"不去"。'],
        ),
    ],
)
def test_ascii_open_quote_is_attached_to_the_following_word(
    full_text, words, expected
):
    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, _segments(words)
    )

    assert _texts(result) == expected


@pytest.mark.parametrize(
    ("full_text", "words", "expected"),
    [
        (
            'He said "No."',
            ["He", "said", "No"],
            ["He", "said", '"No."'],
        ),
        (
            '他说："不去"。',
            ["他说", "不去"],
            ["他说：", '"不去"。'],
        ),
        (
            '他说："不去……"',
            ["他说", "不去"],
            ["他说：", '"不去……"'],
        ),
    ],
)
def test_ascii_close_quote_is_attached_to_the_previous_word(
    full_text, words, expected
):
    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, _segments(words)
    )

    assert _texts(result) == expected


def test_word_internal_apostrophes_are_not_split():
    result = qwen_asr_runner._attach_full_text_punctuation(
        "rock'n'roll is fun.", _segments(["rock'n'roll", "is", "fun"])
    )

    assert _texts(result) == ["rock'n'roll", "is", "fun."]


@pytest.mark.parametrize(
    ("full_text", "words", "expected"),
    [
        (
            "He said 'hello' then 'goodbye' now.",
            ["He", "said", "hello", "then", "goodbye", "now"],
            ["He", "said", "'hello'", "then", "'goodbye'", "now."],
        ),
        (
            "He said 'it's fine' then 'don't go'.",
            ["He", "said", "it's", "fine", "then", "don't", "go"],
            ["He", "said", "'it's", "fine'", "then", "'don't", "go'."],
        ),
        (
            "the boys' bikes are here.",
            ["the", "boys", "bikes", "are", "here"],
            ["the", "boys'", "bikes", "are", "here."],
        ),
        (
            "the 90s' music was good.",
            ["the", "90s", "music", "was", "good"],
            ["the", "90s'", "music", "was", "good."],
        ),
        (
            "他说「她说『好』」。",
            ["他说", "她说", "好"],
            ["他说", "「她说", "『好』」。"],
        ),
    ],
)
def test_quote_state_and_apostrophe_cases_preserve_group_order(
    full_text, words, expected
):
    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, _segments(words)
    )

    assert _texts(result) == expected


def test_quote_state_is_reset_between_calls_and_double_single_states_are_independent():
    first = qwen_asr_runner._attach_full_text_punctuation(
        'He said "a".', _segments(["He", "said", "a"])
    )
    second = qwen_asr_runner._attach_full_text_punctuation(
        'He said "b".', _segments(["He", "said", "b"])
    )
    mixed = qwen_asr_runner._attach_full_text_punctuation(
        'He said \'a\' and "b".', _segments(["He", "said", "a", "and", "b"])
    )

    assert _texts(first) == ["He", "said", '"a".']
    assert _texts(second) == ["He", "said", '"b".']
    assert _texts(mixed) == ["He", "said", "'a'", "and", '"b".']


@pytest.mark.parametrize(
    ("full_text", "words", "expected"),
    [
        (
            "Hello there. How are you? I am fine, thanks!",
            ["Hello", "there", "How", "are", "you", "I", "am", "fine", "thanks"],
            ["Hello", "there.", "How", "are", "you?", "I", "am", "fine,", "thanks!"],
        ),
        (
            "Dr. Smith works at the U.S. office.",
            ["Dr", "Smith", "works", "at", "the", "U", "S", "office"],
            ["Dr.", "Smith", "works", "at", "the", "U.", "S.", "office."],
        ),
        (
            'He said "no way" and left.',
            ["He", "said", "no", "way", "and", "left"],
            ["He", "said", '"no', 'way"', "and", "left."],
        ),
        (
            "你好。你今天怎么样？我很好，谢谢！",
            ["你好", "你今天怎么样", "我很好", "谢谢"],
            ["你好。", "你今天怎么样？", "我很好，", "谢谢！"],
        ),
        (
            "我买了苹果、香蕉、橘子。你要哪个？",
            ["我买了苹果", "香蕉", "橘子", "你要哪个"],
            ["我买了苹果、", "香蕉、", "橘子。", "你要哪个？"],
        ),
        (
            "他说「我不去」。然后就走了。",
            ["他说", "我不去", "然后就走了"],
            ["他说", "「我不去」。", "然后就走了。"],
        ),
        (
            "邪魔をするなら、まず貴様から倒す。",
            ["邪魔をするなら", "まず貴様から倒す"],
            ["邪魔をするなら、", "まず貴様から倒す。"],
        ),
        (
            "彼は「行かない」と言った。",
            ["彼は", "行かない", "と", "言った"],
            ["彼は", "「行かない」", "と", "言った。"],
        ),
        (
            "안녕하세요. 오늘 어떠세요? 저는 괜찮아요, 감사합니다!",
            ["안녕하세요", "오늘", "어떠세요", "저는", "괜찮아요", "감사합니다"],
            ["안녕하세요.", "오늘", "어떠세요?", "저는", "괜찮아요,", "감사합니다!"],
        ),
        (
            "사과、바나나、귤을 샀어요.",
            ["사과", "바나나", "귤을", "샀어요"],
            ["사과、", "바나나、", "귤을", "샀어요."],
        ),
        (
            "这个 API 叫 fetch()，很好用。",
            ["这个", "API", "叫", "fetch", "很好用"],
            ["这个", "API", "叫", "fetch", "()，很好用。"],
        ),
        (
            "他说「她说『好』」。",
            ["他说", "她说", "好"],
            ["他说", "「她说", "『好』」。"],
        ),
        (
            "嗯…… 然后呢？",
            ["嗯", "然后呢"],
            ["嗯……", "然后呢？"],
        ),
        (
            "What?! Really!!!",
            ["What", "Really"],
            ["What?!", "Really!!!"],
        ),
        (
            "「開始」です。",
            ["開始", "です"],
            ["「開始」", "です。"],
        ),
    ],
)
def test_fifteen_multilingual_inputs_preserve_text_order_and_ownership(
    full_text, words, expected
):
    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, _segments(words)
    )

    assert _texts(result) == expected


def test_t17_uses_production_function_and_does_not_change_words_or_timestamps():
    full_text = 'He said "hello." then "goodbye".'
    words = ["He", "said", "hello", "then", "goodbye"]
    timestamp_segments = _segments(words)
    original = deepcopy(timestamp_segments)
    template_expected = ["He", "said", '"hello."', "then", '"goodbye".']

    result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, timestamp_segments
    )

    assert _texts(result) == template_expected
    assert "".join(_texts(result)) == full_text.replace(" ", "")
    assert len(result) == len(original)
    assert [item["start"] for item in result] == [item["start"] for item in original]
    assert [item["end"] for item in result] == [item["end"] for item in original]


def test_t18a_quote_state_does_not_cross_function_calls():
    full_text = 'He said "hello." then "goodbye".'
    full_result = qwen_asr_runner._attach_full_text_punctuation(
        full_text, _segments(["He", "said", "hello", "then", "goodbye"])
    )
    block_result = qwen_asr_runner._attach_full_text_punctuation(
        '." then "goodbye".', _segments(["then", "goodbye"])
    )

    assert _texts(full_result)[2].endswith('"')
    assert '"' in _texts(block_result)[0]


def test_c6_returns_raw_words_when_its_own_attach_step_loses_a_character(
    monkeypatch, caplog
):
    """C6：模拟挂标点步骤自身损坏时，必须退回未挂标点词流。"""
    full_text = 'He said "hello".'
    timestamp_segments = _segments(["He", "said", "hello"])
    real_apply = qwen_asr_runner._apply_punctuation_gap

    def drop_one_character(
        decorated, previous_index, current_index, previous_text, current_text
    ):
        real_apply(
            decorated,
            previous_index,
            current_index,
            previous_text,
            current_text[:-1],
        )

    monkeypatch.setattr(
        qwen_asr_runner, "_apply_punctuation_gap", drop_one_character
    )
    with caplog.at_level("ERROR", logger="backend.core.qwen_asr_runner"):
        result = qwen_asr_runner._attach_full_text_punctuation(
            full_text, timestamp_segments
        )

    assert result == timestamp_segments
    assert "字符不守恒" in caplog.text
    assert "位置" in caplog.text


def test_t7_second_pass_mismatch_stops_worker_and_marks_task_error(
    monkeypatch, tmp_path
):
    """T7：第二遍全文不守恒时，不得继续写字幕，任务必须进入失败态。"""
    from backend import main, worker

    job_dir = tmp_path / "p411-t7-job"
    cache_dir = job_dir / "cache" / "hash"
    job_dir.mkdir()
    cache_dir.mkdir(parents=True)
    video_path = job_dir / "input.mp4"
    video_path.write_bytes(b"video")
    raw_path = cache_dir / "qwen_raw.json"
    raw_path.write_text(json.dumps({
        "full_text": "Good.",
        "word_timestamps": [{
            "word": "Bad",
            "text": "Bad",
            "start": 0.0,
            "end": 1.0,
        }],
    }), encoding="utf-8")
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps({
        "video_path": str(video_path),
        "video_filename": "input.mp4",
        "outputMode": "srt",
        "model": "large-v2",
        "sourceLanguage": "en",
        "transcriptionTier": "standard",
    }), encoding="utf-8")
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "running", "run_id": "p411-t7"}),
        encoding="utf-8",
    )
    output = io.StringIO()
    subtitle_calls = []

    class Checkpoint:
        def __init__(self, *args, **kwargs):
            pass

        def load(self):
            return "start"

        def save(self, *args, **kwargs):
            pass

    monkeypatch.setattr(worker, "_real_stdout", output)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(config_path)])
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "p411-t7")
    monkeypatch.setattr(worker, "Checkpoint", Checkpoint)
    monkeypatch.setattr(
        worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log")
    )
    monkeypatch.setattr(
        worker, "_download_source_if_url", lambda config, directory, path: path
    )
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache_dir, "hash")
    )
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio")
    )
    def mismatched_transcription(*args):
        # 真实 G5-2 主引擎收据会在数据进入组句前执行同一守恒检查。
        receipt = worker.TranscriptionReceipt(
            routed_primary="qwen",
            actual_primary="qwen",
            word_source="qwen_word_timestamps",
            source_text="Good.",
            source_words=[{
                "text": "Bad",
                "start": 0.0,
                "end": 1.0,
            }],
                      language_decision=locked_decision(),
        artifact_language_check="ok",
                  )
        return worker.TranscriptionEnvelope([], receipt)

    monkeypatch.setattr(worker, "_step_transcribe", mismatched_transcription)
    monkeypatch.setattr(worker, "_step_subtitle", lambda *args: subtitle_calls.append(1))

    worker.main()
    messages = [
        json.loads(line)
        for line in output.getvalue().splitlines()
        if line.strip()
    ]
    error_message = next(message for message in messages if message["type"] == "error")

    assert subtitle_calls == []
    assert not list(job_dir.glob("*.srt"))

    job_id = job_dir.name
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {
            "id": job_id,
            "status": "running",
            "progress": 35,
            "run_id": "p411-t7",
        }
    })
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    main._sync_job_from_worker_message(job_id, {
        **error_message,
        "run_id": "p411-t7",
    })

    assert main.JOBS[job_id]["status"] == "error"
    assert main.JOBS[job_id]["progress"] == 100
    assert main.JOBS[job_id].get("result_srt") is None
    assert main.JOBS[job_id].get("output_ready") is not True
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["status"] == "error"


class _P411BoundaryBackend:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls += 1
        return self.response


class _P411ExplodingBoundaryBackend:
    def translate_raw_json(self, prompt, system_prompt=None):
        raise AssertionError("缓存恢复路径不应再次调用断句模型")


# G12 第三批 B（A+ §11，2026-09-28）：原 T18b「分组第二遍贴标点是最终结果」已移除。
# 生产收据的全文由词拼回（transcriber_v2 `_delivery_source_text`），分组第二遍贴标点
# 结构上只能「成功但一字不改」或「失败原样退回」；识别阶段那一处照旧记账并告知用户。
# 新合同见 test/test_g12b3b_aplus.py::test_grouping_does_not_reattach_punctuation。
