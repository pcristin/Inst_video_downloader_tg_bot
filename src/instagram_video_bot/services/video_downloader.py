"""Instagram video downloading service using instagrapi."""
import asyncio
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .instagram_client import InstagramAuthError, InstagramClient
from ..config.settings import settings
from ..utils.account_manager import get_account_manager

logger = logging.getLogger(__name__)

@dataclass
class VideoInfo:
    """Video information container."""
    file_path: Path
    title: str
    duration: Optional[float] = None
    description: Optional[str] = None

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
                file_path = client.download_video(url, output_dir)
                if not file_path:
                    raise DownloadError("Failed to download video")

                media_info = {'title': '', 'duration': 0}
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

                self.last_download_time = time.time()
                return VideoInfo(
                    file_path=file_path,
                    title=media_info.get('title', ''),
                    duration=media_info.get('duration'),
                    description=media_info.get('title', '')
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
            raise DownloadError(f"Download failed: {str(last_error)}")
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
