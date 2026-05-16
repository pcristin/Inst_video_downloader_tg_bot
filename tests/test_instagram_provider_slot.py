import asyncio
from pathlib import Path

import pytest

from src.instagram_video_bot.services.instagram_fast_extractor import (
    DownloadedMedia,
    FastExtractorDownloadResult,
)
from src.instagram_video_bot.services.video_downloader import VideoDownloader


class _FastExtractorSuccess:
    def __init__(self, path: Path):
        self._path = path

    def extract_and_download(self, _url: str, _output_dir: Path) -> FastExtractorDownloadResult:
        return FastExtractorDownloadResult(
            shortcode="abc",
            caption="fast-caption",
            media_items=[DownloadedMedia(file_path=self._path, media_type="video")],
        )


class _SuccessDownloadClient:
    username = "acc_wait"
    proxy = None

    def __init__(self, file_path: Path):
        self._file_path = file_path

    def login(self):
        return True

    def download_video(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def download_media(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def get_media_info(self, _url: str):
        return {"title": "story", "duration": 5}


class _Account:
    username = "acc_wait"
    password = "pw"
    proxy = None
    totp_secret = "totp"
    session_file = None


class _TemporarilyLeasedManager:
    def __init__(self, account):
        self.account = account
        self.leased = True

    def get_available_accounts(self):
        return [self.account]

    def get_eligible_account_count(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        return 0 if self.account.username in excluded_usernames else 1

    def acquire_account(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        if self.leased or self.account.username in excluded_usernames:
            return None
        return self.account

    def release_account(self, _account):
        return None

    def release_username(self, username):
        if username == self.account.username:
            self.leased = False

    def record_account_success(self, _account):
        return None


@pytest.mark.asyncio
async def test_instagram_account_wait_does_not_consume_provider_slot(monkeypatch, tmp_path):
    story_downloader = VideoDownloader()
    fast_downloader = VideoDownloader()
    for downloader in (story_downloader, fast_downloader):
        downloader.min_delay_between_downloads = 0
        downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_MAX_CONCURRENT_JOBS", 1)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS", 1.0)

    account = _Account()
    manager = _TemporarilyLeasedManager(account)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    story_path = tmp_path / "story.mp4"
    fast_path = tmp_path / "fast.mp4"
    story_path.write_bytes(b"story")
    fast_path.write_bytes(b"fast")
    fast_downloader.fast_extractor = _FastExtractorSuccess(fast_path)
    monkeypatch.setattr(story_downloader, "_build_leased_client", lambda _account: _SuccessDownloadClient(story_path))

    story_task = asyncio.create_task(
        story_downloader.download_video(
            "https://www.instagram.com/stories/user/1234567890123456789/",
            tmp_path,
        )
    )
    await asyncio.sleep(0.05)

    fast_info = await asyncio.wait_for(
        fast_downloader.download_video("https://www.instagram.com/reel/fast/", tmp_path),
        timeout=0.5,
    )
    manager.release_username(account.username)
    story_info = await asyncio.wait_for(story_task, timeout=1)

    assert fast_info.file_path == fast_path
    assert story_info.file_path == story_path
