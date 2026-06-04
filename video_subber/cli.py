"""CLI entry point: video-subber — 一键视频中文字幕生成."""
import argparse, sys, shutil, json, os
from pathlib import Path
from .extractor import extract_audio
from .transcriber import transcribe, Segment
from .translator import translate_segments
from .subtitler import build_srt, build_ass, burn_subtitles
from .checkpoint import Checkpoint


def check_libass():
    import subprocess
    for ff in ["/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "ffmpeg"]:
        try:
            r = subprocess.run([ff, "-version"], capture_output=True, text=True, timeout=5)
            if "libass" in r.stdout:
                return True
        except Exception:
            continue
    return False


def main():
    p = argparse.ArgumentParser("video-subber", description="一键视频中文字幕生成")
    p.add_argument("input", help="输入视频文件")
    p.add_argument("--subtitles", required=True, choices=["yes", "no"],
                   help="原视频是否有字幕: yes/no")
    p.add_argument("-o", "--output", default=None, help="输出路径")
    p.add_argument("--lang", default="auto", help="源语言代码")
    p.add_argument("--model", default="medium",
                   choices=["tiny", "base", "small", "medium", "large-v3"])
    p.add_argument("--no-burn", action="store_true", help="仅 SRT，不烧录")
    p.add_argument("--backend", default="baidu",
                   choices=["google", "baidu"], help="翻译后端 (默认: baidu)")
    p.add_argument("--no-vad", action="store_true", help="跳过 VAD 过滤")
    p.add_argument("--keep-cache", action="store_true", help="保留缓存(断点+翻译结果)")
    p.add_argument("--force", action="store_true", help="强制重新开始，清除所有缓存")
    args = p.parse_args()

    # Pre-flight
    if not shutil.which("ffmpeg"):
        print("❌ 未检测到 ffmpeg。macOS: brew install ffmpeg-full")
        sys.exit(1)
    if not check_libass() and not args.no_burn:
        print("⚠️ 当前 ffmpeg 缺少 libass，将仅输出 SRT。brew install ffmpeg-full")
        args.no_burn = True

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"❌ 文件不存在: {args.input}")
        sys.exit(1)

    output = args.output or str(input_path.parent / f"{input_path.stem}_zh.mp4")
    work_dir = Path(output).parent
    cache = work_dir / f"{input_path.stem}.video-subber-cache"
    if args.force and cache.exists():
        shutil.rmtree(cache)
        print("🔄 已清除缓存，从头开始")
    cache.mkdir(exist_ok=True)

    TRANSCRIPT = cache / "transcript.json"
    TRANSLATED = cache / "translated.json"
    AUDIO = cache / "audio.wav"

    ck = Checkpoint(str(cache / "checkpoint.json"), str(input_path))

    try:
        # === Step 1: Extract Audio ===
        if not AUDIO.exists():
            print("[1/4] 提取音频…", flush=True)
            extract_audio(str(input_path), str(AUDIO))
            ck.save("extract_audio")
        else:
            print("[1/4] 音频已缓存，跳过", flush=True)

        # === Step 2: Transcribe ===
        if not TRANSCRIPT.exists():
            print(f"[2/4] Whisper 转录 (模型: {args.model})…", flush=True)
            segments = transcribe(str(AUDIO), args.model, args.lang, skip_vad=args.no_vad)
            with open(TRANSCRIPT, "w", encoding="utf-8") as f:
                json.dump([{"id": s.id, "start": s.start, "end": s.end, "text": s.text}
                           for s in segments], f, ensure_ascii=False, indent=2)
            print(f"       ✓ {len(segments)} 段", flush=True)
        else:
            print("[2/4] 转录已缓存，跳过", flush=True)
            raw = json.load(open(TRANSCRIPT, "r", encoding="utf-8"))
            segments = [Segment(s["id"], s["start"], s["end"], s["text"]) for s in raw]

        # === Step 3: Translate ===
        if not TRANSLATED.exists():
            print(f"[3/4] 翻译中…", flush=True)
            zh_list = translate_segments([(s.start, s.end, s.text) for s in segments],
                                          backend=args.backend)
            for s, zh in zip(segments, zh_list):
                s.zh = zh
            with open(TRANSLATED, "w", encoding="utf-8") as f:
                json.dump([{"id": s.id, "start": s.start, "end": s.end,
                            "text": s.text, "zh": s.zh} for s in segments],
                          f, ensure_ascii=False, indent=2)
            success = sum(1 for s in segments if s.zh.strip() and s.zh != "[翻译失败]")
            print(f"       ✓ {success}/{len(segments)} 成功", flush=True)
        else:
            print("[3/4] 翻译已缓存，跳过", flush=True)
            raw = json.load(open(TRANSLATED, "r", encoding="utf-8"))
            for s, r in zip(segments, raw):
                s.zh = r.get("zh", "")

        # === Step 4: SRT + Burn ===
        print("[4/4] 生成字幕…", flush=True)
        srt_out = work_dir / f"{input_path.stem}.srt"
        build_srt(segments, str(srt_out))
        print(f"       ✓ SRT: {srt_out}", flush=True)

        if not args.no_burn:
            ass_tmp = cache / "output.ass"
            # alignment: 8=top (video has hard subs), 2=bottom (no subs)
            al = 8 if args.subtitles == "yes" else 2
            build_ass(segments, str(ass_tmp), alignment=al)
            print("       烧录中…", flush=True)
            burn_subtitles(str(input_path), str(ass_tmp), output)
            print(f"       ✓ 视频: {output}", flush=True)

        ck.save("done")

        if not args.keep_cache:
            shutil.rmtree(cache, ignore_errors=True)
        print(f"\n✅ 完成！", flush=True)

    except Exception as e:
        print(f"\n❌ {e}", flush=True)
        print(f"   缓存保留在: {cache} (下次运行自动复用)", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
