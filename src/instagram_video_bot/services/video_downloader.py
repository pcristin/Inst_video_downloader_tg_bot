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
        """Initialize the video downloader with default settings."""
        # Rotate user agents to look more natural
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        ]
        
        # Track last download time for rate limiting
        self.last_download_time = 0
        self.min_delay_between_downloads = 10  # Minimum 10 seconds between downloads
        
    def _get_ydl_opts(self) -> Dict[str, Any]:
        """Get yt-dlp options with randomized headers."""
        # Random user agent for each download
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
            'verbose': True,
            'no_warnings': False,
            'quiet': False,
            'no_color': True,
            'recode_video': 'mp4',
            
            # Add proxy if configured
            'proxy': proxy,
            
            # More realistic headers
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
                'sec-ch-ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"'
            },
            
            # Additional options to look more human
            'sleep_interval': 2,  # Sleep 2 seconds between requests
            'max_sleep_interval': 5,  # Max 5 seconds
            'sleep_interval_subtitles': 1,
            
            
            # Retry options
            'retries': 3,
            'retry_sleep': 5,
            
            # FFmpeg options for video processing
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
                '-aspect', f'{settings.VIDEO_WIDTH}:{settings.VIDEO_HEIGHT}',
                '-c:a', 'aac',
                '-b:a', settings.VIDEO_BITRATE,
                '-movflags', '+faststart'
            ],
        }
        
        return opts

    async def download_video(self, url: str, output_dir: Path) -> VideoInfo:
        """
        Download a video from Instagram with anti-detection measures.
        
        Args:
            url: Instagram video URL
            output_dir: Directory to save the video
        
        Returns:
            VideoInfo object containing downloaded video information
        
        Raises:
            AuthenticationError: If Instagram authentication fails
            DownloadError: If video download fails
        """
        # Rate limiting to avoid detection
        current_time = time.time()
        time_since_last = current_time - self.last_download_time
        
        if time_since_last < self.min_delay_between_downloads:
            delay = self.min_delay_between_downloads - time_since_last
            logger.info(f"Rate limiting: waiting {delay:.1f} seconds before download")
            await asyncio.sleep(delay)
        
        # Add random delay to look more human (1-3 seconds)
        random_delay = random.uniform(1, 3)
        logger.info(f"Adding random delay of {random_delay:.1f} seconds")
        await asyncio.sleep(random_delay)
        
        # Get fresh options for each download
        ydl_opts = self._get_ydl_opts()
        ydl_opts['outtmpl'] = str(output_dir / '%(title)s.%(ext)s')

        # Log the user agent being used
        logger.info(f"Using User-Agent: {ydl_opts['http_headers']['User-Agent'][:50]}...")

        try:
            with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info is None:
                        raise DownloadError("Failed to extract video info")

                    video_path = Path(ydl.prepare_filename(info))
                    if not video_path.exists():
                        raise DownloadError(f"Video file not found at {video_path}")

                    logger.info(f"Video downloaded successfully to {video_path}")
                
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
            
            # Check for authentication-related errors
            if any(phrase in error_str for phrase in [
                "login required", 
                "rate-limit reached", 
                "no csrf token",
                "authentication failed",
                "cookies",
                "locked behind the login page"
            ]):
                # Try to rotate to a different account
                manager = get_account_manager()
                
                if manager.current_account:
                    logger.warning(f"Account {manager.current_account.username} seems to have issues")
                    manager.mark_account_banned(manager.current_account)
                    
                    # Try with a new account
                    if manager.rotate_account():
                        logger.info("Switched to a new account, retrying download...")
                        # Retry with new account
                        return await self.download_video(url, output_dir)
                
                raise AuthenticationError(
                    "Instagram authentication failed. All accounts may be exhausted. "
                    "Please check account status with: python3 manage_accounts.py status"
                )
            
            # Check for rate limiting
            if "rate-limit" in error_str or "too many requests" in error_str:
                raise DownloadError(
                    "Instagram rate limit reached. Please wait a few minutes and try again."
                )
            
            # Generic download error
            raise DownloadError(f"Download failed: {str(e)}") 