"""CLI entry point: video-subber — 一键视频中文字幕生成."""
import argparse, sys, shutil, json, os, logging
from datetime import datetime
from pathlib import Path
from .extractor import extract_audio
from .transcriber import transcribe, Segment
from .translator import translate_segments
from .subtitler import build_srt, build_ass, burn_subtitles
from .checkpoint import Checkpoint, get_file_hash, find_versioned_cache, cleanup_old_versions


class _TeeOutput:
    """Write to both terminal and a log file simultaneously.

    Every print() call across ALL modules (transcriber, translator, etc.)
    automatically lands in the log file without needing per-module changes.
    """

    def __init__(self, log_path: str):
        self.terminal = sys.stdout
        self.log = open(log_path, "w", encoding="utf-8")

    def write(self, msg: str) -> None:
        self.terminal.write(msg)
        self.log.write(msg)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()

    def close(self) -> None:
        sys.stdout = self.terminal
        self.log.close()


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
    p.add_argument("--vad-threshold", type=float, default=0.0,
                   help="faster-whisper 内置 VAD 阈值 (默认 0=关闭; 0.4 启用)")
    p.add_argument("--no-speech-threshold", type=float, default=0.6,
                   help="Whisper no_speech_prob 阈值 (默认 0.6, 越高越宽松)")
    p.add_argument("--no-filter-keywords", action="store_true",
                   help="关闭幻觉关键词黑名单 (音乐/♪/掌声 等)")
    p.add_argument("--no-speaker-detect", action="store_true",
                   help="关闭说话人变化检测 (默认开启)")
    p.add_argument("--speaker-threshold", type=float, default=0.75,
                   help="说话人变化检测阈值 (默认 0.75, 越低越容易判定换人)")
    p.add_argument("--keep-cache", action="store_true", help="保留缓存(断点+翻译结果)")
    p.add_argument("--max-cache-versions", type=int, default=5,
                   help="同一视频名最多保留几个版本的缓存 (默认 5)")
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
    cache_base = work_dir / f"{input_path.stem}.video-subber-cache"

    if args.force and cache_base.exists():
        shutil.rmtree(cache_base)
        print("🔄 已清除所有缓存，从头开始", flush=True)
    cache_base.mkdir(exist_ok=True)

    # ── 版本化缓存：按文件 SHA256 查找或创建子目录 ──
    cache, is_new = find_versioned_cache(cache_base, str(input_path))

    if is_new:
        h = get_file_hash(str(input_path))[:12]
        print(f"   📁 新文件 — 创建缓存: {h}", flush=True)
        max_v = getattr(args, 'max_cache_versions', 5)
        cleanup_old_versions(cache_base, keep=max_v)
    else:
        h = get_file_hash(str(input_path))[:12]
        print(f"   📁 复用缓存: {h}", flush=True)

    TRANSCRIPT = cache / "transcript.json"
    TRANSLATED = cache / "translated.json"
    AUDIO = cache / "audio.wav"

    ck = Checkpoint(str(cache / "checkpoint.json"), str(input_path))

    # ── 日志系统：logs/ 目录下两条日志 ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = work_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    user_log_path = log_dir / f"{input_path.stem}_{ts}_user.log"
    debug_log_path = log_dir / f"{input_path.stem}_{ts}_debug.log"

    # 用户日志：终端所见即所得
    tee = _TeeOutput(str(user_log_path))
    sys.stdout = tee

    # 开发者日志：只记录我们自己的代码级细节，第三方库保持安静
    logging.basicConfig(
        level=logging.WARNING,  # 根 logger 只收 WARNING+，屏蔽 numba/httpcore 等第三方噪音
        format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(str(debug_log_path), encoding="utf-8")],
    )
    for _mod in ("cli", "transcriber", "translator", "subtitler", "extractor", "checkpoint"):
        logging.getLogger(_mod).setLevel(logging.DEBUG)
    _log = logging.getLogger("cli")
    _log.info("===== video-subber debug log =====")
    _log.info("视频: %s", input_path)
    _log.info("模型: %s | 语言: %s | 字幕: %s | VAD: %s",
              args.model, args.lang, args.subtitles,
              "关" if args.vad_threshold == 0 else f"阈值{args.vad_threshold}")

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
            segments = transcribe(
                str(AUDIO), args.model, args.lang,
                skip_vad=args.no_vad,
                vad_threshold=args.vad_threshold,
                no_speech_threshold=args.no_speech_threshold,
                filter_keywords=not args.no_filter_keywords,
                speaker_detect=not args.no_speaker_detect,
                speaker_threshold=args.speaker_threshold,
            )
            with open(TRANSCRIPT, "w", encoding="utf-8") as f:
                json.dump([{"id": s.id, "start": s.start, "end": s.end,
                            "text": s.text, "speaker_change": s.speaker_change,
                            "uncertain": s.uncertain}
                           for s in segments], f, ensure_ascii=False, indent=2)
            print(f"       ✓ {len(segments)} 段", flush=True)
        else:
            print("[2/4] 转录已缓存，跳过", flush=True)
            raw = json.load(open(TRANSCRIPT, "r", encoding="utf-8"))
            segments = [Segment(s["id"], s["start"], s["end"], s["text"]) for s in raw]
            for s, r in zip(segments, raw):
                s.speaker_change = r.get("speaker_change", False)
                s.uncertain = r.get("uncertain", False)

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
            # 只清除当前版本的子目录，不影响其他同名视频的缓存
            shutil.rmtree(cache, ignore_errors=True)
            # 如果缓存目录已空，一并移除
            try:
                cache_base.rmdir()
            except OSError:
                pass
        print(f"\n✅ 完成！", flush=True)
        print(f"   📄 用户日志: {user_log_path}", flush=True)
        print(f"   🔧 调试日志: {debug_log_path}", flush=True)

    except Exception as e:
        print(f"\n❌ {e}", flush=True)
        print(f"   缓存保留在: {cache}", flush=True)
        print(f"   📄 用户日志: {user_log_path}", flush=True)
        print(f"   🔧 调试日志: {debug_log_path}", flush=True)
        sys.exit(1)

    finally:
        tee.close()


if __name__ == "__main__":
    main()
