from pathlib import Path

import pytest

from src.instagram_video_bot.services.instagram_client import InstagramAuthError
from src.instagram_video_bot.services.instagram_fast_extractor import (
    DownloadedMedia,
    FastExtractorDownloadResult,
    InstagramFastExtractorError,
)
from src.instagram_video_bot.services.video_downloader import DownloadError, VideoDownloader


class _SuccessDownloadClient:
    username = "acc1"
    proxy = None

    def __init__(self, file_path: Path, media_info):
        self._file_path = file_path
        self._media_info = media_info

    def download_video(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def download_media(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def get_media_info(self, _url: str):
        return self._media_info


class _AuthFailClient:
    username = "acc_fail"
    proxy = None

    def download_video(self, _url: str, _output_dir: Path):
        raise InstagramAuthError("challenge_required")

    def download_media(self, _url: str, _output_dir: Path):
        raise InstagramAuthError("challenge_required")

    def get_media_info(self, _url: str):
        return None


class _FastExtractorSuccess:
    def __init__(self, path: Path, media_type: str = "video"):
        self._path = path
        self._media_type = media_type

    def extract_and_download(self, _url: str, _output_dir: Path) -> FastExtractorDownloadResult:
        return FastExtractorDownloadResult(
            shortcode="abc",
            caption="fast-caption",
            media_items=[DownloadedMedia(file_path=self._path, media_type=self._media_type)],
        )


class _FastExtractorFailure:
    def extract_and_download(self, _url: str, _output_dir: Path) -> FastExtractorDownloadResult:
        raise InstagramFastExtractorError("fast-failed")


@pytest.mark.asyncio
async def test_fast_method_success_skips_legacy(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)

    expected_path = tmp_path / "fast.mp4"
    expected_path.write_bytes(b"video")

    downloader.fast_extractor = _FastExtractorSuccess(expected_path)
    downloader._get_client = lambda: (_ for _ in ()).throw(AssertionError("legacy should not run"))

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "fast-caption"
    assert info.primary_media_type == "video"
    assert len(info.media_items) == 1
    assert info.media_items[0].file_path == expected_path


@pytest.mark.asyncio
async def test_fast_failure_falls_back_to_legacy(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)

    expected_path = tmp_path / "legacy.mp4"
    expected_path.write_bytes(b"video")

    downloader.fast_extractor = _FastExtractorFailure()
    downloader._get_client = lambda: _SuccessDownloadClient(
        expected_path,
        {"title": "legacy-title", "duration": 7},
    )

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "legacy-title"
    assert info.primary_media_type == "video"
    assert len(info.media_items) == 1


@pytest.mark.asyncio
async def test_story_url_routes_directly_to_legacy(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)

    expected_path = tmp_path / "story.jpg"
    expected_path.write_bytes(b"photo")

    downloader.fast_extractor = _FastExtractorSuccess(expected_path)
    downloader._get_client = lambda: _SuccessDownloadClient(
        expected_path,
        {"title": "story", "duration": 0},
    )

    info = await downloader.download_video(
        "https://www.instagram.com/stories/user/1234567890123456789/",
        tmp_path,
    )

    assert info.file_path == expected_path
    assert info.primary_media_type == "photo"


@pytest.mark.asyncio
async def test_download_rotates_and_retries_once_after_auth_failure(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "video_retry.mp4"
    expected_path.write_bytes(b"video")

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

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "ok"
    assert handled["called"] == 1


@pytest.mark.asyncio
async def test_download_fails_after_retry_on_auth_failure(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    downloader._get_client = lambda: _AuthFailClient()
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    async def _handle_auth():
        return None

    downloader._handle_auth_error = _handle_auth

    with pytest.raises(DownloadError, match="Authentication failed after account rotation retry"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)
