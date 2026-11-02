"""P367：缓存保留精确失败标记，字幕成品显示可读原文。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.subtitler import build_ass, build_srt
from backend.core.display_timeline import build_display_timeline


class Segment:
    def __init__(self, text, zh):
        self.start = 1.0
        self.end = 2.0
        self.text = text
        self.zh = zh
        self.words = []
        self.speaker_change = False
        self.uncertain = False


# G5-1：写出器降级为纯序列化，只接收 display_timeline 生成的条目。
# 排版与时间修复不再发生在写出器内部，因此测试也从「喂 Segment」
# 改成「先生成时间表、再序列化」——断言的行为（失败译文显示可读原文、
# 不泄露缓存哨兵、成功译文原样渲染）一个字都没变。
def _render_srt(segments, path):
    build_srt(build_display_timeline(segments), str(path))


def _render_ass(segments, path):
    build_ass(build_display_timeline(segments), str(path))


def test_failed_translation_renders_original_in_srt_without_changing_cache_marker(tmp_path):
    segment = Segment("You", "[翻译失败]")
    output = tmp_path / "failed.srt"

    _render_srt([segment], output)

    rendered = output.read_text(encoding="utf-8")
    assert "【未翻译】You" in rendered
    assert "[翻译失败]" not in rendered
    assert segment.zh == "[翻译失败]"


def test_failed_translation_renders_single_line_original_in_ass(tmp_path):
    output = tmp_path / "failed.ass"

    _render_ass([Segment("What", "[翻译失败]")], output)

    rendered = output.read_text(encoding="utf-8")
    assert "【未翻译】What" in rendered
    assert "【未翻译】\\NWhat" not in rendered


def test_successful_translation_rendering_is_unchanged(tmp_path):
    output = tmp_path / "ok.srt"

    _render_srt([Segment("You", "你")], output)

    rendered = output.read_text(encoding="utf-8")
    assert "\n你\n" in rendered
    assert "【未翻译】" not in rendered
