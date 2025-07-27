"""Instagram client using instagrapi."""
import logging
import time
from pathlib import Path
from typing import Optional

from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, 
    BadPassword, 
    ChallengeRequired,
    FeedbackRequired,
    PleaseWaitFewMinutes,
    ClientError
)

from ..config.settings import settings

logger = logging.getLogger(__name__)

class InstagramClient:
    """Instagram client wrapper using instagrapi."""
    
    def __init__(self, username: str, password: str, session_file: Optional[Path] = None, proxy: Optional[str] = None):
        self.username = username
        self.password = password
        self.proxy = proxy
        self.session_file = session_file or settings.BASE_DIR / "sessions" / f"{username}.json"
        self.client = Client()
        self._setup_proxy()
        
    def _setup_proxy(self):
        """Configure proxy if available."""
        proxy_to_use = None
        
        # Use provided proxy first
        if self.proxy:
            proxy_to_use = self.proxy
        # Fall back to single proxy from settings
        elif settings.get_single_proxy():
            proxy_to_use = settings.get_single_proxy()
        
        if proxy_to_use:
            self.client.set_proxy(proxy_to_use)
            logger.info(f"Proxy configured: {proxy_to_use}")
    
    def login(self) -> bool:
        """Login to Instagram with session persistence."""
        try:
            # Try loading existing session first
            if self.session_file.exists():
                logger.info(f"Loading session from {self.session_file}")
                session = self.client.load_settings(self.session_file)
                if session:
                    self.client.set_settings(session)
                    
                    # Test if session is still valid
                    try:
                        self.client.get_timeline_feed()
                        logger.info("Session is valid")
                        return True
                    except LoginRequired:
                        logger.info("Session expired, need fresh login")
                        # Keep device UUIDs for consistency
                        old_session = self.client.get_settings()
                        self.client.set_settings({})
                        self.client.set_uuids(old_session["uuids"])
            
            # Fresh login
            logger.info(f"Logging in as {self.username}")
            
            # Handle 2FA if needed
            verification_code = None
            if settings.TOTP_SECRET:
                import pyotp
                totp = pyotp.TOTP(settings.TOTP_SECRET)
                verification_code = totp.now()
                logger.info("Using TOTP for 2FA")
            
            success = self.client.login(
                self.username, 
                self.password, 
                verification_code=verification_code
            )
            
            if success:
                # Save session for future use
                self.session_file.parent.mkdir(parents=True, exist_ok=True)
                self.client.dump_settings(self.session_file)
                logger.info(f"Login successful, session saved to {self.session_file}")
                return True
                
        except BadPassword:
            logger.error("Invalid username or password")
        except ChallengeRequired as e:
            logger.error(f"Instagram challenge required: {e}")
        except FeedbackRequired as e:
            logger.error(f"Instagram feedback required: {e}")
        except PleaseWaitFewMinutes as e:
            logger.error(f"Rate limited: {e}")
        except Exception as e:
            logger.error(f"Login failed: {e}")
            
        return False
    
    def download_video(self, url: str, output_dir: Path) -> Optional[Path]:
        """Download video using instagrapi."""
        try:
            # Extract media PK from URL
            media_pk = self.client.media_pk_from_url(url)
            logger.info(f"Extracted media PK: {media_pk}")
            
            # Download video directly
            output_dir.mkdir(parents=True, exist_ok=True)
            video_path = self.client.video_download(media_pk, folder=output_dir)
            
            logger.info(f"Video downloaded: {video_path}")
            return video_path
            
        except Exception as e:
            logger.error(f"Video download failed: {e}")
            return None
    
    def download_photo(self, url: str, output_dir: Path) -> Optional[Path]:
        """Download photo using instagrapi."""
        try:
            # Extract media PK from URL
            media_pk = self.client.media_pk_from_url(url)
            logger.info(f"Extracted media PK: {media_pk}")
            
            # Download photo directly
            output_dir.mkdir(parents=True, exist_ok=True)
            photo_path = self.client.photo_download(media_pk, folder=output_dir)
            
            logger.info(f"Photo downloaded: {photo_path}")
            return photo_path
            
        except Exception as e:
            logger.error(f"Photo download failed: {e}")
            return None
    
    def get_media_info(self, url: str) -> Optional[dict]:
        """Get media information."""
        try:
            media_pk = self.client.media_pk_from_url(url)
            media_info = self.client.media_info(media_pk)
            return {
                'title': media_info.caption_text or '',
                'duration': getattr(media_info, 'video_duration', 0),
                'user': media_info.user.username,
                'pk': media_pk,
                'media_type': media_info.media_type,  # 1 = photo, 2 = video, 8 = carousel
                'is_video': media_info.media_type == 2
            }
        except Exception as e:
            logger.error(f"Failed to get media info: {e}")
            return None 