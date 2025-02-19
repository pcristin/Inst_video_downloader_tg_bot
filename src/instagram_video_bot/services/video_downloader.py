"""Instagram video downloading service."""
import asyncio
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from yt_dlp import YoutubeDL

from ..config.settings import settings
from ..utils.instagram_auth import refresh_instagram_cookies, refresh_instagram_cookies_sync

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
        self.ydl_opts = {
            'format': 'best',
            'cookiefile': str(settings.COOKIES_FILE),
            'verbose': True,
            'no_warnings': False,
            'recode_video': 'mp4',
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
        async def try_download(retry: bool = False) -> VideoInfo:
            if retry:
                logger.info("Retrying with fresh cookies...")
                if not await refresh_instagram_cookies():
                    raise AuthenticationError("Failed to refresh Instagram cookies")

            self.ydl_opts['outtmpl'] = str(output_dir / '%(title)s.%(ext)s')

            try:
                with YoutubeDL(self.ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info is None:
                        raise DownloadError("Failed to extract video info")

                    video_path = Path(ydl.prepare_filename(info))
                    if not video_path.exists():
                        raise DownloadError(f"Video file not found at {video_path}")

                    logger.info(f"Video downloaded successfully to {video_path}")
                    return VideoInfo(
                        file_path=video_path,
                        title=info.get('title', ''),
                        duration=info.get('duration'),
                        description=info.get('description')
                    )
            except Exception as e:
                raise DownloadError(f"Download failed: {str(e)}")

        try:
            return await try_download(retry=False)
        except Exception as e:
            error_str = str(e).lower()
            if "login required" in error_str or "rate-limit reached" in error_str:
                logger.info("Authentication failed, retrying with fresh cookies...")
                return await try_download(retry=True)
            raise 