"""Provider-specific media adapters used by the download coordinator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional, cast

from .download_models import AuthenticationError, DownloadError, MediaItem, VideoInfo
from .instagram_client import InstagramAuthError, InstagramClient
from .instagram_fast_extractor import InstagramFastExtractor, InstagramFastExtractorError
from .media_metadata import probe_video_metadata
from .twitter_downloader import TwitterDownloadError, TwitterDownloader
from .youtube_downloader import YouTubeDownloadError, YouTubeShortsDownloader
from ..config.settings import settings

logger = logging.getLogger(__name__)


class InstagramProviderAdapter:
    """Instagram-specific execution helpers."""

    def __init__(self, fast_extractor: InstagramFastExtractor):
        self.fast_extractor = fast_extractor

    def build_single_account_client(self) -> InstagramClient:
        """Create and login a single-account client."""
        client = InstagramClient(
            username=settings.IG_USERNAME,
            password=settings.IG_PASSWORD,
            totp_secret=settings.TOTP_SECRET,
        )
        if not client.login():
            raise AuthenticationError("Failed to login to Instagram")
        return client

    def build_leased_client(
        self,
        *,
        username: str,
        password: str,
        session_file: Path | None,
        proxy: str | None,
        totp_secret: str | None,
    ) -> InstagramClient:
        """Create and login a leased-account client."""
        client = InstagramClient(
            username=username,
            password=password,
            session_file=session_file,
            proxy=proxy,
            totp_secret=totp_secret,
        )
        if not client.login():
            raise AuthenticationError("Failed to login to Instagram")
        return client

    def download_with_fast_method(self, url: str, output_dir: Path) -> VideoInfo:
        """Attempt the fast extractor method and map result into VideoInfo."""
        if not self.fast_extractor:
            raise InstagramFastExtractorError("Fast extractor is not configured")

        self.fast_extractor.proxy = settings.get_single_proxy()
        result = self.fast_extractor.extract_and_download(url, output_dir)
        if not result.media_items:
            raise InstagramFastExtractorError("Fast extractor returned no media")

        media_items = [
            self._build_media_item(
                file_path=item.file_path,
                media_type=item.media_type,
                caption=result.caption or None,
                duration=item.duration,
                width=item.width,
                height=item.height,
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

    def download_with_instagram_client(
        self,
        *,
        client: InstagramClient,
        url: str,
        output_dir: Path,
        redact_proxy,
    ) -> VideoInfo:
        """Download Instagram media and map metadata into VideoInfo."""
        account_name = client.username
        proxy_value = client.proxy or settings.get_single_proxy() or "none"
        redacted_proxy = redact_proxy(proxy_value)
        logger.info(
            "Starting Instagram download attempt",
            extra={
                "username": account_name,
                "proxy": redacted_proxy,
            },
        )

        downloaded_paths = self._download_with_legacy_client(client, url, output_dir)
        if not downloaded_paths:
            failure_class = getattr(client, "last_failure_class", None)
            if failure_class == "auth_challenge":
                reason = getattr(client, "last_failure_reason", None) or failure_class
                raise InstagramAuthError(str(reason))
            if failure_class and failure_class != "download_failed":
                raise DownloadError(f"Instagram download failed: {failure_class}")
            raise DownloadError("Failed to download video")

        if isinstance(downloaded_paths, list):
            file_paths = downloaded_paths
        else:
            file_paths = [downloaded_paths]

        media_info = {"title": "", "duration": 0}
        try:
            info = client.get_media_info(url)
            if info:
                media_info = info
        except InstagramAuthError as auth_error:
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

        media_items = []
        for file_path in file_paths:
            media_type = self._infer_media_type(file_path)
            media_items.append(
                self._build_media_item(
                    file_path=file_path,
                    media_type=media_type,
                    caption=media_info.get("title") or None,
                    duration=media_info.get("duration"),
                )
            )
        primary_item = media_items[0]
        return VideoInfo(
            file_path=primary_item.file_path,
            title=media_info.get("title", ""),
            duration=primary_item.duration,
            description=media_info.get("title", ""),
            media_items=media_items,
            primary_media_type=primary_item.media_type,
        )

    def is_story_url(self, url: str) -> bool:
        """Check if URL targets Instagram stories."""
        return "/stories/" in url.lower()

    @staticmethod
    def _download_with_legacy_client(
        client: InstagramClient, url: str, output_dir: Path
    ) -> Optional[Path | list[Path]]:
        """Download media using existing authenticated client path."""
        if hasattr(client, "download_media"):
            return client.download_media(url, output_dir)
        return client.download_video(url, output_dir)

    @staticmethod
    def _infer_media_type(file_path: Path) -> str:
        ext = file_path.suffix.lower()
        if ext in {".mp4", ".mov", ".mkv", ".webm"}:
            return "video"
        return "photo"

    @staticmethod
    def _build_media_item(
        *,
        file_path: Path,
        media_type: str,
        caption: str | None = None,
        duration: float | int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> MediaItem:
        """Build a media item and fill missing video metadata from the local file."""
        duration_is_missing = InstagramProviderAdapter._is_missing_video_duration(duration)
        if media_type == "video" and (duration_is_missing or not width or not height):
            metadata = probe_video_metadata(file_path)
            duration = metadata.duration if duration_is_missing else duration
            width = width or metadata.width
            height = height or metadata.height

        return MediaItem(
            file_path=file_path,
            media_type=cast(Literal["video", "photo"], media_type),
            caption=caption,
            duration=duration,
            width=width,
            height=height,
        )

    @staticmethod
    def _is_missing_video_duration(duration: float | int | None) -> bool:
        if duration is None:
            return True
        try:
            return float(duration) <= 0
        except (TypeError, ValueError):
            return True


class TwitterProviderAdapter:
    """Twitter/X adapter around yt-dlp downloader."""

    def __init__(self, downloader: TwitterDownloader):
        self.downloader = downloader

    async def download(self, url: str, output_dir: Path) -> VideoInfo:
        try:
            result = await self.downloader.download_media(url, output_dir)
        except TwitterDownloadError as error:
            raise DownloadError(str(error)) from error

        if not result.media_items:
            raise DownloadError("Twitter/X download returned no media items")

        media_items = [
            InstagramProviderAdapter._build_media_item(
                file_path=item.file_path,
                media_type=item.media_type,
                caption=result.title or None,
            )
            for item in result.media_items
        ]
        primary_item = media_items[0]
        return VideoInfo(
            file_path=primary_item.file_path,
            title=result.title or "",
            description=result.title or "",
            media_items=media_items,
            primary_media_type=primary_item.media_type,
        )


class YouTubeShortsProviderAdapter:
    """YouTube Shorts adapter around yt-dlp downloader."""

    def __init__(self, downloader: YouTubeShortsDownloader):
        self.downloader = downloader

    async def download(self, url: str, output_dir: Path) -> VideoInfo:
        try:
            result = await self.downloader.download_media(url, output_dir)
        except YouTubeDownloadError as error:
            raise DownloadError(str(error)) from error

        if not result.media_items:
            raise DownloadError("YouTube Shorts download returned no media items")

        media_items = [
            InstagramProviderAdapter._build_media_item(
                file_path=item.file_path,
                media_type=item.media_type,
                caption=result.title or None,
            )
            for item in result.media_items
        ]
        primary_item = media_items[0]
        return VideoInfo(
            file_path=primary_item.file_path,
            title=result.title or "",
            description=result.title or "",
            media_items=media_items,
            primary_media_type=primary_item.media_type,
        )
