"""Dynamic ASS subtitle generation with user-configurable styles."""
from backend.core import subtitler as _subtitler
from backend.core.subtitler import _to_ass_time
from backend.core.display_timeline import assert_timeline_valid

# 字幕样式默认值（G12 第三批 B，用户 2026-09-16 按样品 ③ 确认）。
# 边距**只在这里定义**；字号默认值另有 config_manager 与前端两处，由测试守住一致。
DEFAULT_FONT_SIZE = 72
MARGIN_L, MARGIN_R, MARGIN_V = 96, 96, 46

# (字色, 阴影底色)。描边色不在这张表里：以前每项第 2 个值都是黑色，描边一律取到黑色，
# 设置里的“白色描边”“无描边”在成片里都不起作用（P478）。
ASS_COLORS = {
    "white":  ("&H00FFFFFF", "&H80000000"),
    "yellow": ("&H0000FFFF", "&H80000000"),
    "green":  ("&H0000FF00", "&H80000000"),
    "cyan":   ("&H00FFFF00", "&H80000000"),
    "red":    ("&H000000FF", "&H80000000"),
}


def build_ass_styled(entries: list, output_path: str, alignment: int = 2,
                     style: dict | None = None,
                     video_width: int = 1920, video_height: int = 1080) -> None:
    """把最终显示时间表序列化成带样式的内部 ASS。

    G5-1：不再跨模块借 subtitler 的私有收集函数自行重算条目。
    它和另外两个写出器读同一张 `display_timeline` 生成的表，只序列化、不改时间。

    style dict keys: font, size, color, outlineColor, outlineWidth, bgColor
    PlayRes 固定 1920x1080，字号跨分辨率一致。
    描边（P478）：white → 白色描边；none → 描边宽度 0（阴影照旧）；其余 → 黑色描边。
    """
    assert_timeline_valid(entries, srt=True, ass=True)
    s = style or {}
    # 所有烧录都走这个写出器（B2），默认字体沿用原写出器按系统区分的那个常量。
    font_name = s.get("font", _subtitler._DEFAULT_FONT)
    font_size = s.get("size", DEFAULT_FONT_SIZE)
    play_res_x, play_res_y = 1920, 1080
    color_name = s.get("color", "white")
    outline_name = s.get("outlineColor", "black")
    bg_name = s.get("bgColor", "transparent")

    colors = ASS_COLORS.get(color_name, ASS_COLORS["white"])
    primary_color = colors[0]

    outline_color = "&H00FFFFFF" if outline_name == "white" else "&H00000000"
    outline_w = 0 if outline_name == "none" else s.get("outlineWidth", 2)

    if bg_name == "transparent":
        back_color = "&H80000000"
    else:
        back_color = ASS_COLORS.get(bg_name, (None, "&H80000000"))[1]

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
            f"0,0,0,0,100,100,0,0,1,{outline_w},2,{alignment},"
            f"{MARGIN_L},{MARGIN_R},{MARGIN_V},1\n\n"
        )
        f.write("[Events]\n")
        f.write(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        for e in entries:
            if not e.text:
                continue
            ass_text = e.text.replace("\n", "\\N")
            f.write(
                f"Dialogue: 0,{_to_ass_time(e.start)},{_to_ass_time(e.end)},"
                f"Default,,0,0,0,,{ass_text}\n"
            )
