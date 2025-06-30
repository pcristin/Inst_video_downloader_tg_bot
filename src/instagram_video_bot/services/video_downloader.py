"""Instagram video downloading service."""
import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

from yt_dlp import YoutubeDL

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
    """Service for downloading Instagram videos."""

    def __init__(self):
        """Initialize the video downloader."""
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        ]
        
        self.last_download_time = 0
        self.min_delay_between_downloads = 5  # Minimum 5 seconds between downloads
        
    def _get_ydl_opts(self) -> Dict[str, Any]:
        """Get yt-dlp options with realistic headers."""
        user_agent = random.choice(self.user_agents)
        
        # Build proxy string if configured
        proxy = None
        if settings.PROXY_HOST and settings.PROXY_PORT:
            if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
                proxy = f'http://{settings.PROXY_USERNAME}:{settings.PROXY_PASSWORD}@{settings.PROXY_HOST}:{settings.PROXY_PORT}'
            else:
                proxy = f'http://{settings.PROXY_HOST}:{settings.PROXY_PORT}'
        
        opts = {
            'format': 'best',
            'cookiefile': str(settings.COOKIES_FILE),
            'verbose': False,
            'no_warnings': True,
            'quiet': True,
            'no_color': True,
            'recode_video': 'mp4',
            
            # Proxy configuration
            'proxy': proxy,
            
            # Realistic headers
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
            
            # Rate limiting
            'sleep_interval': 2,
            'max_sleep_interval': 5,
            
            # Retry configuration
            'retries': 3,
            'retry_sleep': 5,
            
            # Video processing options
            'ffmpeg_args': [
                '-vf', (
                    'format=yuv420p,'
                    f'scale={settings.VIDEO_WIDTH}:{settings.VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,'
                    f'pad={settings.VIDEO_WIDTH}:{settings.VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,'
                    'setsar=1'
                ),
                '-c:v', 'libx264',
                '-profile:v', 'baseline',
                '-level', '3.0',
                '-preset', 'medium',
                '-crf', settings.VIDEO_CRF,
                '-c:a', 'aac',
                '-b:a', settings.VIDEO_BITRATE,
                '-movflags', '+faststart'
            ],
        }
        
        return opts

    async def download_video(self, url: str, output_dir: Path) -> VideoInfo:
        """
        Download a video from Instagram.
        
        Args:
            url: Instagram video URL
            output_dir: Directory to save the video
        
        Returns:
            VideoInfo object containing downloaded video information
        
        Raises:
            AuthenticationError: If Instagram authentication fails
            DownloadError: If video download fails
        """
        # Basic rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_download_time
        
        if time_since_last < self.min_delay_between_downloads:
            delay = self.min_delay_between_downloads - time_since_last
            logger.info(f"Rate limiting: waiting {delay:.1f} seconds")
            await asyncio.sleep(delay)
        
        # Add small random delay
        random_delay = random.uniform(1, 3)
        await asyncio.sleep(random_delay)
        
        # Get download options
        ydl_opts = self._get_ydl_opts()
        ydl_opts['outtmpl'] = str(output_dir / '%(title)s.%(ext)s')

        logger.info(f"Downloading video from: {url}")

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise DownloadError("Failed to extract video info")

                video_path = Path(ydl.prepare_filename(info))
                if not video_path.exists():
                    raise DownloadError(f"Video file not found at {video_path}")

                logger.info(f"Video downloaded successfully: {video_path}")
                
                # Update last download time
                self.last_download_time = time.time()
                
                return VideoInfo(
                    file_path=video_path,
                    title=info.get('title', ''),
                    duration=info.get('duration'),
                    description=info.get('description')
                )
                
        except Exception as e:
            error_str = str(e).lower()
            
            # Check for authentication errors
            if any(phrase in error_str for phrase in [
                "login required", 
                "rate-limit reached", 
                "no csrf token",
                "authentication failed",
                "cookies",
                "locked behind the login page"
            ]):
                await self._handle_auth_error()
                raise AuthenticationError(
                    "Instagram authentication failed. Please check account status."
                )
            
            # Check for rate limiting
            if "rate-limit" in error_str or "too many requests" in error_str:
                raise DownloadError(
                    "Instagram rate limit reached. Please wait and try again."
                )
            
            # Generic download error
            logger.error(f"Download failed: {str(e)}")
            raise DownloadError(f"Download failed: {str(e)}")
    
    async def _handle_auth_error(self) -> None:
        """Handle authentication errors by trying account rotation."""
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