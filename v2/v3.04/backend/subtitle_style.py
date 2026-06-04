"""Dynamic ASS subtitle generation with user-configurable styles."""
from backend.core.subtitler import _collect_entries, _annotate_speaker_changes, _to_ass_time

ASS_COLORS = {
    "white":  ("&H00FFFFFF", "&H00000000", "&H80000000"),
    "yellow": ("&H0000FFFF", "&H00000000", "&H80000000"),
    "green":  ("&H0000FF00", "&H00000000", "&H80000000"),
    "cyan":   ("&H00FFFF00", "&H00000000", "&H80000000"),
    "red":    ("&H000000FF", "&H00000000", "&H80000000"),
}


def build_ass_styled(segments: list, output_path: str, alignment: int = 2,
                     style: dict | None = None,
                     video_width: int = 1920, video_height: int = 1080) -> None:
    """Generate ASS with dynamic font/size/color/outline/bg.

    style dict keys:
      font, size, color, outlineColor, bgColor

    PlayRes fixed at 1920x1080 so font size stays consistent across resolutions.
    video_width/video_height used only for aspect ratio detection (portrait vs landscape).
    """
    s = style or {}
    font_name = s.get("font", "PingFang SC")
    font_size = s.get("size", 20)
    play_res_x, play_res_y = 1920, 1080
    color_name = s.get("color", "white")
    outline_name = s.get("outlineColor", "black")
    bg_name = s.get("bgColor", "transparent")

    colors = ASS_COLORS.get(color_name, ASS_COLORS["white"])
    primary_color = colors[0]

    if outline_name == "none":
        outline_color = "&H00000000"
    else:
        outline_color = ASS_COLORS.get(outline_name, (None, "&H00000000"))[1]
    outline_w = s.get("outlineWidth", 2)

    if bg_name == "transparent":
        back_color = "&H80000000"
    else:
        back_color = ASS_COLORS.get(bg_name, (None, None, "&H80000000"))[2]

    entries_sp = _collect_entries(segments)
    entries = _annotate_speaker_changes(entries_sp)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"[Script Info]\nScriptType: v4.00+\nPlayResX: {play_res_x}\nPlayResY: {play_res_y}\n\n")
        f.write("[V4+ Styles]\n")
        f.write(
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )
        f.write(
            f"Style: Default,{font_name},{font_size},{primary_color},"
            f"&H000000FF,{outline_color},{back_color},"
            f"0,0,0,0,100,100,0,0,1,{outline_w},2,{alignment},6,6,10,1\n\n"
        )
        f.write("[Events]\n")
        f.write(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        for start, end, text in entries:
            if not text:
                continue
            f.write(
                f"Dialogue: 0,{_to_ass_time(start)},{_to_ass_time(end)},"
                f"Default,,0,0,0,,{text}\n"
            )
