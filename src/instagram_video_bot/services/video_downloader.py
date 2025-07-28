"""Instagram video downloading service using instagrapi."""
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .instagram_client import InstagramClient
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
        self.min_delay_between_downloads = 2  # Reduced since instagrapi is more efficient
        
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

        try:
            client = self._get_client()
            
            # Get media info first for metadata (title, duration, etc.)
            media_info = client.get_media_info(url)
            if not media_info:
                raise DownloadError("Failed to get media information")
            
            # Always attempt video download (since we only handle videos/reels)
            file_path = client.download_video(url, output_dir)
            
            if not file_path:
                raise DownloadError("Failed to download video")
            
            self.last_download_time = time.time()
            
            return VideoInfo(
                file_path=file_path,
                title=media_info.get('title', ''),
                duration=media_info.get('duration'),
                description=media_info.get('title', '')
            )
            
        except AuthenticationError:
            await self._handle_auth_error()
            raise
        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise DownloadError(f"Download failed: {str(e)}")
    
    async def _handle_auth_error(self) -> None:
        """Handle authentication errors by trying account rotation."""
        self.client = None  # Reset client
        
        manager = get_account_manager()
        if manager and manager.current_account:
            logger.warning(f"Account {manager.current_account.username} seems to have issues")
            manager.mark_account_banned(manager.current_account)
            
            # Try rotating to a new account
            if manager.rotate_account():
                logger.info("Successfully rotated to a new account")
            else:
                logger.error("No accounts available for rotation")
        else:
            logger.warning("No account manager available for rotation") 