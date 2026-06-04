"""自动校准物理特征阈值 — 不手动选段，从音频自身分布找音乐/对话分界线。

用法:
    PYTHONPATH=. python -m video_subber.calibrate_features <video_path>

原理:
    1. 用 Whisper 转录全片（无过滤），收集所有 segment 的物理特征
    2. 对每个特征，找分布中的最大间隙（最大类间方差）作为阈值
    3. 打印推荐 FILTER_CONFIG 和校准报告
"""
import sys
import json
import wave
import logging
import numpy as np
from pathlib import Path

_log = logging.getLogger("calibrate_features")


def _compute_features(audio_path: str, sr: int, start: float, end: float) -> dict:
    """对音频片段提取 3 个物理特征，返回 dict 或 None（片段太短）。"""
    dur = end - start
    if dur < 1.0:
        return None

    try:
        wf = wave.open(audio_path, "rb")
        full_audio = np.frombuffer(wf.readframes(wf.getnframes()),
                                    dtype=np.int16).astype(np.float32) / 32768.0
        wf.close()
    except Exception:
        return None

    s = max(0, int(start * sr))
    e = min(len(full_audio), int(end * sr))
    if e - s < int(sr * 0.5):
        return None

    y = full_audio[s:e]

    try:
        import librosa
    except ImportError:
        return None

    rms = librosa.feature.rms(y=y, frame_length=512, hop_length=256)[0]
    rms_var = float(np.var(rms)) if len(rms) > 1 else 0.0

    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    cent_mean = float(np.mean(cent)) if len(cent) > 0 else 0.0

    zcr = librosa.feature.zero_crossing_rate(y, frame_length=512, hop_length=256)[0]
    zcr_mean = float(np.mean(zcr)) if len(zcr) > 0 else 0.0

    return {"rms_var": rms_var, "cent_mean": cent_mean, "zcr_mean": zcr_mean}


def _find_threshold(values: list[float], direction: str = "below") -> float:
    """在值分布中找音乐/对话的最佳分界线。

    方向 'below': 音乐值更低 → 阈值应该在低值区和高值区的间隙里
    用 Otsu 类最大间隙法 -> 在排序后的值里找相邻差值最大的位置。
    """
    if len(values) < 10:
        # 样本太少，返回保守默认值
        return 0.0005 if direction == "below" else 0.06

    sorted_vals = np.sort(values)

    # 找到相邻值之间的最大间隙
    gaps = np.diff(sorted_vals)
    gap_idx = int(np.argmax(gaps))

    # 阈值 = 间隙左边那一段的最大值 + 小幅安全边距
    left_cluster_max = sorted_vals[gap_idx]

    if direction == "below":
        # 音乐值更低 → 阈值放在左边集群顶部
        return float(left_cluster_max + (gaps[gap_idx] * 0.3))
    else:
        # 音乐值更高 → 阈值放在间隙中间
        return float(left_cluster_max + gaps[gap_idx] * 0.5)


def _analyze_distribution(values: list[float], name: str):
    """分析特征分布，打印摘要。"""
    if not values:
        return
    arr = np.array(values)
    pcts = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    print(f"  {name}: n={len(arr)}  "
          f"min={arr.min():.6f}  "
          f"median={np.median(arr):.6f}  "
          f"max={arr.max():.6f}")
    print(f"    百分位: " +
          "  ".join(f"P{p}={np.percentile(arr, p):.6f}" for p in pcts))


def run(video_path: str, model_size: str = "tiny"):
    """主入口：转录 + 特征收集 + 阈值计算 + 报告。"""
    import tempfile, subprocess, os

    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")

    print("=" * 64)
    print("物理特征自动校准")
    print("=" * 64)
    print(f"视频: {video_path}")
    print(f"模型: {model_size}")
    print()

    # ── 1. 提取音频 ──
    print("[1/4] 提取音频…")
    tmp_dir = tempfile.mkdtemp(prefix="cal_")
    audio_path = os.path.join(tmp_dir, "audio.wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn",
         "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"音频提取失败: {r.stderr[-500:]}")
    print(f"  ✓ {audio_path}")
    print()

    # ── 2. 转录（无过滤）──
    print(f"[2/4] Whisper 转录 (模型: {model_size})…")
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments_iter, info = model.transcribe(
        audio_path, beam_size=5,
        condition_on_previous_text=False,
        log_prob_threshold=-1.0,
    )
    print(f"  语言: {info.language} (概率 {info.language_probability:.2f})")

    # 收集所有 segment（不过滤）
    raw_segments = []
    for seg in segments_iter:
        text = seg.text.strip()
        if not text or len(text) < 2:
            continue
        raw_segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": text[:80],
            "nsp": float(getattr(seg, "no_speech_prob", 0.0)),
        })

    print(f"  ✓ {len(raw_segments)} 段")
    print()

    # ── 3. 对每段算物理特征 ──
    print("[3/4] 计算物理特征…")
    features = []
    wf = wave.open(audio_path, "rb")
    sr = wf.getframerate()
    wf.close()

    for i, seg in enumerate(raw_segments):
        feats = _compute_features(audio_path, sr, seg["start"], seg["end"])
        if feats is not None:
            feats["idx"] = i
            feats["start"] = seg["start"]
            feats["end"] = seg["end"]
            feats["nsp"] = seg["nsp"]
            feats["text"] = seg["text"]
            features.append(feats)

    print(f"  ✓ {len(features)} 段有效 (丢弃 {len(raw_segments)-len(features)} 段太短)")
    print()

    # 提取三个特征数组
    rms_vals = [f["rms_var"] for f in features]
    cent_vals = [f["cent_mean"] for f in features]
    zcr_vals = [f["zcr_mean"] for f in features]

    # ── 4. 分布分析 + 阈值计算 ──
    print("[4/4] 分布分析 + 阈值计算")
    print("-" * 64)

    print("特征分布:")
    _analyze_distribution(rms_vals, "RMS 方差")
    _analyze_distribution(cent_vals, "频谱质心")
    _analyze_distribution(zcr_vals, "零交叉率")
    print()

    # 自动找到每个特征的最佳阈值
    rms_thr = _find_threshold(rms_vals, direction="below")
    cent_thr = _find_threshold(cent_vals, direction="below")
    zcr_thr = _find_threshold(zcr_vals, direction="below")

    # 安全夹持：阈值不能比理论值更极端
    rms_thr = max(0.0001, min(0.01, rms_thr))
    cent_thr = max(500, min(3000, cent_thr))
    zcr_thr = max(0.02, min(0.15, zcr_thr))

    print("推荐阈值:")
    print(f"  rms_var:   {rms_thr:.6f}")
    print(f"  cent_mean: {cent_thr:.0f}")
    print(f"  zcr_mean:  {zcr_thr:.4f}")
    print()

    # 验证：用推荐阈值对现有段投票
    music_votes = []
    for f in features:
        v = sum([
            f["rms_var"] < rms_thr,
            f["cent_mean"] < cent_thr,
            f["zcr_mean"] < zcr_thr,
        ])
        music_votes.append(v)

    n_music = sum(1 for v in music_votes if v >= 2)
    n_speech = sum(1 for v in music_votes if v == 0)
    n_uncertain = len(music_votes) - n_music - n_speech

    print(f"验证 ({len(features)} 段):")
    print(f"  判音乐 (≥2票): {n_music} 段 ({100*n_music/len(features):.1f}%)")
    print(f"  判说话 (0票):  {n_speech} 段 ({100*n_speech/len(features):.1f}%)")
    print(f"  中间 (1票):    {n_uncertain} 段 ({100*n_uncertain/len(features):.1f}%)")
    print()

    # 展示判"音乐"的段（用户可检查是否合理）
    if n_music > 0:
        print("判为'音乐'的段 (前 10):")
        music_segs = [f for f, v in zip(features, music_votes) if v >= 2]
        for f in music_segs[:10]:
            print(f"  [{f['start']:7.1f}s - {f['end']:7.1f}s] "
                  f"rms={f['rms_var']:.6f} cent={f['cent_mean']:.0f} zcr={f['zcr_mean']:.4f} "
                  f"nsp={f['nsp']:.4f} | {f['text'][:60]}")
        print()

    # 输出可直接粘贴的配置
    print("=" * 64)
    print("FILTER_CONFIG (可直接粘贴到 transcriber.py):")
    print("=" * 64)
    config = {
        "rms_var": round(rms_thr, 6),
        "cent_mean": int(round(cent_thr)),
        "zcr_mean": round(zcr_thr, 4),
        "kurtosis": 1.5,     # 不变（第四特征，非本次校准范围）
        "nsp": 0.6,           # 不变（P026 结论）
        "intro_nsp": 0.2,     # 不变
    }
    print(json.dumps(config, indent=4, ensure_ascii=False))
    print()

    # 清理
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return config


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m video_subber.calibrate_features <video_path> [model_size]")
        sys.exit(1)
    video = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "tiny"
    run(video, model)
