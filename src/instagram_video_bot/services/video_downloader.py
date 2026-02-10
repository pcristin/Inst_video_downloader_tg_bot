"""Instagram video downloading service using instagrapi."""
import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional

from .instagram_client import InstagramAuthError, InstagramClient
from .instagram_fast_extractor import InstagramFastExtractor, InstagramFastExtractorError
from ..config.settings import settings
from ..utils.account_manager import get_account_manager

logger = logging.getLogger(__name__)


@dataclass
class MediaItem:
    """Represents one downloaded media file."""

    file_path: Path
    media_type: Literal["video", "photo"]
    caption: Optional[str] = None
    duration: Optional[float] = None


@dataclass
class VideoInfo:
    """Video information container."""

    file_path: Path
    title: str
    duration: Optional[float] = None
    description: Optional[str] = None
    media_items: List[MediaItem] = field(default_factory=list)
    primary_media_type: Literal["video", "photo"] = "video"


class VideoDownloadError(Exception):
    """Base exception for video download errors."""

    pass


class AuthenticationError(VideoDownloadError):
    """Raised when authentication fails."""

    pass


class DownloadError(VideoDownloadError):
    """Raised when video download fails."""

    pass


class VideoDownloader:
    """Service for downloading Instagram videos using instagrapi."""

    def __init__(self):
        """Initialize the video downloader."""
        self.client: Optional[InstagramClient] = None
        self.last_download_time = 0
        self.min_delay_between_downloads = 10
        self.random_delay_range = (1.0, 3.0)
        self.fast_extractor = InstagramFastExtractor(
            timeout_connect=settings.IG_FAST_TIMEOUT_CONNECT,
            timeout_read=settings.IG_FAST_TIMEOUT_READ,
        )

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
        
    def _get_client(self) -> InstagramClient:
        """Get or create Instagram client."""
        if self.client is None:
            # Check if using multi-account mode
            manager = get_account_manager()
            if manager and manager.current_account:
                account = manager.current_account
                self.client = InstagramClient(
                    username=account.username,
                    password=account.password,
                    session_file=account.session_file,
                    proxy=account.proxy,
                    totp_secret=account.totp_secret
                )
            else:
                # Single account mode
                self.client = InstagramClient(
                    username=settings.IG_USERNAME,
                    password=settings.IG_PASSWORD,
                    totp_secret=settings.TOTP_SECRET
                )
            
            if not self.client.login():
                raise AuthenticationError("Failed to login to Instagram")
                
        return self.client

    async def download_video(self, url: str, output_dir: Path) -> VideoInfo:
        """Download a video from Instagram using instagrapi."""
        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_download_time
        
        if time_since_last < self.min_delay_between_downloads:
            delay = self.min_delay_between_downloads - time_since_last
            logger.info(f"Rate limiting: waiting {delay:.1f} seconds")
            await asyncio.sleep(delay)

        if self.random_delay_range[1] > 0:
            jitter = random.uniform(*self.random_delay_range)
            logger.debug(f"Adding jitter delay: {jitter:.1f} seconds")
            await asyncio.sleep(jitter)

        fast_error: Optional[Exception] = None
        is_story_url = self._is_story_url(url)
        if settings.IG_FAST_METHOD_ENABLED and not is_story_url:
            try:
                fast_result = self._download_with_fast_method(url, output_dir)
                self.last_download_time = time.time()
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

        last_error: Optional[Exception] = None
        for attempt in range(2):
            rotated = attempt == 1
            try:
                client = self._get_client()
                account_name = client.username
                proxy_value = client.proxy or settings.get_single_proxy() or "none"
                redacted_proxy = self._redact_proxy(proxy_value)
                logger.info(
                    "Starting download attempt",
                    extra={
                        "username": account_name,
                        "proxy": redacted_proxy,
                        "attempt": attempt + 1,
                        "rotated": rotated,
                    },
                )

                # Download first; metadata is best-effort.
                file_path = self._download_with_legacy_client(client, url, output_dir)
                if not file_path:
                    raise DownloadError("Failed to download video")

                media_info = {"title": "", "duration": 0}
                try:
                    info = client.get_media_info(url)
                    if info:
                        media_info = info
                    else:
                        logger.warning(
                            "Metadata unavailable after download",
                            extra={
                                "username": account_name,
                                "failure_class": "metadata_unavailable",
                            },
                        )
                except InstagramAuthError as auth_error:
                    # Download already succeeded; keep response path healthy.
                    logger.warning(
                        "Metadata failed with auth error after download",
                        extra={
                            "username": account_name,
                            "failure_class": "metadata_unavailable",
                            "error": str(auth_error),
                        },
                    )
                except Exception as metadata_error:
                    logger.warning(
                        "Metadata lookup failed after download",
                        extra={
                            "username": account_name,
                            "failure_class": "metadata_unavailable",
                            "error": str(metadata_error),
                        },
                    )

                media_type = self._infer_media_type(file_path)
                media_item = MediaItem(
                    file_path=file_path,
                    media_type=media_type,
                    caption=media_info.get("title") or None,
                    duration=media_info.get("duration"),
                )
                self.last_download_time = time.time()
                return VideoInfo(
                    file_path=file_path,
                    title=media_info.get("title", ""),
                    duration=media_info.get("duration"),
                    description=media_info.get("title", ""),
                    media_items=[media_item],
                    primary_media_type=media_type,
                )

            except (InstagramAuthError, AuthenticationError) as auth_error:
                last_error = auth_error
                logger.warning(
                    "Authentication-like failure during download",
                    extra={
                        "failure_class": "auth_challenge",
                        "attempt": attempt + 1,
                        "rotated": rotated,
                        "error": str(auth_error),
                    },
                )
                if attempt == 0:
                    await self._handle_auth_error()
                    continue
                raise DownloadError("Authentication failed after account rotation retry") from auth_error
            except DownloadError as download_error:
                last_error = download_error
                logger.error(
                    "Download failed",
                    extra={"failure_class": "download_failed", "error": str(download_error)},
                )
                raise
            except Exception as e:
                last_error = e
                logger.error(f"Download failed: {e}")
                raise DownloadError(f"Download failed: {str(e)}") from e

        if last_error:
            if fast_error:
                raise DownloadError(
                    f"Download failed: {str(last_error)} (fast_path_error={str(fast_error)})"
                )
            raise DownloadError(f"Download failed: {str(last_error)}")
        if fast_error:
            raise DownloadError(f"Download failed: fast_path_error={str(fast_error)}")
        raise DownloadError("Download failed")

    async def _handle_auth_error(self) -> None:
        """Handle authentication errors by trying account rotation."""
        self.client = None  # Reset client

        manager = get_account_manager()
        if manager and manager.current_account:
            current_username = manager.current_account.username
            logger.warning(
                "Account marked for rotation due to auth failure",
                extra={"username": current_username, "failure_class": "auth_challenge"},
            )
            manager.mark_account_banned(manager.current_account)

            # mark_account_banned already attempts rotation internally.
            if manager.current_account and manager.current_account.username != current_username:
                logger.info(
                    "Successfully rotated to a new account",
                    extra={"username": manager.current_account.username, "rotated": True},
                )
            else:
                logger.error("No accounts available for rotation")
        else:
            logger.warning("No account manager available for rotation")

    def _download_with_fast_method(self, url: str, output_dir: Path) -> VideoInfo:
        """Attempt the new fast extractor method and map result into VideoInfo."""
        if not self.fast_extractor:
            raise InstagramFastExtractorError("Fast extractor is not configured")

        self.fast_extractor.proxy = self._get_fast_proxy()
        result = self.fast_extractor.extract_and_download(url, output_dir)
        if not result.media_items:
            raise InstagramFastExtractorError("Fast extractor returned no media")

        media_items = [
            MediaItem(
                file_path=item.file_path,
                media_type=item.media_type,
                caption=result.caption or None,
                duration=item.duration,
            )
            for item in result.media_items
        ]
        primary_item = media_items[0]
        return VideoInfo(
            file_path=primary_item.file_path,
            title=result.caption or "",
            duration=primary_item.duration,
            description=result.caption or "",
            media_items=media_items,
            primary_media_type=primary_item.media_type,
        )

    def _download_with_legacy_client(
        self, client: InstagramClient, url: str, output_dir: Path
    ) -> Optional[Path]:
        """Download media using existing authenticated client path."""
        if hasattr(client, "download_media"):
            return client.download_media(url, output_dir)
        return client.download_video(url, output_dir)

    @staticmethod
    def _infer_media_type(file_path: Path) -> Literal["video", "photo"]:
        """Infer media type from extension for legacy downloader responses."""
        ext = file_path.suffix.lower()
        if ext in {".mp4", ".mov", ".mkv", ".webm"}:
            return "video"
        return "photo"

    @staticmethod
    def _is_story_url(url: str) -> bool:
        """Check if URL targets Instagram stories."""
        return "/stories/" in url.lower()

    @staticmethod
    def _get_fast_proxy() -> Optional[str]:
        """Resolve proxy for fast extractor requests."""
        manager = get_account_manager()
        if manager and manager.current_account and manager.current_account.proxy:
            return manager.current_account.proxy
        return settings.get_single_proxy()
