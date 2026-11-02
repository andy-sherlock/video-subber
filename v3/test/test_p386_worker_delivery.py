"""P386/P387/P388 收口：Worker 侧成品交付（B3 回退后的新流程）。

对应方案 §5.1 的十二步流程与七条铁律：

  铁律 1  一轮只认本轮验证过的成品
  铁律 2  目标同名文件：内容一致则采用，内容不同则挑空闲名。
          绝不覆盖、绝不归档、绝不改名用户已有文件
  铁律 3  未核对通过前绝不删除任何文件
  铁律 4  任何删除动作前先过 A11 路径安全检查
  铁律 5  「完成」只有在货确认送达后才能宣告
  铁律 7  改名落正式名、宣布完成之前各有一次轮次检查，紧贴动作前一行。
          实际保证强度：把旧轮 Worker 写用户目录的窗口从"整个复制时长"压到
          微秒级，**不是消灭**——检查是"读 run_id 返回布尔"，不是原子操作。
          彻底关闭需改父进程的 Worker 生命周期，不在本次范围

被测契约（实现于 backend/worker.py）：

  _still_current_run(job_dir, my_run_id) -> bool
      读 meta.json 的 run_id 比对。读不到一律返回 False。

  _deliver_outputs(job_dir, config, run_id, *, srt_path, video_path,
                   output_mode, on_delivered) -> bool
      执行 §5.1 第 2–12 步。送达并确认仍是当前轮之后调用 on_delivered()
      宣告完成，再做第 12 步清理。
      返回 True = 已宣告完成；False = 判定自己不是当前轮，未宣告。
      核对失败 / 改名失败等真错误抛 DeliveryError。
"""

import json
import os
import shutil
import types
from pathlib import Path

import pytest

from backend import worker


SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"
OTHER_SRT_BODY = "1\n00:00:00,000 --> 00:00:02,000\n这是用户自己的旧字幕\n"


def _write_meta(job_dir: Path, run_id: str) -> None:
    (job_dir / "meta.json").write_text(
        json.dumps({"run_id": run_id, "status": "running"}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture
def scene(tmp_path):
    """一个 srt 模式的任务：任务目录里有成品，保存目录是空的。"""
    job_dir = tmp_path / "jobs" / "job1"
    job_dir.mkdir(parents=True)
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    srt = job_dir / "movie.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    _write_meta(job_dir, "run-A")

    calls = []
    return types.SimpleNamespace(
        job_dir=job_dir,
        save_dir=save_dir,
        srt=srt,
        target=save_dir / "movie.srt",
        run_id="run-A",
        config={"savePath": str(save_dir), "outputMode": "srt"},
        calls=calls,
        on_delivered=lambda: calls.append(
            {"target_exists": (save_dir / "movie.srt").is_file()}
        ),
    )


def _deliver(scene, **overrides):
    kwargs = {
        "srt_path": str(scene.srt),
        "video_path": None,
        "output_mode": "srt",
        "on_delivered": scene.on_delivered,
    }
    kwargs.update(overrides)
    return worker._deliver_outputs(
        scene.job_dir, scene.config, scene.run_id, **kwargs
    )


def _part_files(save_dir: Path):
    return list(save_dir.glob(".video-subber-*.part"))


# ── 主干 ──

def test_01_delivers_srt_into_save_dir(scene):
    """成品被送到保存目录，内容与源逐字节一致。"""
    assert _deliver(scene) is True
    assert scene.target.is_file()
    assert scene.target.read_text(encoding="utf-8") == SRT_BODY
    assert not _part_files(scene.save_dir), "临时文件必须清理干净"


def test_02_announces_done_only_after_file_landed(scene):
    """铁律 5：宣告完成的那一刻，货必须已经在保存目录里。"""
    assert _deliver(scene) is True
    assert len(scene.calls) == 1, "必须宣告完成恰好一次"
    assert scene.calls[0]["target_exists"] is True, (
        "on_delivered 被调用时目标文件还不存在 —— 说明先宣告了完成再送货"
    )


# ── 铁律 2：同名文件 ──

def test_03_identical_existing_target_is_adopted(scene):
    """同名且内容一致 → 直接采用，不产生 (2)，不留临时文件。"""
    scene.target.write_text(SRT_BODY, encoding="utf-8")
    before = scene.target.stat().st_mtime_ns

    assert _deliver(scene) is True

    assert scene.target.read_text(encoding="utf-8") == SRT_BODY
    assert not (scene.save_dir / "movie (2).srt").exists(), "内容一致不该产生重复成品"
    assert not _part_files(scene.save_dir)
    assert scene.target.stat().st_mtime_ns == before, "内容一致时不该重写目标文件"


def test_04_different_existing_target_gets_free_name(scene):
    """同名但内容不同 → 挑空闲名；用户原文件一个字节都不许动。"""
    scene.target.write_text(OTHER_SRT_BODY, encoding="utf-8")

    assert _deliver(scene) is True

    assert scene.target.read_text(encoding="utf-8") == OTHER_SRT_BODY, (
        "用户已有的同名文件被覆盖/归档/改名了"
    )
    assert (scene.save_dir / "movie (2).srt").read_text(encoding="utf-8") == SRT_BODY
    # 绝不归档：保存目录里不许多出任何以原名为前缀的时间戳备份
    names = sorted(p.name for p in scene.save_dir.iterdir())
    assert names == ["movie (2).srt", "movie.srt"], f"保存目录多出了文件: {names}"


def test_05_picks_next_free_name_when_2_is_taken(scene):
    """(2) 也被占且内容不同 → 继续挑 (3)。"""
    scene.target.write_text(OTHER_SRT_BODY, encoding="utf-8")
    (scene.save_dir / "movie (2).srt").write_text("另一个旧文件\n", encoding="utf-8")

    assert _deliver(scene) is True

    assert (scene.save_dir / "movie (3).srt").read_text(encoding="utf-8") == SRT_BODY
    assert scene.target.read_text(encoding="utf-8") == OTHER_SRT_BODY
    assert (scene.save_dir / "movie (2).srt").read_text(encoding="utf-8") == "另一个旧文件\n"


# ── 铁律 3：核对 ──

def test_06_checksum_mismatch_aborts_without_landing(scene, monkeypatch):
    """复制出来的字节和源对不上 → 不改名、不宣告完成、临时文件清掉。"""
    real_fsync = worker._fsync_path

    def fsync_then_corrupt(path):
        """在临时文件落盘之后、核对之前弄坏它 —— 真实的字节不符。"""
        real_fsync(path)
        candidate = Path(path)
        if candidate.name.startswith(".video-subber-"):
            with open(candidate, "ab") as handle:
                handle.write(b"X")

    monkeypatch.setattr(worker, "_fsync_path", fsync_then_corrupt)

    with pytest.raises(worker.DeliveryError):
        _deliver(scene)

    assert not scene.target.exists(), "核对没通过却把文件落到了正式名"
    assert not _part_files(scene.save_dir), "失败路径必须清理自己的临时文件"
    assert scene.calls == [], "核对失败还宣告了完成"
    assert scene.srt.is_file(), "铁律 3：核对没过就删了任务目录里的成品"


def test_07_temp_file_cleaned_when_rename_fails(scene, monkeypatch):
    """R1：改名失败时必须在 finally 里清掉自己的 .part。"""
    def boom(*args, **kwargs):
        raise OSError("模拟改名失败")

    monkeypatch.setattr(worker.os, "link", boom)
    monkeypatch.setattr(worker.os, "rename", boom)

    with pytest.raises(worker.DeliveryError):
        _deliver(scene)

    assert not _part_files(scene.save_dir), "改名失败后临时文件泄漏了"
    assert scene.calls == []


# ── 铁律 7：轮次边界 ──

def test_08_stale_run_blocked_before_rename(scene, monkeypatch):
    """旧 run 在改名之前被拦 → 保存目录零改动、临时文件已清理、不宣告完成。"""
    real_sha = worker._sha256_of

    def sha_then_rotate_run(path):
        digest = real_sha(path)
        _write_meta(scene.job_dir, "run-B")   # 第 5 步之后、第 7 步之前换轮
        return digest

    monkeypatch.setattr(worker, "_sha256_of", sha_then_rotate_run)

    assert _deliver(scene) is False

    assert list(scene.save_dir.iterdir()) == [], "旧 run 动了用户保存目录"
    assert scene.calls == [], "旧 run 宣告了完成"


def test_09_stale_run_blocked_before_done(scene, monkeypatch):
    """旧 run 在写 done 之前被拦 → 货可能已送达，但绝不宣告完成。

    这就是方案 §5.2 Finding A 承认并接受的窗口。"""
    real_fsync = worker._fsync_path

    def fsync_then_rotate_run(path):
        real_fsync(path)
        if Path(path) == scene.save_dir:      # 第 9 步：fsync 目标父目录
            _write_meta(scene.job_dir, "run-B")

    monkeypatch.setattr(worker, "_fsync_path", fsync_then_rotate_run)

    assert _deliver(scene) is False
    assert scene.calls == [], "已经不是当前轮却宣告了完成"


def test_10_unreadable_meta_is_treated_as_not_current(scene):
    """读不到权威 run_id → 一律当作不是当前轮，不做任何不可逆动作。"""
    (scene.job_dir / "meta.json").write_text("{ 坏掉的 JSON", encoding="utf-8")

    assert _deliver(scene) is False
    assert list(scene.save_dir.iterdir()) == []
    assert scene.calls == []
    assert scene.srt.is_file()


def test_11_still_current_run_defaults_to_false(scene):
    """_still_current_run 的三条判定。"""
    assert worker._still_current_run(scene.job_dir, "run-A") is True
    assert worker._still_current_run(scene.job_dir, "run-B") is False
    assert worker._still_current_run(scene.job_dir, "") is False
    (scene.job_dir / "meta.json").unlink()
    assert worker._still_current_run(scene.job_dir, "run-A") is False


# ── 第 12 步：清理 ──

def test_12_cleans_cached_audio_but_keeps_the_job_copy(scene):
    """送达之后只清 cache 里的 audio.wav，任务目录内的成品副本必须留着。

    父进程收到完成消息后要回任务目录把成品打开核对，删了它成品记录会整组作废。
    磁盘占用由独立的清缓存功能负责。"""
    cache_version = scene.job_dir / "cache" / "v1"
    cache_version.mkdir(parents=True)
    audio = cache_version / "audio.wav"
    audio.write_bytes(b"RIFF fake wav")

    assert _deliver(scene) is True

    assert scene.target.is_file(), "清理不得影响已送达的成品"
    assert scene.srt.is_file(), "任务目录内的成品副本要留给父进程核对"
    assert not audio.exists(), "cache 里的 audio.wav 应在送达后清掉"


def test_13_a11_refuses_symlinked_cache(scene, tmp_path):
    """铁律 4：cache 指向任务目录外时，A11 必须拦下删除动作。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_audio = outside / "audio.wav"
    outside_audio.write_bytes("用户在别处的重要文件".encode("utf-8"))
    (scene.job_dir / "cache").symlink_to(outside, target_is_directory=True)

    assert _deliver(scene) is True

    assert outside_audio.is_file(), "A11 没拦住：跟着软链接删到了任务目录之外"
    assert outside_audio.read_bytes() == "用户在别处的重要文件".encode("utf-8")


# ── A12：无可信 SRT 不发布视频 ──

def test_15_srt_that_exists_but_is_not_a_subtitle_is_refused(scene):
    """字幕文件在，但内容不是字幕 → 不交付、不宣告完成。

    父进程收到完成消息后会把这个文件打开、检查里面有没有时间轴。Worker 这边
    必须用同一套标准——弱标准在前面放行、强标准在后面否决，结果就是成品已经
    送出去了，用户却看到"已完成"且点不到成品入口。"""
    scene.srt.write_text("这只是一段普通文字，没有时间轴\n", encoding="utf-8")

    with pytest.raises(worker.DeliveryError):
        _deliver(scene)

    assert list(scene.save_dir.iterdir()) == [], "核验不合格却把文件送出去了"
    assert scene.calls == [], "核验不合格却宣告了完成"


def test_16_video_that_exists_but_is_not_media_is_refused(scene):
    """视频文件在，但不是有效媒体 → burn 模式不交付。"""
    video = scene.job_dir / "movie_zh.mp4"
    video.write_bytes(b"this is not a video at all")
    scene.config["outputMode"] = "burn"

    with pytest.raises(worker.DeliveryError):
        _deliver(scene, video_path=str(video), output_mode="burn")

    assert list(scene.save_dir.iterdir()) == []
    assert scene.calls == []


def test_14_burn_mode_without_srt_delivers_nothing(scene):
    """A12：burn 模式缺 SRT 时，视频不得单独送出，也不得宣告完成。"""
    video = scene.job_dir / "movie_zh.mp4"
    video.write_bytes(b"fake mp4 payload")
    scene.srt.unlink()
    scene.config["outputMode"] = "burn"

    with pytest.raises(worker.DeliveryError):
        _deliver(scene, video_path=str(video), output_mode="burn")

    assert list(scene.save_dir.iterdir()) == []
    assert scene.calls == []
