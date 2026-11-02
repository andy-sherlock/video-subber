"""P453：识别阶段不再把内部计数当进度贴给用户。

`completed_segments` 有 4 个写入点、3 种含义（Whisper 字幕段数 / Qwen 音频块序号 /
常量 1），1 个读取点统一渲染成「已识别 N 段」，于是界面出现约 300→2→10 的假倒退。
**不是转录倒退，是给数字贴错标签。**

P375 已裁决识别阶段不显示这类数字；G9 的 dcc18a3 又把它加了回来，本条是第二次删。
标签本身必须保留——`completed` 仍是 `if` 条件的一半，连它一起拿掉会让文案掉成默认值。
"""

from backend import job_state


def test_speech_recognition_keeps_its_label_but_shows_no_segment_count():
    state = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe", "progress": 17,
        "transcription_phase": "recognizing_speech", "completed_segments": 20,
        "audio_total_duration": 180, "audio_processed_duration": 60,
    })

    assert state["display_message"] == "正在识别语音，请稍等"
    assert state["percent"] == 17
    assert "已识别" not in (state["activity"] or "")
    assert "20" not in (state["activity"] or "")


def test_sound_analysis_keeps_its_label_but_shows_no_segment_count():
    state = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe", "progress": 9,
        "transcription_phase": "analyzing_sound", "completed_segments": 7,
    })

    assert state["display_message"] == "正在分析声音类型，请稍等"
    assert "已识别" not in (state["activity"] or "")


def test_label_still_appears_when_only_completed_segments_is_set():
    """`completed` 仍是 if 条件的一半：只有它、没有 transcription_phase 时，
    标签必须仍然是「正在识别语音，请稍等」，不能掉成默认文案。"""
    state = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe", "progress": 5,
        "completed_segments": 3,
    })

    assert state["display_message"] == "正在识别语音，请稍等"
