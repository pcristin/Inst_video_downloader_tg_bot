"""Instagram video downloading service."""
import asyncio
import logging
import random
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

import yt_dlp

from ..config.settings import settings
from ..utils.account_manager import get_account_manager
from ..utils.proxy_manager import get_proxy_for_account

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
        
    def _get_ydl_opts(self, account_name: Optional[str] = None) -> Dict[str, Any]:
        """Get yt-dlp options with realistic headers and account-specific proxy."""
        user_agent = random.choice(self.user_agents)
        
        # Get proxy for the specific account
        proxy = None
        if account_name:
            proxy_config = get_proxy_for_account(account_name)
            if proxy_config:
                proxy = proxy_config.url
                logger.info(f"Using proxy for {account_name}: {proxy_config.host}:{proxy_config.port}")
        else:
            # Fallback to legacy single proxy config if no account specified
            if settings.PROXY_HOST and settings.PROXY_PORT:
                if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
                    proxy = f'http://{settings.PROXY_USERNAME}:{settings.PROXY_PASSWORD}@{settings.PROXY_HOST}:{settings.PROXY_PORT}'
                else:
                    proxy = f'http://{settings.PROXY_HOST}:{settings.PROXY_PORT}'
        
        # Get cookies file for the account
        cookies_file = settings.COOKIES_FILE
        if account_name:
            account_cookies = Path(settings.COOKIES_FILE.parent / f"{account_name}_cookies.txt")
            if account_cookies.exists():
                cookies_file = account_cookies
        
        opts = {
            'format': 'best[ext=mp4]/best',
            'cookiefile': str(cookies_file),
            'verbose': True,  # Enable verbose for debugging
            'no_warnings': False,
            'quiet': False,
            'no_color': True,
            
            # Force overwrite to avoid "already downloaded" issues
            'overwrites': True,
            'nooverwrites': False,
            
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
            
            # Disable post-processing for now to avoid empty files
            'postprocessors': [],
        }
        
        return opts

    async def _enforce_rate_limit(self):
        """Enforce rate limiting between downloads."""
        current_time = time.time()
        time_since_last = current_time - self.last_download_time
        
        if time_since_last < self.min_delay_between_downloads:
            sleep_time = self.min_delay_between_downloads - time_since_last
            logger.info(f"Rate limiting: sleeping for {sleep_time:.1f} seconds")
            await asyncio.sleep(sleep_time)
        
        self.last_download_time = time.time()

    async def download_video(self, url: str, retry_on_auth_error: bool = True) -> VideoInfo:
        """
        Download Instagram video from URL.
        
        Args:
            url: Instagram video URL
            retry_on_auth_error: Whether to retry with account rotation on auth errors
            
        Returns:
            VideoInfo object containing file path and metadata
            
        Raises:
            VideoDownloadError: If download fails
        """
        await self._enforce_rate_limit()
        
        # Get current account for proxy assignment
        account_manager = get_account_manager()
        current_account = account_manager.current_account if account_manager else None
        account_name = current_account.username if current_account else None
        
        if account_name:
            logger.info(f"Downloading with account: {account_name}")
        
        ydl_opts = self._get_ydl_opts(account_name)
        
        # Create temporary file for download
        import uuid
        temp_filename = str(Path(settings.TEMP_DIR) / f"video_{uuid.uuid4().hex}.mp4")
        
        # Clean up any existing file with same name
        if Path(temp_filename).exists():
            Path(temp_filename).unlink()
        
        ydl_opts.update({
            'outtmpl': temp_filename.replace('.mp4', '.%(ext)s'),
        })
        
        try:
            # Run yt-dlp operations in thread pool to avoid blocking event loop
            loop = asyncio.get_event_loop()
            
            def _download_sync():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Extract info first
                    logger.info(f"Extracting video info from: {url}")
                    info = ydl.extract_info(url, download=False)
                    
                    if not info:
                        raise VideoDownloadError("Could not extract video information")
                    
                    # Download the video
                    logger.info("Downloading video...")
                    ydl.download([url])
                    return info
            
            info = await loop.run_in_executor(None, _download_sync)
            
            # Wait a bit to ensure file is written
            await asyncio.sleep(0.5)
            
            # Find the actual downloaded file
            video_file = None
            temp_dir = Path(temp_filename).parent
            base_name = Path(temp_filename).stem
            
            # Look for the downloaded file with various extensions
            for ext in ['.mp4', '.webm', '.mkv', '.m4a']:
                potential_file = Path(temp_dir) / f"{base_name}{ext}"
                if potential_file.exists():
                    video_file = str(potential_file)
                    break
            
            if not video_file:
                # List all files in temp dir for debugging
                logger.error(f"Expected file pattern: {base_name}.*")
                logger.error(f"Files in temp dir: {list(temp_dir.glob('*'))}")
                raise VideoDownloadError("Downloaded video file not found")
            
            # Check file size
            video_path = Path(video_file)
            file_size = video_path.stat().st_size
            logger.info(f"Downloaded file size: {file_size} bytes")
            
            if file_size == 0:
                raise VideoDownloadError("Downloaded video file is empty")
            
            # Update account usage on successful download
            if account_manager and current_account:
                from datetime import datetime
                current_account.last_used = datetime.now()
                account_manager._save_state()
                logger.info(f"Updated usage for account: {account_name}")
            
            logger.info(f"Video downloaded successfully: {video_file} ({file_size} bytes)")
            
            return VideoInfo(
                file_path=Path(video_file),
                title=info.get('title', 'Instagram Video'),
                duration=info.get('duration'),
                description=info.get('description')
            )
                
        except yt_dlp.DownloadError as e:
            error_msg = str(e).lower()
            
            # Check for authentication errors
            if any(keyword in error_msg for keyword in [
                'login', 'cookies', 'authentication', 'unauthorized', 
                'forbidden', 'private', 'not available', 'age-gated'
            ]):
                logger.warning(f"Authentication error detected: {e}")
                
                if retry_on_auth_error and account_manager and current_account:
                    logger.info("Attempting to rotate account and retry...")
                    
                    # Mark current account as having issues and rotate
                    account_manager.mark_account_banned(current_account)
                    
                    # Add delay between account switches to appear more human
                    delay = random.uniform(10, 20)
                    logger.info(f"Waiting {delay:.1f} seconds before retry...")
                    await asyncio.sleep(delay)
                    
                    # Retry with new account (recursive call with retry disabled to avoid loops)
                    return await self.download_video(url, retry_on_auth_error=False)
                else:
                    raise VideoDownloadError(f"Authentication failed: {e}")
            else:
                # Other download errors
                raise VideoDownloadError(f"Download failed: {e}")
                
        except Exception as e:
            # Clean up temp file on error
            try:
                if Path(temp_filename).exists():
                    Path(temp_filename).unlink()
            except:
                pass
            
            raise VideoDownloadError(f"Unexpected error during download: {e}")

    def get_video_info(self, url: str) -> Dict[str, Any]:
        """
        Get video information without downloading.
        
        Args:
            url: Instagram video URL
            
        Returns:
            Dictionary containing video metadata
            
        Raises:
            VideoDownloadError: If info extraction fails
        """
        # Get current account for proxy assignment
        account_manager = get_account_manager()
        current_account = account_manager.current_account if account_manager else None
        account_name = current_account.username if current_account else None
        
        ydl_opts = self._get_ydl_opts(account_name)
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Extracting video info from: {url}")
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise VideoDownloadError("Could not extract video information")
                
                return info
                
        except yt_dlp.DownloadError as e:
            raise VideoDownloadError(f"Failed to get video info: {e}")
        except Exception as e:
            raise VideoDownloadError(f"Unexpected error getting video info: {e}")

# Create a global instance
video_downloader = VideoDownloader() 