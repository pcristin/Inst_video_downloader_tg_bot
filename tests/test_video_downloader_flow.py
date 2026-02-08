from pathlib import Path

import pytest

from src.instagram_video_bot.services.instagram_client import InstagramAuthError
from src.instagram_video_bot.services.video_downloader import DownloadError, VideoDownloader


class _SuccessDownloadClient:
    username = "acc1"
    proxy = None

    def __init__(self, file_path: Path, media_info):
        self._file_path = file_path
        self._media_info = media_info

    def download_video(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def get_media_info(self, _url: str):
        return self._media_info


class _AuthFailClient:
    username = "acc_fail"
    proxy = None

    def download_video(self, _url: str, _output_dir: Path):
        raise InstagramAuthError("challenge_required")

    def get_media_info(self, _url: str):
        return None


@pytest.mark.asyncio
async def test_download_succeeds_even_when_metadata_unavailable():
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    expected_path = Path("/tmp/video.mp4")
    client = _SuccessDownloadClient(expected_path, None)
    downloader._get_client = lambda: client

    info = await downloader.download_video("https://www.instagram.com/reel/a/", Path("/tmp"))

    assert info.file_path == expected_path
    assert info.title == ""
    assert info.duration == 0


@pytest.mark.asyncio
async def test_download_rotates_and_retries_once_after_auth_failure():
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    expected_path = Path("/tmp/video_retry.mp4")
    clients = [
        _AuthFailClient(),
        _SuccessDownloadClient(expected_path, {"title": "ok", "duration": 10}),
    ]
    attempts = {"count": 0}
    handled = {"called": 0}

    def _get_client():
        idx = min(attempts["count"], len(clients) - 1)
        attempts["count"] += 1
        return clients[idx]

    async def _handle_auth():
        handled["called"] += 1

    downloader._get_client = _get_client
    downloader._handle_auth_error = _handle_auth

    info = await downloader.download_video("https://www.instagram.com/reel/a/", Path("/tmp"))

    assert info.file_path == expected_path
    assert info.title == "ok"
    assert handled["called"] == 1


@pytest.mark.asyncio
async def test_download_fails_after_retry_on_auth_failure():
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    downloader._get_client = lambda: _AuthFailClient()

    async def _handle_auth():
        return None

    downloader._handle_auth_error = _handle_auth

    with pytest.raises(DownloadError, match="Authentication failed after account rotation retry"):
        await downloader.download_video("https://www.instagram.com/reel/a/", Path("/tmp"))
