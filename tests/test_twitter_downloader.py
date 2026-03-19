import subprocess

import pytest

from src.instagram_video_bot.services import twitter_downloader
from src.instagram_video_bot.services.twitter_downloader import (
    TwitterDownloadError,
    TwitterDownloader,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://twitter.com/user/status/1901234567890123456",
        "https://x.com/user/status/1901234567890123456",
        "https://www.x.com/user/status/1901234567890123456?s=20",
    ],
)
def test_twitter_downloader_supports_status_urls(url):
    assert TwitterDownloader.is_supported_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://twitter.com/user",
        "https://x.com/home",
        "https://www.instagram.com/reel/abc123/",
    ],
)
def test_twitter_downloader_rejects_non_status_urls(url):
    assert not TwitterDownloader.is_supported_url(url)


def test_collect_files_filters_non_media_sidecars(tmp_path):
    prefix = "twitter_1900000000000000000_1234567890"
    (tmp_path / f"{prefix}_01.mp4").write_bytes(b"video")
    (tmp_path / f"{prefix}_02.jpg").write_bytes(b"photo")
    (tmp_path / f"{prefix}_03.info.json").write_text("{}")
    (tmp_path / f"{prefix}_04.vtt").write_text("subtitle")

    files = TwitterDownloader._collect_files(tmp_path, prefix)

    assert [path.suffix for path in files] == [".mp4", ".jpg"]


def test_download_media_converts_download_timeout_to_twitter_error(tmp_path, monkeypatch):
    downloader = TwitterDownloader(timeout_seconds=5)
    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=5)

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _raise_timeout)

    with pytest.raises(TwitterDownloadError, match="timed out"):
        downloader._download_media_sync(
            "https://twitter.com/user/status/1901234567890123456",
            tmp_path,
        )
