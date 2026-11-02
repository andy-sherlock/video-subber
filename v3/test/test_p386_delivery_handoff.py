"""P386 交接缝：工人送完货 → 管家核对成品。

上一轮两次方案外审都没发现这里的问题，因为单看工人的代码是对的、单看管家的
代码也是对的——**只有把两边接起来真跑一遍才会暴露**。

接缝上的约定：

  工人宣告完成时，会告诉管家「成品在任务目录的这两个路径」。管家不直接信，
  它要按这个地址走一趟，把文件打开看一眼（字幕读一遍、视频 ffprobe 探一下），
  看到了才登记成品。

  所以工人在宣告完成之后，**不得删掉任务目录里管家还要去核对的那两个文件**。
  磁盘占用由独立的清缓存功能负责，不由交付流程顺手解决。

  cache 下的 audio.wav 不在此列：管家从来不核对它（B5 继续删）。
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend import main, worker


SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"


def _make_probe_video(path: Path) -> None:
    """造一个 ffprobe 认得的极小视频，让管家的核对是真的核对。"""
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-f", "lavfi",
         "-i", "color=c=black:s=32x32:d=0.1", "-y", str(path)],
        capture_output=True,
    )
    if result.returncode != 0 or not path.is_file():
        pytest.skip("本机没有可用的 ffmpeg，跳过需要真实视频的接缝测试")


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    """一个准备交付的任务。JOBS_DIR 必须在任何核对之前就位。"""
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / "h1"
    job_dir.mkdir(parents=True)
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})

    srt = job_dir / "clip.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    cache = job_dir / "cache" / "v1"
    cache.mkdir(parents=True)
    audio = cache / "audio.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 512)

    def build(mode):
        video = None
        if mode in ("burn", "mux"):
            name = "clip_zh.mp4" if mode == "burn" else "clip_zh_sub.mp4"
            video = job_dir / name
            _make_probe_video(video)
        config = {"outputMode": mode, "savePath": str(save_dir),
                  "video_filename": "clip.mp4"}
        (job_dir / "meta.json").write_text(
            json.dumps({"status": "running", "run_id": "run-1", "config": config}),
            encoding="utf-8",
        )
        return config, video

    return type("Handoff", (), {
        "job_dir": job_dir, "save_dir": save_dir, "srt": srt,
        "audio": audio, "build": staticmethod(build),
    })


def _deliver_then_verify(scene, mode):
    """工人整套跑完（含第 12 步清理），然后管家按工人给的地址去核对。"""
    config, video = scene.build(mode)
    delivered = worker._deliver_outputs(
        scene.job_dir, config, "run-1",
        srt_path=str(scene.srt),
        video_path=str(video) if video else None,
        output_mode=mode,
        on_delivered=lambda: None,
    )
    assert delivered is True, "交付本身就该成功"
    fields = main._verified_result_fields(
        "h1", "run-1",
        {"result_srt": str(scene.srt),
         "result_video": str(video) if video else None},
        output_ready=True,
    )
    return fields, video


@pytest.mark.parametrize("mode", ["srt", "burn", "mux"])
def test_parent_can_still_verify_the_outputs_after_delivery(handoff, mode):
    """三种输出方式，管家在工人送完货之后都必须还能核对到成品。"""
    fields, _video = _deliver_then_verify(handoff, mode)

    assert fields["result_srt"] is not None, (
        f"{mode} 模式：管家核对不到字幕，成品记录会被整组作废，"
        f"「在文件夹中显示」按钮会消失"
    )
    assert fields["output_ready"] is True
    # can_show_output_folder 的判据
    assert fields["output_ready"] and fields["result_srt"], (
        f"{mode} 模式：用户会看到「完成」但点不到成品入口"
    )


@pytest.mark.parametrize("mode", ["burn", "mux"])
def test_video_record_survives_delivery(handoff, mode):
    """带视频的两种模式，成品视频的记录不能在交付后变空。"""
    fields, video = _deliver_then_verify(handoff, mode)

    assert fields["result_video"] is not None, (
        f"{mode} 模式：管家核对不到成品视频，软件里那条记录会变空"
    )
    assert Path(fields["result_video"]) == video


@pytest.mark.parametrize("mode", ["srt", "burn", "mux"])
def test_crash_recovery_still_recognizes_a_finished_job(handoff, mode):
    """任务跑完的瞬间崩溃（状态还停在 running）→ 重开后必须认得出它做完了。"""
    config, video = handoff.build(mode)
    worker._deliver_outputs(
        handoff.job_dir, config, "run-1",
        srt_path=str(handoff.srt),
        video_path=str(video) if video else None,
        output_mode=mode, on_delivered=lambda: None,
    )
    meta = {
        "status": "running", "run_id": "run-1", "config": config,
        "result_srt": str(handoff.srt),
        "result_video": str(video) if video else None,
        "result_run_id": "run-1", "output_ready": True,
    }

    assert main._has_valid_final_outputs(handoff.job_dir, meta) is True, (
        f"{mode} 模式：崩溃重开后认不出这任务已完成，会显示成「可继续」"
    )


def test_reveal_never_points_at_the_users_own_same_named_file(handoff):
    """同名冲突时成品落到 (2)，「在文件夹中显示」绝不能选中用户那个旧文件。

    Worker 不回报实际落点，父进程只能按配置推导出原始名字。那个名字下面躺着的
    是用户自己的文件——推导出来的路径存在，不等于它就是本轮成品。这正是 P386
    的"认错对象"。没有可信的精确路径时，只应打开目录。"""
    config, _video = handoff.build("srt")
    users_own = handoff.save_dir / "clip.srt"
    users_own.write_text("用户自己放在这里的旧字幕\n", encoding="utf-8")

    worker._deliver_outputs(
        handoff.job_dir, config, "run-1", srt_path=str(handoff.srt),
        video_path=None, output_mode="srt", on_delivered=lambda: None,
    )

    # 内容不同 → 成品避让到 (2)，用户的文件毫发无损
    assert (handoff.save_dir / "clip (2).srt").is_file()
    assert users_own.read_text(encoding="utf-8") == "用户自己放在这里的旧字幕\n"

    meta = {
        "status": "done", "run_id": "run-1", "config": config,
        "result_srt": str(handoff.srt), "result_video": None,
        "result_run_id": "run-1", "output_ready": True,
    }
    fields = main._public_result_projection(meta, handoff.job_dir)
    target = main._saved_output_target(meta, fields, handoff.job_dir)

    assert target != users_own, "reveal 把用户带到了他自己的旧文件"
    assert target == handoff.save_dir, "没有可信的精确路径时，只应打开目录"


def test_cached_audio_is_still_cleaned_up(handoff):
    """B5 不变：管家从来不核对 audio.wav，它照删不误。"""
    config, _video = handoff.build("srt")
    worker._deliver_outputs(
        handoff.job_dir, config, "run-1", srt_path=str(handoff.srt),
        video_path=None, output_mode="srt", on_delivered=lambda: None,
    )

    assert not handoff.audio.exists(), "cache 里的 audio.wav 应该被清掉"
