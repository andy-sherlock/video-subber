"""P441: lock the verified YouTube downloader stack and enable Node."""

import sys
import tomllib
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_project_pins_verified_youtube_downloader_stack():
    """The formal APP install must reproduce the versions proven by the real test."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(project["project"]["dependencies"])

    assert "yt-dlp==2026.8.19" in dependencies
    assert "yt-dlp-ejs==0.8.0" in dependencies


def test_video_and_audio_downloads_explicitly_enable_node(monkeypatch, tmp_path):
    """Both yt-dlp calls must use Node instead of relying on yt-dlp's default runtime."""
    youtube_dl = MagicMock()
    downloader = MagicMock()
    youtube_dl.return_value.__enter__.return_value = downloader
    downloader.extract_info.return_value = {"title": "test", "ext": "mp4"}
    downloader.prepare_filename.return_value = str(tmp_path / "stream.mp4")

    fake_yt_dlp = types.ModuleType("yt_dlp")
    fake_yt_dlp.YoutubeDL = youtube_dl
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_yt_dlp)

    from backend.core import video_downloader

    monkeypatch.setattr(video_downloader, "_detect_system_proxy", lambda: None)
    result = video_downloader.download_video(
        "https://www.youtube.com/watch?v=SkQzl3kO0cA",
        str(tmp_path),
    )

    assert result is not None
    assert len(youtube_dl.call_args_list) == 2
    for call in youtube_dl.call_args_list:
        assert call.args[0]["js_runtimes"] == {"node": {}}
