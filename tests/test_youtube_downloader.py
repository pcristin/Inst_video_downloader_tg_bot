import subprocess

import pytest

from src.instagram_video_bot.services import youtube_downloader
from src.instagram_video_bot.services.youtube_downloader import (
    YouTubeDownloadError,
    YouTubeShortsDownloader,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/shorts/abc123XYZ90",
        "https://m.youtube.com/shorts/abc123XYZ90?feature=share",
    ],
)
def test_youtube_downloader_supports_shorts_urls(url):
    assert YouTubeShortsDownloader.is_supported_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123XYZ90",
        "https://youtu.be/abc123XYZ90",
        "https://x.com/user/status/1901234567890123456",
    ],
)
def test_youtube_downloader_rejects_non_shorts_urls(url):
    assert not YouTubeShortsDownloader.is_supported_url(url)


def test_youtube_download_converts_timeout_to_error(tmp_path, monkeypatch):
    downloader = YouTubeShortsDownloader(timeout_seconds=5)
    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=5)

    monkeypatch.setattr(youtube_downloader.subprocess, "run", _raise_timeout)

    with pytest.raises(YouTubeDownloadError, match="timed out"):
        downloader._download_media_sync(
            "https://www.youtube.com/shorts/abc123XYZ90",
            tmp_path,
        )
