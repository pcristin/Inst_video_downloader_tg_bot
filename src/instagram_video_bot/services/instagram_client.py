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
    
    def __init__(self, username: str, password: str, session_file: Optional[Path] = None, proxy: Optional[str] = None, totp_secret: Optional[str] = None):
        self.username = username
        self.password = password
        self.proxy = proxy
        self.totp_secret = totp_secret
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
            if self.totp_secret and self.totp_secret.strip():
                import pyotp
                totp = pyotp.TOTP(self.totp_secret.strip())
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
            
            try:
                video_path = self.client.video_download(media_pk, folder=output_dir)
                logger.info(f"Video downloaded: {video_path}")
                return video_path
            except Exception as download_error:
                # Log the specific error for debugging
                logger.warning(f"Standard video download failed: {download_error}")
                
                # If standard download fails, try alternative approach
                # Sometimes the download methods work even when info methods fail
                try:
                    # Try downloading by URL directly (some methods support this)
                    video_path = self.client.video_download_by_url(
                        url=url, 
                        folder=output_dir
                    )
                    logger.info(f"Video downloaded via URL method: {video_path}")
                    return video_path
                except Exception as url_download_error:
                    logger.error(f"Both download methods failed: {url_download_error}")
                    raise download_error  # Re-raise the original error
                    
        except Exception as e:
            logger.error(f"Video download failed: {e}")
            return None

    def get_media_info(self, url: str) -> Optional[dict]:
        """Get media information for video/reel content."""
        try:
            media_pk = self.client.media_pk_from_url(url)
            
            # Try different methods in order of preference
            # 1. Try the standard media_info first
            try:
                media_info = self.client.media_info(media_pk)
                return {
                    'title': media_info.caption_text or '',
                    'duration': getattr(media_info, 'video_duration', 0),
                    'user': media_info.user.username,
                    'pk': media_pk
                }
            except Exception as validation_error:
                logger.warning(f"Standard media_info failed (likely Pydantic validation): {validation_error}")
                
                # 2. Try GraphQL API directly
                try:
                    media_info = self.client.media_info_gql(media_pk)
                    return {
                        'title': media_info.caption_text or '',
                        'duration': getattr(media_info, 'video_duration', 0),
                        'user': media_info.user.username,
                        'pk': media_pk
                    }
                except Exception as gql_error:
                    logger.warning(f"GraphQL media_info failed: {gql_error}")
                    
                    # 3. Try mobile API directly
                    try:
                        media_info = self.client.media_info_v1(media_pk)
                        return {
                            'title': media_info.caption_text or '',
                            'duration': getattr(media_info, 'video_duration', 0),
                            'user': media_info.user.username,
                            'pk': media_pk
                        }
                    except Exception as v1_error:
                        logger.warning(f"Mobile API media_info failed: {v1_error}")
                        
                        # 4. Last resort: Use oEmbed for basic info
                        try:
                            oembed_info = self.client.media_oembed(url)
                            return {
                                'title': getattr(oembed_info, 'title', '') or '',
                                'duration': 0,
                                'user': getattr(oembed_info, 'author_name', '') or 'unknown',
                                'pk': media_pk
                            }
                        except Exception as oembed_error:
                            logger.warning(f"oEmbed fallback failed: {oembed_error}")
                            
                            # 5. Final fallback - minimal info for download attempt
                            logger.info("Using minimal fallback info")
                            return {
                                'title': '',
                                'duration': 0,
                                'user': 'unknown',
                                'pk': media_pk
                            }
                            
        except Exception as e:
            logger.error(f"Failed to get media info: {e}")
            return None 