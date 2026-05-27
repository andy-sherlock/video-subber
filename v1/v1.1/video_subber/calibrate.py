"""Run all 5 Whisper models on a short calibration clip, collect no_speech_prob & avg_logprob distributions, recommend per-model thresholds."""
import json, sys, numpy as np
from faster_whisper import WhisperModel

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
SPEECH_STARTS_AT = 5.0  # user-confirmed: dialogue starts ~5s


def calibrate(audio_path):
    results = {}
    for model_name in MODELS:
        print(f"\n{'='*50}")
        print(f"  {model_name}...")
        print(f"{'='*50}", flush=True)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            audio_path, language="ru",
            condition_on_previous_text=False, vad_filter=False,
        )
        all_segs = []
        for seg in segments:
            all_segs.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip()[:60],
                "no_speech_prob": round(seg.no_speech_prob, 4),
                "avg_logprob": round(seg.avg_logprob, 4),
            })
        results[model_name] = all_segs
        print(f"  → {len(all_segs)} 段, 语言={info.language} p={info.language_probability:.2f}", flush=True)
        del model

    with open("calibration_raw.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return results


def analyze(results):
    print("\n" + "=" * 70)
    print("  校准结果")
    print("=" * 70)
    recommendations = {}
    for model_name, segs in results.items():
        speech_segs = [s for s in segs if s["start"] >= SPEECH_STARTS_AT]
        pre_speech = [s for s in segs if s["start"] < SPEECH_STARTS_AT]
        if not speech_segs:
            continue
        nsp = [s["no_speech_prob"] for s in speech_segs]
        logp = [s["avg_logprob"] for s in speech_segs]
        recommended_nsp = round(min(max(nsp) + 0.05, 0.95), 2)
        recommended_logp = round(min(logp) - 0.1, 2)
        recommendations[model_name] = {"no_speech_prob": recommended_nsp, "avg_logprob": recommended_logp}
        print(f"\n  {model_name}: {len(segs)} 段 (人声{len(speech_segs)} 片头{len(pre_speech)})")
        print(f"    no_speech_prob: median={np.median(nsp):.3f} P95={np.percentile(nsp,95):.3f} max={max(nsp):.3f}")
        print(f"    avg_logprob:    median={np.median(logp):.3f} P5={np.percentile(logp,5):.3f} min={min(logp):.3f}")
        if pre_speech:
            pnsp = [s["no_speech_prob"] for s in pre_speech]
            print(f"    片头nsp: median={np.median(pnsp):.3f} max={max(pnsp):.3f}")
        print(f"    → no_speech_prob 阈值: {recommended_nsp}  avg_logprob 阈值: {recommended_logp}")

    print("\n" + "=" * 70)
    print("  FILTER_THRESHOLDS = {")
    for m, t in recommendations.items():
        print(f'      "{m}": {{"no_speech_prob": {t["no_speech_prob"]}, "avg_logprob": {t["avg_logprob"]}}},')
    print("  }")
    return recommendations


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "/tmp/calib_first3m.wav"
    r = calibrate(audio)
    analyze(r)
