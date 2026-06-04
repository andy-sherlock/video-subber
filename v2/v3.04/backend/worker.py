"""Subprocess worker — runs V1.3 pipeline in isolation.

Protocol: prints JSON lines to stdout for progress events.
  {"type":"progress","stage":"extract","percent":5,"message":"..."}
  {"type":"progress","stage":"transcribe","percent":25,"message":"...","detail":"87/287"}
  {"type":"done","result_video":"...","result_srt":"...","failed_segments":[...]}
  {"type":"error","message":"...","failed_segments":[...]}
"""
import sys, os, json, time, builtins, logging, shutil, re as _re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 模块级保存子进程的真实 stdout。main() 内会把 sys.stdout 重定向到 sys.stderr 以保护
# JSON IPC 协议；模块级函数（如 stderr_progress）写入 _real_stdout 直达父进程的 PIPE。
# V2.07 修复：之前 _real_stdout 仅在 main() 局部赋值，导致 stderr_progress 引用时
# NameError（"name '_real_stdout' is not defined"）阻塞所有流水线。
_real_stdout = sys.stdout

# ── 0) Setup logging BEFORE anything else ──
LOG_DIR = Path.home() / ".video-subber" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 根 Logger：开启 DEBUG，由下游 Handler 各自筛选级别
root_log = logging.getLogger()
root_log.setLevel(logging.DEBUG)

# 显式降低第三方库的日志噪音（numba bytecode dump, httpcore 连接池等）
for _noisy in ("numba", "httpcore", "hpack", "h2", "httpx", "urllib3", "faster_whisper"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# 全局句柄持有，防止重复添加
_file_handler_user = None
_file_handler_debug = None

def _setup_file_log(job_id: str):
    """双 Handler 分层分流日志系统。

    - user.log：仅记录 INFO+（步骤进度、核心决策、警告、错误），面向用户和客服。
    - debug.log：全量 DEBUG+，带 50MB × 3 滚动限额保护磁盘，面向开发者深度排查。
    """
    global _file_handler_user, _file_handler_debug

    # 清理旧任务的句柄（若存在）
    if _file_handler_user:
        root_log.removeHandler(_file_handler_user)
    if _file_handler_debug:
        root_log.removeHandler(_file_handler_debug)

    log_dir = LOG_DIR / job_id
    log_dir.mkdir(parents=True, exist_ok=True)

    class _ScrubFormatter(logging.Formatter):
        def format(self, record):
            return scrub_logs(super().format(record))

    # ────── 用户日志：只收 INFO+ ──────
    user_path = log_dir / "user.log"
    _file_handler_user = logging.FileHandler(str(user_path), encoding="utf-8")
    _file_handler_user.setLevel(logging.INFO)
    _file_handler_user.setFormatter(_ScrubFormatter(
        "%(asctime)s %(message)s", datefmt="%H:%M:%S"
    ))

    # ────── 开发者日志：全量 DEBUG+，带滚动保护 ──────
    debug_path = log_dir / "debug.log"
    _file_handler_debug = RotatingFileHandler(
        str(debug_path),
        maxBytes=50 * 1024 * 1024,  # 50MB 单文件上限
        backupCount=3,              # 最多保留 3 个历史文件
        encoding="utf-8",
    )
    _file_handler_debug.setLevel(logging.DEBUG)
    _file_handler_debug.setFormatter(_ScrubFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root_log.addHandler(_file_handler_user)
    root_log.addHandler(_file_handler_debug)

    # 架构留痕：stdout 被 JSON lines IPC 协议占用（见下方 sys.stdout = sys.stderr）。
    # V2.07 已把 transcriber_v2 / worker 内的关键进度 print() 替换为 _log.info，
    # 因此走 logging handler 进 user.log。残留的 print() 仅为协议性/启动错误输出。

    return str(user_path), str(debug_path)

# ── 1) Monkey-patch input() BEFORE any V1.3 import ──
_original_input = builtins.input


def _auto_yes_input(prompt: str = "") -> str:
    logging.getLogger("worker").info("suppressed input(): %s", prompt.strip())
    return "y"


builtins.input = _auto_yes_input

# ── 2) Path setup ──
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.extractor import extract_audio
from backend.core.subtitler import build_srt, build_ass, burn_subtitles, mux_subtitles
from backend.core.checkpoint import Checkpoint, find_versioned_cache, cleanup_old_versions
from backend.core.sentence_splitter import word_align_segments, llm_split_segments
from backend.transcriber_v2 import transcribe as transcribe_v2, Segment
from backend.key_manager import scrub_logs


def emit(event_type: str, **kwargs):
    msg = {"type": event_type, **kwargs}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()




def main():
    if len(sys.argv) < 2:
        print("Usage: python -m backend.worker <config.json>", file=sys.stderr)
        sys.exit(1)

    _t_start = time.time()

    config_path = Path(sys.argv[1])
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    job_dir = config_path.parent
    job_id = job_dir.name
    user_log_path, debug_log_path = _setup_file_log(job_id)
    logging.getLogger("worker").info("Worker started — 用户日志: %s  调试日志: %s",
                                     user_log_path, debug_log_path)
    # ── 版本号写入日志 ──
    try:
        import tomllib
        _ppt = Path(__file__).parent.parent / "pyproject.toml"
        _ver = tomllib.loads(_ppt.read_text()).get("project", {}).get("version", "dev")
    except Exception:
        _ver = "dev"
    logging.getLogger("worker").info("版本: %s", _ver)
    video_path = config["video_path"]
    # ── outputMode: "srt" | "burn" | "mux" (backward compat: derive from burn flag) ──
    output_mode = config.get("outputMode")
    if output_mode is None:
        burn_old = config.get("burn", True)
        output_mode = "burn" if burn_old else "srt"
    burn_flag = (output_mode == "burn")
    mux_flag = (output_mode == "mux")
    subtitles = config.get("subtitles", "no")
    model = config.get("model", "medium")
    translation_mode = config.get("translationMode", "speed")
    translation_priority = config.get("translationPriority", ["google", "bing", "youdao"])
    api_keys = config.get("apiKeys", [])
    context_aware = config.get("contextAware", False)
    style = config.get("style", None)
    speaker_detect = config.get("speakerDetect", False)  # 实验性功能，默认关闭
    transcription_tier = config.get("transcriptionTier", "standard")  # fast/standard/precise

    # ── v2.05: Config schema 校验，防止静默降级 ──
    def _validate_config(cfg: dict):
        if "burn" in cfg and not isinstance(cfg.get("burn"), (bool, type(None))):
            raise ValueError(f"config.burn 应为 bool，实际为 {type(cfg['burn']).__name__}")
        if "model" in cfg and cfg["model"] not in ("tiny", "base", "small", "medium", "large-v3", None):
            raise ValueError(f"config.model 无效: {cfg['model']}")
        if "translationMode" in cfg and cfg["translationMode"] not in ("speed", "quality", None):
            raise ValueError(f"config.translationMode 无效: {cfg['translationMode']}")
        if "subtitles" in cfg and cfg["subtitles"] not in ("yes", "no", None):
            raise ValueError(f"config.subtitles 无效: {cfg['subtitles']}")
        if "speakerDetect" in cfg and not isinstance(cfg.get("speakerDetect"), (bool, type(None))):
            raise ValueError(f"config.speakerDetect 应为 bool，实际为 {type(cfg['speakerDetect']).__name__}")
        if "transcriptionTier" in cfg and cfg["transcriptionTier"] not in ("fast", "standard", "precise", None):
            raise ValueError(f"config.transcriptionTier 无效: {cfg['transcriptionTier']}")
        if "outputMode" in cfg and cfg["outputMode"] not in ("srt", "burn", "mux", None):
            raise ValueError(f"config.outputMode 无效: {cfg['outputMode']}")
    _validate_config(config)

    # Redirect V1.3 print() to stderr so stdout stays clean JSON.
    # _real_stdout 已在模块顶部保存（V2.07 修复）。
    sys.stdout = sys.stderr

    # Cache paths (versioned per SHA256)
    cache_base = job_dir / "cache"
    cache_base.mkdir(exist_ok=True)
    cache, _ = find_versioned_cache(cache_base, video_path)
    cleanup_old_versions(cache_base, keep=5)

    # Derive output filename from original video name (e.g. "1.ts" → "1")
    _video_filename = config.get("video_filename", "output")
    _video_stem = Path(_video_filename).stem
    _video_suffix = Path(_video_filename).suffix

    audio_path = str(cache / "audio.wav")
    transcript_path = cache / "transcript.json"
    translated_path = cache / "translated.json"
    srt_path = str(job_dir / f"{_video_stem}.srt")
    ass_path = str(cache / f"{_video_stem}.ass")
    video_out = str(job_dir / f"{_video_stem}_zh.mp4")
    video_out_mux = str(job_dir / f"{_video_stem}_zh_sub{_video_suffix}")
    ck_path = str(cache / "checkpoint.json")

    # Which setting affects which pipeline stage (earliest impact)
    SETTING_STAGE = {
        "subtitles": "extract_audio",
        "model": "transcribe",
        "transcriptionTier": "transcribe",
        "speakerDetect": "transcribe",
        "translationMode": "translate",
        "translationPriority": "translate",
        "apiKeys": "translate",
        "contextAware": "translate",
        "style": "subtitle",
        "burn": "burn",
        "outputMode": "burn",
    }
    STAGE_ORDER = ["extract_audio", "transcribe", "translate", "subtitle", "burn"]
    # subtitle produces both .srt (job_dir, user-facing) and .ass (cache, used by
    # burn). Both must be cleared when style changes, otherwise burn reuses the
    # old .ass. burn produces the final video. (P051)
    STAGE_CACHE: dict[str, list[str]] = {
        "extract_audio": [audio_path],
        "transcribe": [str(transcript_path)],
        "translate": [str(translated_path)],
        "subtitle": [srt_path, ass_path],
        "burn": [video_out, video_out_mux],
    }

    ck = Checkpoint(ck_path, video_path)

    try:
        stage = ck.load()
        old_config = {}
        try:
            ck_data = json.loads(Path(ck_path).read_text(encoding="utf-8"))
            old_config = ck_data.get("meta", {})
        except Exception:
            pass

        # Compare configs: if critical settings changed, invalidate affected caches
        if old_config:
            earliest_affected = None
            for key, affected_stage in SETTING_STAGE.items():
                old_val = old_config.get(key)
                new_val = config.get(key)
                if old_val != new_val and new_val is not None:
                    old_idx = STAGE_ORDER.index(affected_stage) if affected_stage in STAGE_ORDER else 99
                    cur_idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1
                    if old_idx <= cur_idx:
                        if earliest_affected is None or STAGE_ORDER.index(affected_stage) < STAGE_ORDER.index(earliest_affected):
                            earliest_affected = affected_stage
            if earliest_affected:
                # Delete all cache files from the earliest affected stage onward
                aff_idx = STAGE_ORDER.index(earliest_affected)
                for si in range(aff_idx, len(STAGE_ORDER)):
                    s = STAGE_ORDER[si]
                    for p_str in STAGE_CACHE.get(s, []):
                        p = Path(p_str)
                        if p.exists():
                            p.unlink()
                            logging.getLogger("worker").info("设置已变更 (%s 受影响)，清除缓存: %s", earliest_affected, p.name)
                stage = earliest_affected
                ck.save(stage, meta=config)

        stderr_progress("start", 0, "正在准备…")

        # ── Step 1: Extract Audio (0-5%) ──
        video_width, video_height = 1920, 1080
        from backend.core.extractor import get_video_resolution
        try:
            video_width, video_height = get_video_resolution(video_path)
        except Exception:
            pass
        if stage in ("start", "extract_audio") and not Path(audio_path).exists():
            stderr_progress("extract", 2, "正在分析视频…")
            extract_audio(video_path, audio_path)
            ck.save("extract_audio", meta=config)
        stderr_progress("extract", 5, "视频分析完成")

        # ── Step 2: Transcribe (5-35%) ──
        segments = None
        if stage in ("start", "extract_audio", "transcribe") and not transcript_path.exists():
            _MODEL_TIME = {"tiny": "1-3 分钟", "base": "2-5 分钟", "small": "5-15 分钟", "medium": "10-25 分钟", "large-v3": "20-40 分钟"}
            time_est = _MODEL_TIME.get(model, "10-30 分钟")
            stderr_progress("transcribe", 10, f"正在识别语音…（{model} 约需 {time_est}）", f"模型: {model}")
            def _trans_progress(n):
                if n == -1:
                    stderr_progress("transcribe", 10, "正在启动语音识别…", f"模型: {model}")
                    return
                pct = min(34, 11 + n // 10)
                stderr_progress("transcribe", pct, f"正在识别语音… 已识别 {n} 段", f"模型: {model}")
            segments = transcribe_v2(
                audio_path, model_size=model, source_lang="auto",
                skip_vad=True, vad_threshold=0.0,
                filter_keywords=True, speaker_detect=speaker_detect,
                transcription_tier=transcription_tier,
                progress_callback=_trans_progress,
            )
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump([{
                    "id": s.id, "start": s.start, "end": s.end,
                    "text": s.text, "speaker_change": s.speaker_change,
                    "uncertain": s.uncertain,
                    "sensevoice_tag": getattr(s, "sensevoice_tag", None),
                    "qwen_text": getattr(s, "qwen_text", None),
                    "words": getattr(s, "words", []),
                } for s in segments], f, ensure_ascii=False, indent=2)
            ck.save("transcribe", meta=config)
        else:
            stderr_progress("transcribe", 10, "已有识别记录，跳过")
            raw = json.loads(transcript_path.read_text(encoding="utf-8"))
            segments = [Segment(r["id"], r["start"], r["end"], r["text"]) for r in raw]
            for s, r in zip(segments, raw):
                s.speaker_change = r.get("speaker_change", False)
                s.uncertain = r.get("uncertain", False)
                s.sensevoice_tag = r.get("sensevoice_tag", None)
                s.qwen_text = r.get("qwen_text", None)
                s.words = r.get("words", [])

        total = len(segments)
        stderr_progress("transcribe", 35, f"语音识别完成: 共 {total} 句")

        # ── Step 3: Translate (35-80%) ──
        failed = []
        sub_splits = {}
        if stage in ("start", "extract_audio", "transcribe", "translate") and not translated_path.exists():
            texts = [s.text.strip() for s in segments]
            try:
                from backend.translation.orchestrator import translate_with_orchestrator

                def prog(pct: int, msg: str):
                    stderr_progress("translate", 40 + int(pct * 0.45), msg)

                zh_list, failed_indices, token_usage, sub_splits = translate_with_orchestrator(
                    texts=texts, mode=translation_mode, priority=translation_priority,
                    api_keys=api_keys, context_aware=context_aware,
                    progress_callback=prog,
                )
                if token_usage:
                    msg = json.dumps({"type": "token_usage", "usage": token_usage}, ensure_ascii=False)
                    _real_stdout.write(msg + "\n")
                    _real_stdout.flush()
            except ImportError:
                # orchestrator not available — abort with a clear error
                raise RuntimeError("翻译模块未就绪，请检查安装。")

            for s, zh in zip(segments, zh_list):
                s.zh = zh
            failed = [(s.id, s.text, s.zh) for s in segments if s.zh == "[翻译失败]"]

            # ── LLM 失败降级 ──
            _has_llm_keys = bool(api_keys)
            _all_failed = len(failed) == total and total > 0
            if _has_llm_keys and _all_failed:
                stderr_progress("translate", 40, "LLM 翻译全部失败，等待用户选择…")
                msg = json.dumps({
                    "type": "llm_failed",
                    "message": "LLM 翻译全部失败，是否改用免费引擎（Google/Bing/有道）继续？",
                    "total": total,
                }, ensure_ascii=False)
                _real_stdout.write(msg + "\n")
                _real_stdout.flush()

                decision_file = job_dir / "llm_fallback.json"
                _deadline = time.time() + 120
                _decision = "use_free"  # 超时自动降级，不阻塞流程
                _auto_fallback = True
                while time.time() < _deadline:
                    if decision_file.exists():
                        try:
                            _dec = json.loads(decision_file.read_text(encoding="utf-8"))
                            _decision = _dec.get("decision", "use_free")
                            _auto_fallback = False
                            break
                        except Exception:
                            pass
                    time.sleep(1)

                if _decision == "use_free":
                    if _auto_fallback:
                        stderr_progress("translate", 40, "超时无回应，已自动切换免费引擎继续翻译…")
                        _note = json.dumps({
                            "type": "notice",
                            "message": "LLM 翻译未能完成，已自动切换免费引擎（Google/Bing/有道）。",
                        }, ensure_ascii=False)
                        _real_stdout.write(_note + "\n")
                        _real_stdout.flush()
                    else:
                        stderr_progress("translate", 40, "已切换为免费引擎，正在重新翻译…")
                    zh_list, failed_indices, token_usage, _ = translate_with_orchestrator(
                        texts=texts, mode=translation_mode, priority=translation_priority,
                        api_keys=[], context_aware=False,
                        progress_callback=prog,
                    )
                    sub_splits = {}  # free engines don't do pre-splitting
                    for s, zh in zip(segments, zh_list):
                        s.zh = zh
                    failed = [(s.id, s.text, s.zh) for s in segments if s.zh == "[翻译失败]"]
                decision_file.unlink(missing_ok=True)

            with open(translated_path, "w", encoding="utf-8") as f:
                json.dump([{
                    "id": s.id, "start": s.start, "end": s.end,
                    "text": s.text, "zh": s.zh,
                } for s in segments], f, ensure_ascii=False, indent=2)
            ck.save("translate", meta=config)

            # ── Phase 1C: reflection review (quality mode) ──
            if api_keys and failed and translation_mode == "quality":
                _reflection_mode = config.get("reflection", False)
                if _reflection_mode:
                    try:
                        from backend.translation.reflection import (
                            build_reflection_prompt, parse_reflection_result, apply_reflection
                        )
                        from backend.translation.orchestrator import _build_llm_backends
                        _r_backends = _build_llm_backends(api_keys)
                        if _r_backends:
                            _r_llm = _r_backends[0]
                            _r_prompt = build_reflection_prompt(texts, zh_list, limit=30)
                            _r_result = _r_llm.translate_raw(_r_prompt, max_tokens=4096)
                            _r_raw = _r_result.translated[0] if _r_result.translated else ""
                            if _r_raw:
                                _r_corrections = parse_reflection_result(_r_raw)
                                zh_list = apply_reflection(zh_list, _r_corrections)
                                for s, zh in zip(segments, zh_list):
                                    s.zh = zh
                                failed = [(s.id, s.text, s.zh) for s in segments if s.zh == "[翻译失败]"]
                                stderr_progress("translate", 78,
                                    f"反思修正完成: {len(_r_corrections)} 条优化")
                                # Re-save corrected translation
                                with open(translated_path, "w", encoding="utf-8") as f:
                                    json.dump([{
                                        "id": s.id, "start": s.start, "end": s.end,
                                        "text": s.text, "zh": s.zh,
                                    } for s in segments], f, ensure_ascii=False, indent=2)
                    except Exception:
                        _log.warning("反思优化失败", exc_info=True)
        else:
            stderr_progress("translate", 40, "已有翻译记录，跳过")
            raw = json.loads(translated_path.read_text(encoding="utf-8"))
            for s, r in zip(segments, raw):
                s.zh = r.get("zh", "")
            failed = [(s.id, s.text, s.zh) for s in segments if s.zh == "[翻译失败]"]

        ok_count = sum(1 for s in segments if s.zh.strip() and s.zh != "[翻译失败]")
        stderr_progress("translate", 80, f"翻译完成: {ok_count}/{total} 句成功, {len(failed)} 句失败")

        # Quality gate: if >50% failed, the cache is corrupt — delete it and abort
        if total > 0 and ok_count / total < 0.5:
            Path(translated_path).unlink(missing_ok=True)
            raise RuntimeError(
                f"翻译质量过低：仅 {ok_count}/{total} 条成功（{int(ok_count/total*100)}%）。"
                "请检查翻译引擎配置后重试。"
            )

        # ── Step 4: Subtitle (80-90%) ──
        # Phase 1D: uncertain flags from hallucination filter are preserved
        # for [?] marking and question_mark_count statistics.
        subtitle_segments = word_align_segments(segments)

        # T4: LLM sentence splitting for long Chinese subtitles
        llm_backend = None
        if api_keys:
            try:
                from backend.translation.orchestrator import _build_llm_backends
                llm_list = _build_llm_backends(api_keys)
                if llm_list:
                    llm_backend = llm_list[0]
            except Exception:
                pass
        subtitle_segments = llm_split_segments(subtitle_segments, llm_backend=llm_backend, pre_splits=sub_splits)

        stderr_progress("subtitle", 85, "正在生成字幕文件…")
        build_srt(subtitle_segments, srt_path)

        if burn_flag:
            alignment = 8 if subtitles == "yes" else 2

            if style:
                try:
                    from backend.subtitle_style import build_ass_styled
                    stderr_progress("subtitle", 88, "正在排版字幕样式…")
                    build_ass_styled(subtitle_segments, ass_path, alignment=alignment, style=style,
                                     video_width=video_width, video_height=video_height)
                except ImportError:
                    build_ass(subtitle_segments, ass_path, alignment=alignment,
                              video_width=video_width, video_height=video_height)
            else:
                stderr_progress("subtitle", 88, "正在排版字幕样式…")
                build_ass(subtitle_segments, ass_path, alignment=alignment,
                          video_width=video_width, video_height=video_height)

            # ── Step 5: Burn (90-100%) ──
            stderr_progress("burn", 92, "正在合成最终视频…")
            burn_subtitles(video_path, ass_path, video_out)
            stderr_progress("burn", 98, "合成完成")

        if mux_flag:
            # ── Step 5: Mux (90-100%) ──
            stderr_progress("burn", 92, "正在封装字幕到视频…")
            mux_subtitles(video_path, srt_path, video_out_mux)
            stderr_progress("burn", 98, "封装完成")

        ck.save("done", meta=config)

        # 保留 input 视频，允许用户切换设置后通过 force=true 重跑。
        # 视频仅在用户主动取消任务（DELETE /api/jobs/{id}）时清理。

        # Copy output files to user's save path
        # If source_dir is known (local_path mode / EXE), save next to the original video
        _source_dir = config.get("source_dir")
        if _source_dir and Path(_source_dir).is_dir():
            save_path = Path(_source_dir)
        else:
            save_path = Path(config.get("savePath", "~/Downloads/Video-Subber")).expanduser()
        save_path.mkdir(parents=True, exist_ok=True)
        _ctx_suffix = "上下文" if context_aware else "逐段"
        if burn_flag and Path(video_out).exists():
            _dest_v = save_path / Path(video_out).name
            if _dest_v.exists():
                _archived = save_path / f"{_video_stem}_{_ctx_suffix}_zh.mp4"
                _dest_v.rename(_archived)
            shutil.copy2(video_out, _dest_v)
        if mux_flag and Path(video_out_mux).exists():
            _dest_v = save_path / Path(video_out_mux).name
            if _dest_v.exists():
                _archived = save_path / f"{_video_stem}_{_ctx_suffix}_zh_sub{_video_suffix}"
                _dest_v.rename(_archived)
            shutil.copy2(video_out_mux, _dest_v)
        if Path(srt_path).exists() and not burn_flag:
            _dest_s = save_path / Path(srt_path).name
            if _dest_s.exists():
                _archived = save_path / f"{_video_stem}_{_ctx_suffix}.srt"
                _dest_s.rename(_archived)
            shutil.copy2(srt_path, _dest_s)

        sys.stdout = _real_stdout
        _t_end = time.time()
        _dur_str = f"合计={_t_end - _t_start:.0f}s"
        logging.getLogger("worker").info("步骤耗时: %s", _dur_str)
        emit("progress", stage="burn", percent=100, message="完成", detail="")
        qm_count = sum(1 for s in segments if getattr(s, "uncertain", False))
        emit("done",
             result_video=video_out if burn_flag else (video_out_mux if mux_flag else None),
             result_srt=srt_path,
             failed_segments=[{"id": fid, "text": ftxt, "reason": freason}
                              for fid, ftxt, freason in failed] if failed else [],
             segment_count=total,
             success_count=ok_count,
             question_mark_count=qm_count)

    except Exception as e:
        sys.stdout = _real_stdout
        _dur_str = f"合计={time.time() - _t_start:.0f}s (异常退出)"
        logging.getLogger("worker").info("步骤耗时: %s", _dur_str)
        emit("error", message=_friendly_error(str(e)), failed_segments=[])


def _friendly_error(raw: str) -> str:
    """把技术错误翻译成用户能理解并行动的提示。"""
    if "未检测到任何语音内容" in raw:
        return "视频中没有检测到语音。可能原因：视频为纯音乐、静音、或音频格式损坏。请确认视频有声音后重试。"
    if "No such file" in raw or "FileNotFound" in raw:
        return "缺少必要的模型文件。请检查网络连接，模型将在下次启动时自动下载。"
    if "Memory" in raw or "memory" in raw:
        return "内存不足，无法处理该视频。请尝试使用更小的模型（如 tiny 或 base）。"
    if "ReadTimeout" in raw or "Timeout" in raw or "timeout" in raw.lower():
        return "连接翻译服务器超时。请检查网络，或切换到其他翻译引擎后重试。"
    if "429" in raw or "Too Many Requests" in raw:
        return "翻译请求过于频繁，已被限流。请稍等片刻后重试，或切换翻译引擎。"
    if "Authentication" in raw or "Invalid API Key" in raw or "401" in raw or "403" in raw:
        return "API Key 验证失败。请检查设置中的 Key 是否正确、是否已过期。"
    return f"处理过程中断。错误详情：{raw[:80]}"


def stderr_progress(stage: str, percent: int, message: str, detail: str = ""):
    """直接写入 _real_stdout，不切换全局 sys.stdout（避免多线程竞态）。"""
    msg = json.dumps({
        "type": "progress", "stage": stage, "percent": percent,
        "message": message, "detail": detail,
    }, ensure_ascii=False)
    _real_stdout.write(msg + "\n")
    _real_stdout.flush()


if __name__ == "__main__":
    main()
