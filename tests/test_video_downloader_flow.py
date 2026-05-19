import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.instagram_client import InstagramAuthError
from src.instagram_video_bot.services.instagram_fast_extractor import (
    DownloadedMedia,
    FastExtractorDownloadResult,
    InstagramFastExtractorError,
)
from src.instagram_video_bot.services.media_metadata import MediaMetadata
from src.instagram_video_bot.services.provider_adapters import InstagramProviderAdapter
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


class _DownloadErrorClient:
    username = "acc_error"
    proxy = None

    def login(self):
        return True

    def download_video(self, _url: str, _output_dir: Path):
        raise DownloadError("legacy download failed")

    def download_media(self, _url: str, _output_dir: Path):
        raise DownloadError("legacy download failed")

    def get_media_info(self, _url: str):
        return None


class _ContentRestrictedClient:
    username = "acc_restricted"
    proxy = None
    last_failure_class = "content_restricted"
    last_failure_reason = "This content isn't available to everyone"

    def login(self):
        return True

    def download_video(self, _url: str, _output_dir: Path):
        return None

    def download_media(self, _url: str, _output_dir: Path):
        return None

    def get_media_info(self, _url: str):
        return None


class _AuthChallengeResultClient:
    username = "acc_challenge"
    proxy = None
    last_failure_class = "auth_challenge"
    last_failure_reason = "login required"

    def login(self):
        return True

    def download_video(self, _url: str, _output_dir: Path):
        return None

    def download_media(self, _url: str, _output_dir: Path):
        return None

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

    def get_leasable_account_count(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        return sum(1 for account in self.accounts if account.username not in excluded_usernames)

    def get_eligible_account_count(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        return sum(1 for account in self.accounts if account.username not in excluded_usernames)

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


class _DelayedLeaseManager(_LeaseManager):
    def __init__(self, accounts):
        super().__init__(accounts)
        self.calls = 0

    def acquire_account(self, excluded_usernames=None):
        self.calls += 1
        if self.calls == 1:
            return None
        return super().acquire_account(excluded_usernames=excluded_usernames)

    def get_leasable_account_count(self, excluded_usernames=None):
        return len(self.accounts)


class _TemporarilyLeasedManager(_LeaseManager):
    def __init__(self, accounts):
        super().__init__(accounts)
        self.leased_usernames = {account.username for account in accounts}

    def get_leasable_account_count(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        return sum(
            1
            for account in self.accounts
            if account.username not in self.leased_usernames
            and account.username not in excluded_usernames
        )

    def get_eligible_account_count(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        return sum(1 for account in self.accounts if account.username not in excluded_usernames)

    def acquire_account(self, excluded_usernames=None):
        excluded_usernames = excluded_usernames or set()
        for index, account in enumerate(self.accounts):
            if account.username not in self.leased_usernames and account.username not in excluded_usernames:
                return self.accounts.pop(index)
        return None

    def release_username(self, username):
        self.leased_usernames.discard(username)


class _HealthEventLeaseManager(_LeaseManager):
    def __init__(self, accounts, events):
        super().__init__(accounts)
        self.events = list(events)

    def record_account_failure(self, account, reason):
        super().record_account_failure(account, reason)
        return self.events.pop(0)


class _Account:
    def __init__(self, username: str):
        self.username = username
        self.password = "pw"
        self.proxy = None
        self.totp_secret = "totp"
        self.session_file = None


def test_build_media_item_treats_zero_duration_as_missing(monkeypatch, tmp_path):
    video_file = tmp_path / "legacy.mp4"
    video_file.write_bytes(b"video")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.provider_adapters.probe_video_metadata",
        lambda _path: MediaMetadata(duration=12.4, width=720, height=1280),
    )

    media_item = InstagramProviderAdapter._build_media_item(
        file_path=video_file,
        media_type="video",
        duration=0,
    )

    assert media_item.duration == 12.4
    assert media_item.width == 720
    assert media_item.height == 1280


@pytest.mark.asyncio
async def test_instagram_fallback_waits_for_leased_account(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS", 1.0)
    expected_path = tmp_path / "waited.mp4"
    expected_path.write_bytes(b"video")
    manager = _DelayedLeaseManager([_Account("acc_wait")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)
    monkeypatch.setattr(
        downloader,
        "_build_leased_client",
        lambda account: _SuccessDownloadClient(expected_path, {"title": "waited", "duration": 5}),
    )

    info = await downloader.download_video("https://www.instagram.com/reel/wait/", tmp_path)

    assert info.file_path == expected_path
    assert manager.calls >= 2


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
async def test_success_preserves_pending_account_pool_alert(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "video_retry_alert.mp4"
    expected_path.write_bytes(b"video")
    alert_event = SimpleNamespace(should_alert_owner=True, username="acc_fail")
    manager = _HealthEventLeaseManager([_Account("acc_fail"), _Account("acc_ok")], [alert_event])
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
    assert downloader.last_account_health_event is alert_event
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
async def test_download_preserves_first_alert_worthy_account_health_event(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    first_alert_event = SimpleNamespace(should_alert_owner=True, username="acc_fail_1")
    later_non_alert_event = SimpleNamespace(should_alert_owner=False, username="acc_fail_2")
    manager = _HealthEventLeaseManager(
        [_Account("acc_fail_1"), _Account("acc_fail_2")],
        [first_alert_event, later_non_alert_event],
    )
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    def _client_factory(**kwargs):
        return _AuthFailClient()

    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.InstagramClient", _client_factory)

    with pytest.raises(DownloadError, match="Authentication failed"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert manager.failures == [
        ("acc_fail_1", "auth_challenge"),
        ("acc_fail_2", "auth_challenge"),
    ]
    assert downloader.last_account_health_event is first_alert_event


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


@pytest.mark.asyncio
async def test_instagram_fast_success_records_provider_metrics(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)
    expected_path = tmp_path / "fast-metrics.mp4"
    expected_path.write_bytes(b"video")
    downloader.fast_extractor = _FastExtractorSuccess(expected_path)

    await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.provider == "instagram"
    assert metrics.instagram_fast_status == "succeeded"
    assert metrics.instagram_success_path == "fast"
    assert metrics.instagram_fallback_attempted is False
    assert metrics.instagram_fast_duration_ms >= 0


@pytest.mark.asyncio
async def test_instagram_fast_failure_records_fallback_metrics(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: None)
    expected_path = tmp_path / "legacy-metrics.mp4"
    expected_path.write_bytes(b"video")
    downloader.fast_extractor = _FastExtractorFailure()
    monkeypatch.setattr(
        downloader,
        "_build_single_account_client",
        lambda: _SuccessDownloadClient(expected_path, {"title": "legacy", "duration": 7}),
    )

    await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_fast_status == "failed"
    assert metrics.instagram_fallback_attempted is True
    assert metrics.instagram_success_path == "fallback"
    assert metrics.instagram_account_attempts == 1


@pytest.mark.asyncio
async def test_single_account_terminal_auth_failure_counts_actual_retries(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: None)
    monkeypatch.setattr(downloader, "_build_single_account_client", lambda: _AuthFailClient())

    with pytest.raises(DownloadError, match="Authentication failed"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_account_attempts == 2
    assert metrics.instagram_auth_failures == 2
    assert metrics.instagram_account_retries == 1
    assert metrics.retry_count == 1
    assert metrics.failure_class == "auth_challenge"


@pytest.mark.asyncio
async def test_single_account_download_error_sets_failure_class(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: None)
    monkeypatch.setattr(downloader, "_build_single_account_client", lambda: _DownloadErrorClient())

    with pytest.raises(DownloadError, match="legacy download failed"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_account_attempts == 1
    assert metrics.instagram_auth_failures == 0
    assert metrics.instagram_account_retries == 0
    assert metrics.retry_count == 0
    assert metrics.failure_class == "download_failed"


@pytest.mark.asyncio
async def test_leased_content_restriction_is_not_recorded_as_account_failure(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    manager = _LeaseManager([_Account("acc_restricted")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.InstagramClient",
        lambda **_kwargs: _ContentRestrictedClient(),
    )

    with pytest.raises(DownloadError, match="content_restricted"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_account_attempts == 1
    assert metrics.instagram_auth_failures == 0
    assert metrics.failure_class == "content_restricted"
    assert manager.failures == []


@pytest.mark.asyncio
async def test_leased_auth_challenge_failure_class_rotates_account(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "after-auth-challenge.mp4"
    expected_path.write_bytes(b"video")

    manager = _LeaseManager([_Account("acc_challenge"), _Account("acc_ok")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.get_account_manager", lambda: manager)

    clients = {
        "acc_challenge": _AuthChallengeResultClient(),
        "acc_ok": _SuccessDownloadClient(expected_path, {"title": "ok", "duration": 10}),
    }
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.InstagramClient",
        lambda **kwargs: clients[kwargs["username"]],
    )

    info = await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    assert info.file_path == expected_path
    assert downloader.last_provider_metrics.instagram_auth_failures == 1
    assert downloader.last_provider_metrics.failure_class == "auth_challenge"
    assert manager.failures == [("acc_challenge", "auth_challenge")]
    assert manager.successes == ["acc_ok"]


@pytest.mark.asyncio
async def test_leased_fallback_success_records_retry_metrics(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)

    expected_path = tmp_path / "leased-retry-metrics.mp4"
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

    await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_account_attempts == 2
    assert metrics.instagram_auth_failures == 1
    assert metrics.instagram_account_retries == 1
    assert metrics.retry_count == 1
    assert metrics.instagram_success_path == "fallback"


@pytest.mark.asyncio
async def test_empty_leased_account_pool_sets_failure_class(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.get_account_manager",
        lambda: _LeaseManager([]),
    )

    with pytest.raises(DownloadError, match="Download failed"):
        await asyncio.wait_for(
            downloader.download_video("https://www.instagram.com/reel/a/", tmp_path),
            timeout=0.5,
        )

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_account_attempts == 0
    assert metrics.failure_class == "no_instagram_accounts"


@pytest.mark.asyncio
async def test_empty_leased_account_pool_overrides_fast_failure_class(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED", True)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS", 0.0)
    downloader.fast_extractor = _FastExtractorFailure()
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.get_account_manager",
        lambda: _LeaseManager([]),
    )

    with pytest.raises(DownloadError, match="fast_path_error=fast-failed"):
        await downloader.download_video("https://www.instagram.com/reel/a/", tmp_path)

    metrics = downloader.last_provider_metrics
    assert metrics.instagram_account_attempts == 0
    assert metrics.failure_class == "no_instagram_accounts"


@pytest.mark.asyncio
async def test_acquire_account_with_wait_fails_promptly_when_all_accounts_excluded(monkeypatch):
    downloader = VideoDownloader()
    manager = _LeaseManager([_Account("acc_used")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS", 1.0)

    account = await asyncio.wait_for(
        downloader._acquire_account_with_wait(manager, {"acc_used"}),
        timeout=0.5,
    )

    assert account is None


@pytest.mark.asyncio
async def test_acquire_account_with_wait_waits_for_temporarily_leased_account(monkeypatch):
    downloader = VideoDownloader()
    manager = _TemporarilyLeasedManager([_Account("acc_wait")])
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS", 1.0)

    acquire_task = asyncio.create_task(downloader._acquire_account_with_wait(manager, set()))
    await asyncio.sleep(0.05)
    manager.release_username("acc_wait")

    account = await asyncio.wait_for(acquire_task, timeout=0.5)

    assert account is not None
    assert account.username == "acc_wait"


class _FlakyTwitterAdapter:
    def __init__(self, result):
        self.calls = 0
        self.result = result

    async def download(self, url, output_dir):
        self.calls += 1
        if self.calls == 1:
            raise DownloadError("timed out while downloading")
        return self.result


class _AlwaysFailingAdapter:
    def __init__(self, message):
        self.calls = 0
        self.message = message

    async def download(self, url, output_dir):
        self.calls += 1
        raise DownloadError(self.message)


@pytest.mark.asyncio
async def test_transient_provider_error_retries_once(monkeypatch, tmp_path):
    media_file = tmp_path / "twitter.mp4"
    media_file.write_bytes(b"video")
    result = VideoInfo(
        file_path=media_file,
        title="twitter",
        media_items=[MediaItem(file_path=media_file, media_type="video")],
        primary_media_type="video",
    )
    downloader = VideoDownloader()
    adapter = _FlakyTwitterAdapter(result)
    downloader.twitter_adapter = adapter
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_TRANSIENT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_RETRY_BACKOFF_SECONDS", 0)

    info = await downloader.download_video("https://x.com/a/status/123", tmp_path)

    assert info.file_path == media_file
    assert adapter.calls == 2
    assert downloader.last_provider_metrics.retry_count == 1


@pytest.mark.asyncio
async def test_transient_provider_error_exhaustion_records_failure_metrics(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    adapter = _AlwaysFailingAdapter("temporarily unavailable")
    downloader.twitter_adapter = adapter
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_TRANSIENT_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_RETRY_BACKOFF_SECONDS", 0)

    with pytest.raises(DownloadError, match="temporarily unavailable"):
        await downloader.download_video("https://x.com/a/status/123", tmp_path)

    assert adapter.calls == 3
    assert downloader.last_provider_metrics.retry_count == 2
    assert downloader.last_provider_metrics.failure_class == "transient_network"


@pytest.mark.asyncio
async def test_non_transient_provider_error_does_not_retry_and_records_unknown(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    adapter = _AlwaysFailingAdapter("bad response")
    downloader.twitter_adapter = adapter
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_TRANSIENT_RETRY_ATTEMPTS", 3)

    with pytest.raises(DownloadError, match="bad response"):
        await downloader.download_video("https://x.com/a/status/123", tmp_path)

    assert adapter.calls == 1
    assert downloader.last_provider_metrics.retry_count == 0
    assert downloader.last_provider_metrics.failure_class == "unknown"


@pytest.mark.asyncio
async def test_youtube_transient_provider_error_uses_retry_wrapper(monkeypatch, tmp_path):
    media_file = tmp_path / "short.mp4"
    media_file.write_bytes(b"video")
    result = VideoInfo(
        file_path=media_file,
        title="short",
        media_items=[MediaItem(file_path=media_file, media_type="video")],
        primary_media_type="video",
    )
    downloader = VideoDownloader()
    adapter = _FlakyTwitterAdapter(result)
    downloader.youtube_adapter = adapter
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_TRANSIENT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr("src.instagram_video_bot.services.video_downloader.settings.PROVIDER_RETRY_BACKOFF_SECONDS", 0)

    info = await downloader.download_video("https://www.youtube.com/shorts/abc123XYZ90", tmp_path)

    assert info.file_path == media_file
    assert adapter.calls == 2
    assert downloader.last_provider_metrics.retry_count == 1


@pytest.mark.asyncio
async def test_unsupported_provider_error_does_not_retry(tmp_path):
    downloader = VideoDownloader()

    with pytest.raises(DownloadError):
        await downloader.download_video("https://x.com/not-a-status", tmp_path)

    assert downloader.last_provider_metrics.retry_count == 0
    assert downloader.last_provider_metrics.failure_class == "unsupported_url"
