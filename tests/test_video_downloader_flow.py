from pathlib import Path

import pytest

from src.instagram_video_bot.services.instagram_client import InstagramAuthError
from src.instagram_video_bot.services.instagram_fast_extractor import (
    DownloadedMedia,
    FastExtractorDownloadResult,
    InstagramFastExtractorError,
)
from src.instagram_video_bot.services.video_downloader import (
    DownloadError,
    MediaItem,
    VideoDownloader,
    VideoInfo,
)


class _SuccessDownloadClient:
    username = "acc1"
    proxy = None

    def __init__(self, file_path: Path, media_info):
        self._file_path = file_path
        self._media_info = media_info

    def login(self):
        return True

    def download_video(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def download_media(self, _url: str, _output_dir: Path) -> Path:
        return self._file_path

    def get_media_info(self, _url: str):
        return self._media_info


class _AuthFailClient:
    username = "acc_fail"
    proxy = None

    def login(self):
        return True

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


class _LeaseManager:
    def __init__(self, accounts):
        self.accounts = list(accounts)
        self.released = []
        self.banned = []
        self.failures = []
        self.successes = []

    def get_available_accounts(self):
        return list(self.accounts)

    def acquire_account(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        for index, account in enumerate(self.accounts):
            if account.username not in excluded_usernames:
                return self.accounts.pop(index)
        return None

    def release_account(self, account):
        if account:
            self.released.append(account.username)

    def mark_account_banned(self, account):
        self.banned.append(account.username)

    def record_account_failure(self, account, reason):
        self.failures.append((account.username, reason))
        self.banned.append(account.username)

    def record_account_success(self, account):
        self.successes.append(account.username)


class _DuplicateLeaseManager(_LeaseManager):
    def __init__(self, accounts, lease_sequence):
        super().__init__(accounts)
        self.lease_sequence = list(lease_sequence)

    def acquire_account(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        for index, account in enumerate(self.lease_sequence):
            if account.username not in excluded_usernames:
                return self.lease_sequence.pop(index)
        return None


class _Account:
    def __init__(self, username: str):
        self.username = username
        self.password = "pw"
        self.proxy = None
        self.totp_secret = "totp"
        self.session_file = None


@pytest.mark.asyncio
async def test_fast_method_success_skips_legacy(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)

    expected_path = tmp_path / "fast.mp4"
    expected_path.write_bytes(b"video")

    downloader.fast_extractor = _FastExtractorSuccess(expected_path)
    monkeypatch.setattr(
        downloader,
        "_download_with_account_leases",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy should not run")),
    )
    monkeypatch.setattr(
        downloader,
        "_download_with_single_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy should not run")),
    )

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "fast-caption"
    assert info.primary_media_type == "video"
    assert len(info.media_items) == 1
    assert info.media_items[0].file_path == expected_path


@pytest.mark.asyncio
async def test_fast_failure_falls_back_to_single_account(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: None)

    expected_path = tmp_path / "legacy.mp4"
    expected_path.write_bytes(b"video")

    downloader.fast_extractor = _FastExtractorFailure()
    monkeypatch.setattr(
        downloader,
        "_build_single_account_client",
        lambda: _SuccessDownloadClient(expected_path, {"title": "legacy-title", "duration": 7}),
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
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: None)

    expected_path = tmp_path / "story.jpg"
    expected_path.write_bytes(b"photo")

    downloader.fast_extractor = _FastExtractorSuccess(expected_path)
    monkeypatch.setattr(
        downloader,
        "_build_single_account_client",
        lambda: _SuccessDownloadClient(expected_path, {"title": "story", "duration": 0}),
    )

    info = await downloader.download_video(
        "https://www.instagram.com/stories/user/1234567890123456789/",
        tmp_path,
    )

    assert info.file_path == expected_path
    assert info.primary_media_type == "photo"


@pytest.mark.asyncio
async def test_download_with_account_lease_retries_after_auth_failure(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "video_retry.mp4"
    expected_path.write_bytes(b"video")

    manager = _LeaseManager([_Account("acc_fail"), _Account("acc_ok")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    clients = {
        "acc_fail": _AuthFailClient(),
        "acc_ok": _SuccessDownloadClient(expected_path, {"title": "ok", "duration": 10}),
    }

    def _client_factory(**kwargs):
        return clients[kwargs["username"]]

    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.InstagramClient", _client_factory)

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "ok"
    assert manager.failures == [("acc_fail", "auth_challenge")]
    assert manager.successes == ["acc_ok"]


@pytest.mark.asyncio
async def test_download_retries_across_all_available_accounts(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "video_retry_all.mp4"
    expected_path.write_bytes(b"video")

    manager = _LeaseManager(
        [
            _Account("acc_fail_1"),
            _Account("acc_fail_2"),
            _Account("acc_fail_3"),
            _Account("acc_ok"),
        ]
    )
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    clients = {
        "acc_fail_1": _AuthFailClient(),
        "acc_fail_2": _AuthFailClient(),
        "acc_fail_3": _AuthFailClient(),
        "acc_ok": _SuccessDownloadClient(expected_path, {"title": "ok", "duration": 10}),
    }

    def _client_factory(**kwargs):
        return clients[kwargs["username"]]

    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.InstagramClient", _client_factory)

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "ok"
    assert manager.failures == [
        ("acc_fail_1", "auth_challenge"),
        ("acc_fail_2", "auth_challenge"),
        ("acc_fail_3", "auth_challenge"),
    ]
    assert manager.successes == ["acc_ok"]


@pytest.mark.asyncio
async def test_download_skips_duplicate_leased_accounts(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "video_retry_duplicate.mp4"
    expected_path.write_bytes(b"video")

    acc_fail = _Account("acc_fail")
    manager = _LeaseManager([acc_fail, acc_fail, _Account("acc_ok")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    clients = {
        "acc_fail": _AuthFailClient(),
        "acc_ok": _SuccessDownloadClient(expected_path, {"title": "ok", "duration": 10}),
    }

    def _client_factory(**kwargs):
        return clients[kwargs["username"]]

    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.InstagramClient", _client_factory)

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "ok"
    assert manager.failures == [("acc_fail", "auth_challenge")]
    assert manager.successes == ["acc_ok"]


@pytest.mark.asyncio
async def test_download_retry_budget_counts_unique_account_leases(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "video_retry_unique_budget.mp4"
    expected_path.write_bytes(b"video")

    acc_fail = _Account("acc_fail")
    acc_ok = _Account("acc_ok")
    manager = _DuplicateLeaseManager(
        accounts=[acc_fail, acc_ok],
        lease_sequence=[acc_fail, acc_fail, acc_fail, acc_ok],
    )
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    clients = {
        "acc_fail": _AuthFailClient(),
        "acc_ok": _SuccessDownloadClient(expected_path, {"title": "ok", "duration": 10}),
    }

    def _client_factory(**kwargs):
        return clients[kwargs["username"]]

    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.InstagramClient", _client_factory)

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert info.title == "ok"
    assert manager.failures == [("acc_fail", "auth_challenge")]
    assert manager.successes == ["acc_ok"]


@pytest.mark.asyncio
async def test_download_fails_after_retry_on_auth_failure(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    manager = _LeaseManager([_Account("acc_fail_1"), _Account("acc_fail_2")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    def _client_factory(**kwargs):
        return _AuthFailClient()

    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.InstagramClient", _client_factory)

    with pytest.raises(DownloadError, match="Authentication failed"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)


@pytest.mark.asyncio
async def test_twitter_status_url_skips_instagram_throttle(monkeypatch, tmp_path):
    downloader = VideoDownloader()

    expected_path = tmp_path / "tweet.mp4"
    expected_path.write_bytes(b"video")
    called = {"url": None}

    async def _throttle(_key: str):
        raise AssertionError("instagram throttle should not run for twitter URLs")

    class _TwitterDownloaderStub:
        async def download_media(self, url: str, _output_dir: Path) -> VideoInfo:
            called["url"] = url
            return VideoInfo(
                file_path=expected_path,
                title="tweet",
                media_items=[MediaItem(file_path=expected_path, media_type="video")],
                primary_media_type="video",
            )

    monkeypatch.setattr(downloader, "_apply_instagram_throttle", _throttle)
    downloader.twitter_downloader = _TwitterDownloaderStub()

    info = await downloader.download_video("https://x.com/someuser/status/1901234567890123456", tmp_path)

    assert called["url"] == "https://x.com/someuser/status/1901234567890123456"
    assert info.file_path == expected_path
    assert info.primary_media_type == "video"


@pytest.mark.asyncio
async def test_twitter_url_routes_to_twitter_downloader(tmp_path):
    downloader = VideoDownloader()

    expected_path = tmp_path / "tweet.mp4"
    expected_path.write_bytes(b"video")
    called = {"url": None}

    class _TwitterDownloaderStub:
        async def download_media(self, url: str, _output_dir: Path) -> VideoInfo:
            called["url"] = url
            return VideoInfo(
                file_path=expected_path,
                title="tweet",
                media_items=[MediaItem(file_path=expected_path, media_type="video")],
                primary_media_type="video",
            )

    downloader.twitter_downloader = _TwitterDownloaderStub()

    info = await downloader.download_video("https://x.com/someuser/status/1901234567890123456", tmp_path)

    assert called["url"] == "https://x.com/someuser/status/1901234567890123456"
    assert info.file_path == expected_path
    assert info.primary_media_type == "video"


@pytest.mark.parametrize(
    "url",
    [
        "https://m.twitter.com/someuser/status/1901234567890123456",
        "https://mobile.twitter.com/someuser/status/1901234567890123456",
    ],
)
def test_is_twitter_domain_url_recognizes_mobile_hosts(url):
    assert VideoDownloader._is_twitter_domain_url(url)


@pytest.mark.asyncio
async def test_non_status_twitter_url_fails_without_instagram_fallback(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    monkeypatch.setattr(
        downloader,
        "_download_instagram_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("instagram path should not run")
        ),
    )

    with pytest.raises(DownloadError, match="Unsupported Twitter/X URL"):
        await downloader.download_video("https://x.com/home", tmp_path)


@pytest.mark.asyncio
async def test_youtube_shorts_url_routes_to_youtube_downloader(tmp_path):
    downloader = VideoDownloader()
    expected_path = tmp_path / "short.mp4"
    expected_path.write_bytes(b"video")
    called = {"url": None}

    class _YouTubeDownloaderStub:
        async def download_media(self, url: str, _output_dir: Path) -> VideoInfo:
            called["url"] = url
            return VideoInfo(
                file_path=expected_path,
                title="short",
                media_items=[MediaItem(file_path=expected_path, media_type="video")],
                primary_media_type="video",
            )

    downloader.youtube_downloader = _YouTubeDownloaderStub()

    info = await downloader.download_video("https://www.youtube.com/shorts/abc123XYZ90", tmp_path)

    assert called["url"] == "https://www.youtube.com/shorts/abc123XYZ90"
    assert info.file_path == expected_path
    assert info.primary_media_type == "video"
