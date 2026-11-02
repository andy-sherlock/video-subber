"""P454/P457：模型吞掉词内标点时的容忍匹配，以及按块记录挂标点成败。

真实任务 1a1b9f8145（47 分钟）里，十块原文共 805 个句末标点，正式正文却是 0 个。
根因是模型返回的词级 token **把词内标点吃掉了**（`twenty-five` → `twentyfive`），
逐字比对必然失配，`_attach_full_text_punctuation` 于是整块退回未挂标点的词流。

十块的第一处失配是三种形状：连字符 7 块、破折号 2 块、缩写句点 1 块。
全片只需放行 33 个词内标点字符（`-`×23、`—`×7、`.`×3）。
"""

from backend.core import qwen_asr_runner


def _segments(words):
    return [
        {"text": word, "start": i * 0.5, "end": i * 0.5 + 0.4, "speaker": "A"}
        for i, word in enumerate(words)
    ]


def _texts(segments):
    return [segment["text"] for segment in segments]


# ── 三种真实形状（取自真实任务的十块第一处失配）──────────────

def test_hyphen_swallowed_by_model_token_is_restored():
    """块 0/1/4/5/6/7/9 的形状：原文 twenty-five，模型给 twentyfive。"""
    result = qwen_asr_runner._attach_full_text_punctuation(
        "out of ten twenty-five. This is",
        _segments(["out", "of", "ten", "twentyfive", "This", "is"]),
    )

    assert _texts(result) == ["out", "of", "ten", "twenty-five.", "This", "is"]


def test_em_dash_swallowed_by_model_token_is_restored():
    """块 2 的形状：原文 plan—yes，模型给 planyes。"""
    result = qwen_asr_runner._attach_full_text_punctuation(
        "our whole plan—yes, we had",
        _segments(["our", "whole", "planyes", "we", "had"]),
    )

    assert _texts(result) == ["our", "whole", "plan—yes,", "we", "had"]


def test_abbreviation_periods_swallowed_by_model_token_are_restored():
    """块 3 的形状：原文 U.S.，模型给 US（合并成一个词，不是拆成 U 和 S）。"""
    result = qwen_asr_runner._attach_full_text_punctuation(
        "get in the U.S. and Europe.",
        _segments(["get", "in", "the", "US", "and", "Europe"]),
    )

    assert _texts(result) == ["get", "in", "the", "U.S.", "and", "Europe."]


# ── 容忍只在逐字比对失败后启用（合同 A7）────────────────────

def test_tolerance_does_not_change_words_that_already_match():
    """词表本身带着词内标点时，走原路径，输出逐字节不变。"""
    result = qwen_asr_runner._attach_full_text_punctuation(
        "rock'n'roll is fun.", _segments(["rock'n'roll", "is", "fun"])
    )

    assert _texts(result) == ["rock'n'roll", "is", "fun."]


def test_tolerance_does_not_rescue_a_real_text_mismatch():
    """真正的文字对不上（不是标点）仍然整块退回，容忍不得放宽到吞字母。"""
    words = _segments(["Hello", "wrld"])
    result = qwen_asr_runner._attach_full_text_punctuation("Hello world.", words)

    assert result == words


# ── 守恒不变量仍然生效（合同 A1 的失败场景）──────────────────

def test_restored_word_keeps_character_conservation():
    """挂回去的词内标点必须计入词文本，否则守恒检查会让整块退回。"""
    result = qwen_asr_runner._attach_full_text_punctuation(
        "a twenty-five b", _segments(["a", "twentyfive", "b"])
    )

    joined = "".join(seg["text"] for seg in result)
    assert "".join("a twenty-five b".split()) == joined


# ── P457：按块记录挂标点成败 ──────────────────────────────

def test_attach_reports_success_and_failure_separately():
    """调用方必须能区分「挂上了」和「没挂上」，不能只靠返回值长得一样。"""
    ok_words = _segments(["out", "of", "ten", "twentyfive"])
    ok, _ = qwen_asr_runner.attach_full_text_punctuation_checked(
        "out of ten twenty-five.", ok_words
    )
    assert ok is True

    bad_words = _segments(["Hello", "wrld"])
    ok2, returned = qwen_asr_runner.attach_full_text_punctuation_checked(
        "Hello world.", bad_words
    )
    assert ok2 is False
    assert returned == bad_words


def test_chunk_records_whether_punctuation_was_attached():
    """_chunk_words 的调用方要能拿到本块的挂标点结果，用于混合场景记账。"""
    chunk = {
        "raw_text": "out of ten twenty-five.",
        "raw_segments": _segments(["out", "of", "ten", "twentyfive"]),
        "t_start": 0.0,
        "t_end": 10.0,
    }
    words, attached = qwen_asr_runner.chunk_words_checked(chunk)

    assert attached is True
    assert any("twenty-five" in w["text"] for w in words)


# ── P457 补完：按块记账 + 混合场景 + 告知用户 ──────────────

def _ok_chunk(t_start):
    return {
        "raw_text": "out of ten twenty-five.",
        "raw_segments": _segments(["out", "of", "ten", "twentyfive"]),
        "t_start": t_start, "t_end": t_start + 300.0,
    }


def _broken_chunk(t_start):
    """文字真对不上（不是词内标点）——挂标点必然失败的那种块。"""
    return {
        "raw_text": "completely different words here.",
        "raw_segments": _segments(["out", "of", "ten", "twentyfive"]),
        "t_start": t_start, "t_end": t_start + 300.0,
    }


def test_transcribed_chunk_records_whether_punctuation_was_attached():
    """每块自己带着「挂上了没有」，而不是只留一行日志。"""
    ok = qwen_asr_runner._refresh_chunk_delivery([_ok_chunk(0.0)], "en")[0]
    bad = qwen_asr_runner._refresh_chunk_delivery([_broken_chunk(0.0)], "en")[0]

    assert ok["punctuation_attached"] is True
    assert bad["punctuation_attached"] is False


def test_mixed_run_can_tell_which_chunk_failed():
    """十块里第 3 块失配：那一块 False、其余九块 True。

    真实任务是 10/10 全失败，看起来像一个整体故障。
    「一半好一半坏」这个状态以前既没代码也没测试（走查第六问抓到的）。
    """
    chunks = [_ok_chunk(i * 300.0) for i in range(10)]
    chunks[2] = _broken_chunk(600.0)

    refreshed = qwen_asr_runner._refresh_chunk_delivery(chunks, "en")
    flags = [c["punctuation_attached"] for c in refreshed]

    assert flags[2] is False
    assert all(flags[i] for i in range(10) if i != 2)
    assert qwen_asr_runner.count_chunks_missing_punctuation(refreshed) == 1


def test_all_good_run_reports_zero_missing():
    chunks = [_ok_chunk(i * 300.0) for i in range(3)]
    refreshed = qwen_asr_runner._refresh_chunk_delivery(chunks, "en")

    assert qwen_asr_runner.count_chunks_missing_punctuation(refreshed) == 0


def test_full_text_shorter_than_word_list_falls_back_safely():
    """⑦.5：容忍匹配的越界分支（正文提前结束）此前零覆盖。

    原文比词表短时必须整块原样退回，不能抛 IndexError。
    """
    words = _segments(["Hello", "world"])
    result = qwen_asr_runner._attach_full_text_punctuation("Hello", words)

    assert result == words


def test_count_helper_tolerates_none_and_junk():
    """计数辅助函数的空输入与脏输入守卫，此前零覆盖。"""
    assert qwen_asr_runner.count_chunks_missing_punctuation(None) == 0
    assert qwen_asr_runner.count_chunks_missing_punctuation([]) == 0
    assert qwen_asr_runner.count_chunks_missing_punctuation(["不是字典"]) == 0
