"""Instagram video downloading service using instagrapi."""
import asyncio
import logging
import random
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .download_models import (
    AuthenticationError,
    DownloadError,
    MediaItem,
    VideoInfo,
    VideoDownloadError,
)
from .instagram_client import InstagramAuthError, InstagramClient
from .instagram_fast_extractor import InstagramFastExtractor
from .provider_adapters import (
    InstagramProviderAdapter,
    TwitterProviderAdapter,
    YouTubeShortsProviderAdapter,
)
from .twitter_downloader import TwitterDownloader
from .youtube_downloader import YouTubeShortsDownloader
from ..config.settings import settings
from ..utils.account_manager import get_account_manager

logger = logging.getLogger(__name__)


class VideoDownloader:
    """Service for downloading Instagram videos using instagrapi."""

    _throttle_lock = threading.Lock()
    _last_instagram_download_by_key: dict[str, float] = {}

    def __init__(self):
        """Initialize the video downloader."""
        self.min_delay_between_downloads = 10
        self.random_delay_range = (1.0, 3.0)
        fast_extractor = InstagramFastExtractor(
            timeout_connect=settings.IG_FAST_TIMEOUT_CONNECT,
            timeout_read=settings.IG_FAST_TIMEOUT_READ,
        )
        self.instagram_adapter = InstagramProviderAdapter(fast_extractor)
        self.twitter_adapter = TwitterProviderAdapter(
            TwitterDownloader(proxy=settings.get_single_proxy())
        )
        self.youtube_adapter = YouTubeShortsProviderAdapter(
            YouTubeShortsDownloader()
        )
        self.last_account_health_event = None

    @property
    def fast_extractor(self):
        """Compatibility accessor for tests and callers."""
        return self.instagram_adapter.fast_extractor

    @fast_extractor.setter
    def fast_extractor(self, value):
        self.instagram_adapter.fast_extractor = value

    @property
    def twitter_downloader(self):
        """Compatibility accessor for tests and callers."""
        return self.twitter_adapter.downloader

    @twitter_downloader.setter
    def twitter_downloader(self, value):
        self.twitter_adapter.downloader = value

    @property
    def youtube_downloader(self):
        """Compatibility accessor for tests and callers."""
        return self.youtube_adapter.downloader

    @youtube_downloader.setter
    def youtube_downloader(self, value):
        self.youtube_adapter.downloader = value

    @staticmethod
    def _redact_proxy(proxy: str) -> str:
        """Return proxy value with credentials removed for logging."""
        if "@" not in proxy:
            return proxy
        if "://" in proxy:
            scheme, remainder = proxy.split("://", 1)
            if "@" in remainder:
                _, host_part = remainder.split("@", 1)
                return f"{scheme}://***@{host_part}"
        _, host_part = proxy.split("@", 1)
        return f"***@{host_part}"

    def _build_single_account_client(self):
        """Compatibility wrapper for single-account client creation."""
        client = InstagramClient(
            username=settings.IG_USERNAME,
            password=settings.IG_PASSWORD,
            totp_secret=settings.TOTP_SECRET,
        )
        if not client.login():
            raise AuthenticationError("Failed to login to Instagram")
        return client

    def _build_leased_client(self, account):
        """Compatibility wrapper for leased-account client creation."""
        client = InstagramClient(
            username=account.username,
            password=account.password,
            session_file=account.session_file,
            proxy=account.proxy,
            totp_secret=account.totp_secret,
        )
        if not client.login():
            raise AuthenticationError("Failed to login to Instagram")
        return client

    async def download_video(self, url: str, output_dir: Path) -> VideoInfo:
        """Download media from supported providers."""
        if self._is_twitter_url(url):
            return await self._download_twitter_media(url, output_dir)
        if self._is_twitter_domain_url(url):
            raise DownloadError("Unsupported Twitter/X URL")
        if self._is_youtube_shorts_url(url):
            return await self._download_youtube_media(url, output_dir)
        if self._is_youtube_domain_url(url):
            raise DownloadError("Unsupported YouTube URL")

        return await self._download_instagram_media(url, output_dir)

    async def _download_instagram_media(self, url: str, output_dir: Path) -> VideoInfo:
        """Download Instagram media with fast-path and leased fallback."""
        lease_key = "instagram-fast"
        manager = get_account_manager()

        fast_error: Optional[Exception] = None
        is_story_url = self.instagram_adapter.is_story_url(url)
        if settings.IG_FAST_METHOD_ENABLED and not is_story_url:
            try:
                await self._apply_instagram_throttle(lease_key)
                fast_result = self.instagram_adapter.download_with_fast_method(url, output_dir)
                return fast_result
            except Exception as error:
                fast_error = error
                logger.warning(
                    "Fast extractor failed, falling back to legacy method",
                    extra={
                        "failure_class": "fast_path_failed",
                        "error": str(error),
                    },
                )

        if manager:
            return await self._download_with_account_leases(url, output_dir, fast_error)
        return await self._download_with_single_account(url, output_dir, fast_error)

    async def _download_with_account_leases(
        self, url: str, output_dir: Path, fast_error: Optional[Exception]
    ) -> VideoInfo:
        """Download using a leased Instagram account for this job."""
        manager = get_account_manager()
        if not manager:
            raise DownloadError("No Instagram accounts available")

        last_error: Optional[Exception] = None
        tried_accounts: set[str] = set()
        max_attempts = max(1, len(manager.get_available_accounts()))
        for attempt in range(max_attempts):
            account = manager.acquire_account(excluded_usernames=tried_accounts)
            if not account:
                break
            tried_accounts.add(account.username)
            try:
                await self._apply_instagram_throttle(account.username)
                client = self._build_leased_client(account)
                result = self.instagram_adapter.download_with_instagram_client(
                    client=client,
                    url=url,
                    output_dir=output_dir,
                    redact_proxy=self._redact_proxy,
                )
                manager.record_account_success(account)
                if not getattr(self.last_account_health_event, "should_alert_owner", False):
                    self.last_account_health_event = None
                return result
            except (InstagramAuthError, AuthenticationError) as auth_error:
                last_error = auth_error
                logger.warning(
                    "Authentication-like failure during download",
                    extra={
                        "failure_class": "auth_challenge",
                        "attempt": attempt + 1,
                        "username": account.username,
                        "error": str(auth_error),
                    },
                )
                event = manager.record_account_failure(account, "auth_challenge")
                if getattr(event, "should_alert_owner", False) or self.last_account_health_event is None:
                    self.last_account_health_event = event
            except Exception as error:
                if isinstance(error, DownloadError):
                    raise
                last_error = error
                raise DownloadError(f"Download failed: {str(error)}") from error
            finally:
                manager.release_account(account)
        if isinstance(last_error, (InstagramAuthError, AuthenticationError)):
            raise DownloadError("Authentication failed after account rotation retry") from last_error
        self._raise_final_download_error(last_error, fast_error)

    async def _download_with_single_account(
        self, url: str, output_dir: Path, fast_error: Optional[Exception]
    ) -> VideoInfo:
        """Download with the configured single Instagram account."""
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                await self._apply_instagram_throttle("__single__")
                client = self._build_single_account_client()
                return self.instagram_adapter.download_with_instagram_client(
                    client=client,
                    url=url,
                    output_dir=output_dir,
                    redact_proxy=self._redact_proxy,
                )
            except (InstagramAuthError, AuthenticationError) as auth_error:
                last_error = auth_error
                logger.warning(
                    "Authentication-like failure during single-account download",
                    extra={"attempt": attempt + 1, "error": str(auth_error)},
                )
            except Exception as error:
                if isinstance(error, DownloadError):
                    raise
                last_error = error
                raise DownloadError(f"Download failed: {str(error)}") from error
        if isinstance(last_error, (InstagramAuthError, AuthenticationError)):
            raise DownloadError("Authentication failed after account rotation retry") from last_error
        self._raise_final_download_error(last_error, fast_error)

    async def _download_twitter_media(self, url: str, output_dir: Path) -> VideoInfo:
        """Download Twitter/X media using yt-dlp."""
        return await self.twitter_adapter.download(url, output_dir)

    async def _download_youtube_media(self, url: str, output_dir: Path) -> VideoInfo:
        """Download YouTube Shorts media using yt-dlp."""
        return await self.youtube_adapter.download(url, output_dir)

    @staticmethod
    def _is_twitter_url(url: str) -> bool:
        """Check if URL targets Twitter/X status routes."""
        return TwitterDownloader.is_supported_url(url)

    @staticmethod
    def _is_twitter_domain_url(url: str) -> bool:
        """Check if URL points to any Twitter/X host, regardless of path."""
        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False
        host = (parsed.hostname or "").lower()
        return host in {
            "twitter.com",
            "www.twitter.com",
            "m.twitter.com",
            "mobile.twitter.com",
            "x.com",
            "www.x.com",
        }

    async def _apply_instagram_throttle(self, key: str) -> None:
        """Apply a conservative per-key delay before Instagram work."""
        sleep_for = 0.0
        with self._throttle_lock:
            last_download = self._last_instagram_download_by_key.get(key, 0.0)
            current_time = time.time()
            time_since_last = current_time - last_download
            if time_since_last < self.min_delay_between_downloads:
                sleep_for = self.min_delay_between_downloads - time_since_last
            self._last_instagram_download_by_key[key] = current_time + sleep_for

        if sleep_for > 0:
            logger.info("Instagram throttle delay %.1fs for key %s", sleep_for, key)
            await asyncio.sleep(sleep_for)

        if self.random_delay_range[1] > 0:
            jitter = random.uniform(*self.random_delay_range)
            logger.debug("Adding Instagram jitter delay: %.1fs", jitter)
            await asyncio.sleep(jitter)

    @staticmethod
    def _raise_final_download_error(
        last_error: Optional[Exception], fast_error: Optional[Exception]
    ) -> None:
        if last_error:
            if fast_error:
                raise DownloadError(
                    f"Download failed: {str(last_error)} (fast_path_error={str(fast_error)})"
                )
            raise DownloadError(f"Download failed: {str(last_error)}")
        if fast_error:
            raise DownloadError(f"Download failed: fast_path_error={str(fast_error)}")
        raise DownloadError("Download failed")

    @staticmethod
    def _is_youtube_shorts_url(url: str) -> bool:
        """Check if URL targets YouTube Shorts routes."""
        return YouTubeShortsDownloader.is_supported_url(url)

    @staticmethod
    def _is_youtube_domain_url(url: str) -> bool:
        """Check if URL points to YouTube regardless of path."""
        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False
        return (parsed.hostname or "").lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"}
