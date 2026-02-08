"""Instagram client using instagrapi."""
import logging
import time
from pathlib import Path
from typing import Optional

import requests
from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ClientError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from ..config.settings import settings

logger = logging.getLogger(__name__)


class InstagramAuthError(Exception):
    """Raised when Instagram authentication/challenge errors are detected."""


class InstagramClient:
    """Instagram client wrapper using instagrapi."""

    def __init__(
        self,
        username: str,
        password: str,
        session_file: Optional[Path] = None,
        proxy: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.proxy = proxy
        self.totp_secret = totp_secret
        self.session_file = (
            session_file or settings.BASE_DIR / "sessions" / f"{username}.json"
        )
        self.client = Client()
        self._session_settings: Optional[dict] = None
        self._setup_proxy()

    def _redact_proxy(self, proxy: Optional[str]) -> str:
        """Return proxy value with credentials removed for logging."""
        if not proxy:
            return "none"
        if "@" not in proxy:
            return proxy
        scheme_sep = "://"
        if scheme_sep in proxy:
            scheme, remainder = proxy.split(scheme_sep, 1)
            if "@" in remainder:
                _, host_part = remainder.split("@", 1)
                return f"{scheme}://***@{host_part}"
        _, host_part = proxy.split("@", 1)
        return f"***@{host_part}"

    @staticmethod
    def _classify_instagram_error(error: Exception) -> str:
        """Classify Instagram-related failures for recovery decisions."""
        error_str = str(error).lower()
        if isinstance(error, ChallengeRequired) or "challenge_required" in error_str:
            return "auth_challenge"
        if isinstance(error, LoginRequired) or "login_required" in error_str:
            return "auth_challenge"
        if "401" in error_str or "403" in error_str or "unauthorized" in error_str:
            return "auth_challenge"
        if isinstance(error, PleaseWaitFewMinutes) or "please wait" in error_str:
            return "rate_limit"
        if "rate" in error_str and "limit" in error_str:
            return "rate_limit"
        return "download_failed"

    def _is_auth_error(self, error: Exception) -> bool:
        """Whether this error should trigger auth recovery/rotation."""
        return self._classify_instagram_error(error) == "auth_challenge"

    def _setup_proxy(self) -> None:
        """Configure proxy if available."""
        proxy_to_use = None

        if self.proxy:
            proxy_to_use = self.proxy
        elif settings.get_single_proxy():
            proxy_to_use = settings.get_single_proxy()

        if proxy_to_use:
            self.client.set_proxy(proxy_to_use)
            logger.info(
                "Proxy configured",
                extra={"proxy": self._redact_proxy(proxy_to_use)},
            )

    def login(self) -> bool:
        """Login to Instagram with session persistence."""
        try:
            session = self._load_session_into_client()
            if session:
                if self._is_session_valid():
                    logger.info("Session is valid")
                    return True
                logger.info("Stored session is no longer valid, refreshing login")

            logger.info(f"Logging in as {self.username}")
            success = self._perform_login()
            if success:
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
        """Download video using instagrapi with validation error handling."""
        try:
            # Extract media PK from URL
            media_pk = int(self.client.media_pk_from_url(url))
            logger.info(f"Extracted media PK: {media_pk}")
            
            # Download video directly
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # First, try yt-dlp as it's more reliable for reels
            video_path = self._download_with_ytdlp_first(url, media_pk, output_dir)
            if video_path:
                logger.info(
                    "Video downloaded via yt-dlp",
                    extra={
                        "username": self.username,
                        "proxy": self._redact_proxy(self.proxy),
                        "failure_class": "none",
                    },
                )
                return video_path
            
            try:
                # Try the standard video_download without folder first (like in the example)
                video_path = self.client.video_download(media_pk)
                if video_path and video_path.exists():
                    # Move to output directory
                    import shutil
                    final_path = output_dir / video_path.name
                    shutil.move(str(video_path), str(final_path))
                    logger.info(f"Video downloaded: {final_path}")
                    return final_path
            except Exception as download_error:
                failure_class = self._classify_instagram_error(download_error)
                
                # Check if this is a session expiration issue
                if self._is_auth_error(download_error):
                    logger.warning("Session expired during download, attempting re-login...")
                    if self._relogin():
                        # Retry download after successful re-login
                        try:
                            video_path = self.client.video_download(media_pk, folder=output_dir)
                            logger.info(f"Video downloaded after re-login: {video_path}")
                            return video_path
                        except Exception:
                            logger.warning("Download still failed after re-login, trying fallbacks...")
                    else:
                        raise InstagramAuthError(str(download_error)) from download_error
                
                logger.warning(
                    "Standard video download failed",
                    extra={
                        "username": self.username,
                        "proxy": self._redact_proxy(self.proxy),
                        "failure_class": failure_class,
                    },
                )
                
                # If standard download fails, use yt-dlp as fallback
                logger.warning("Standard download failed, using yt-dlp fallback...")
                video_path = self._download_with_ytdlp_first(url, media_pk, output_dir)
                if video_path:
                    logger.info(f"Video downloaded via yt-dlp fallback: {video_path}")
                    return video_path
                
                logger.error(f"All download methods failed")
                raise download_error  # Re-raise the original error
                    
        except InstagramAuthError:
            raise
        except Exception as e:
            logger.error(f"Video download failed: {e}")
            return None
    
    def _get_media_dict_raw(self, media_pk: int) -> Optional[dict]:
        """Get raw media dictionary from API, bypassing Pydantic validation."""
        try:
            endpoint = f"media/{media_pk}/info/"
            data = self.client.private_request(endpoint)
            
            if isinstance(data, dict) and data.get('message') == 'login_required':
                logger.warning("Session expired during raw media fetch, attempting re-login...")
                if self._relogin():
                    data = self.client.private_request(endpoint)
                else:
                    return None
            
            if isinstance(data, dict) and 'items' in data:
                items = data.get('items', [])
                if items:
                    return items[0]
            return None
        except Exception as e:
            logger.warning(f"Failed to get raw media dict: {e}")
            return None
    
    def _get_video_url_raw(self, media_pk: int) -> Optional[str]:
        """Get video URL from raw API data, bypassing Pydantic validation."""
        try:
            # Make direct API call to get raw media info using proper endpoint
            endpoint = f"media/{media_pk}/info/"
            data = self.client.private_request(endpoint)
            
            # Check if we got a login_required error
            if isinstance(data, dict) and data.get('message') == 'login_required':
                logger.warning("Session expired during raw video URL extraction, attempting re-login...")
                if self._relogin():
                    # Retry after successful re-login
                    data = self.client.private_request(endpoint)
                else:
                    logger.warning("Re-login failed, cannot get raw video URL")
                    return None
            
            # Debug: log the keys we get back
            logger.debug(f"Raw API response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            
            if isinstance(data, dict) and 'items' in data:
                # Navigate through the response to find video URL
                items = data.get('items', [])
                if items:
                    item = items[0]
                    logger.debug(f"Item keys: {list(item.keys())}")
                    
                    # Try different video URL fields
                    video_versions = item.get('video_versions', [])
                    if video_versions:
                        # Get the highest quality version (usually first)
                        video_url = video_versions[0].get('url')
                        logger.info(f"Found video URL in video_versions: {video_url[:100]}...")
                        return video_url
                    
                    # For clips/reels, try clips metadata
                    clips_metadata = item.get('clips_metadata', {})
                    if clips_metadata:
                        logger.debug(f"clips_metadata keys: {list(clips_metadata.keys())}")
                        clips_video_versions = clips_metadata.get('video_versions', [])
                        if clips_video_versions:
                            video_url = clips_video_versions[0].get('url')
                            logger.info(f"Found video URL in clips_metadata: {video_url[:100]}...")
                            return video_url
                    
                    # Fallback: try other video URL fields
                    if item.get('video_url'):
                        video_url = item.get('video_url')
                        logger.info(f"Found video URL in video_url field: {video_url[:100]}...")
                        return video_url
                        
                    # Debug: log all available keys in the item
                    logger.warning(f"Could not find video URL. Available item keys: {list(item.keys())}")
                    
                    # Additional debug: check if there are video-related fields
                    video_keys = [k for k in item.keys() if 'video' in k.lower()]
                    logger.debug(f"Video-related keys found: {video_keys}")
                    
                else:
                    logger.warning("No items found in API response")
            else:
                logger.warning(f"Unexpected API response format: {type(data)}")
                if isinstance(data, dict):
                    logger.debug(f"Response keys: {list(data.keys())}")
                
            return None
                
        except Exception as e:
            error_str = str(e).lower()
            if 'login_required' in error_str or '403' in error_str:
                logger.warning("Session expired in raw URL extraction, attempting re-login...")
                if self._relogin():
                    # Retry the whole method after re-login
                    try:
                        return self._get_video_url_raw(media_pk)
                    except Exception as retry_error:
                        logger.warning(f"Raw URL extraction still failed after re-login: {retry_error}")
                        return None
            
            logger.warning(f"Failed to get raw video URL: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
            return None

    def get_media_info(self, url: str) -> Optional[dict]:
        """Get media information for video/reel content."""
        try:
            media_pk = int(self.client.media_pk_from_url(url))
            fallback_info = {
                'title': '',
                'duration': 0,
                'user': 'unknown',
                'pk': media_pk
            }
            
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
                if self._is_auth_error(validation_error):
                    logger.warning("Session expired, attempting re-login...")
                    if self._relogin():
                        # Retry after successful re-login
                        try:
                            media_info = self.client.media_info(media_pk)
                            return {
                                'title': media_info.caption_text or '',
                                'duration': getattr(media_info, 'video_duration', 0),
                                'user': media_info.user.username,
                                'pk': media_pk
                            }
                        except Exception:
                            pass  # Continue to fallbacks
                    else:
                        raise InstagramAuthError(str(validation_error)) from validation_error
                    
                logger.warning(f"Standard media_info failed (likely Pydantic validation): {validation_error}")
            
            # 2. Try mobile API directly
            try:
                media_info = self.client.media_info_v1(media_pk)
                return {
                    'title': media_info.caption_text or '',
                    'duration': getattr(media_info, 'video_duration', 0),
                    'user': media_info.user.username,
                    'pk': media_pk
                }
            except Exception as v1_error:
                if self._is_auth_error(v1_error):
                    raise InstagramAuthError(str(v1_error)) from v1_error
                logger.warning(f"Mobile API media_info failed: {v1_error}")
            
            # 3. Last resort: Use oEmbed for basic info (with validation fix)
            try:
                oembed_data = self._get_oembed_safe(url)
                if oembed_data:
                    return {
                        'title': oembed_data.get('title', ''),
                        'duration': 0,
                        'user': oembed_data.get('author_name', 'unknown'),
                        'pk': media_pk
                    }
            except Exception as oembed_error:
                logger.warning(f"oEmbed fallback failed: {oembed_error}")

            # 4. Final fallback - minimal info for download attempt
            logger.info("Using minimal fallback info")
            return fallback_info
        except InstagramAuthError:
            raise
        except Exception as e:
            logger.error(f"Failed to get media info: {e}")
            return None
    
    def _relogin(self) -> bool:
        """Attempt to re-login when session expires."""
        try:
            logger.info("Attempting to re-login due to session expiration...")
            session = self._load_session_into_client()
            if not session and self._session_settings:
                self._apply_session_to_client(self._session_settings)

            success = self._perform_login()
            if success:
                logger.info("Re-login successful, session refreshed")
                return True

            logger.error("Re-login failed")
            return False

        except Exception as e:
            logger.error(f"Re-login failed: {e}")
            return False
    
    def _get_oembed_safe(self, url: str) -> Optional[dict]:
        """Get oEmbed data with safe handling of missing fields."""
        try:
            # Make direct API call to avoid Pydantic validation
            endpoint = f"oembed/?url={url}"
            data = self.client.private_request(endpoint)
            
            if isinstance(data, dict):
                # Return raw dictionary, letting caller handle missing fields
                return data
            else:
                logger.warning(f"Unexpected oEmbed response format: {type(data)}")
                return None
                
        except Exception as e:
            logger.warning(f"Safe oEmbed request failed: {e}")
            return None
    
    def _download_without_metadata(self, media_pk: int, output_dir: Path) -> Optional[Path]:
        """Try to download by constructing direct video URLs or using external tools."""
        try:
            # First, try constructing common Instagram video URL patterns
            # These are educated guesses based on Instagram's CDN structure
            possible_urls = [
                f"https://scontent.cdninstagram.com/v/t50.{media_pk}.mp4",
                f"https://scontent-ams4-1.cdninstagram.com/v/t50.{media_pk}.mp4",
                f"https://instagram.fams4-1.fna.fbcdn.net/v/t50.{media_pk}.mp4",
            ]
            
            for i, test_url in enumerate(possible_urls):
                try:
                    logger.info(f"Trying constructed URL {i+1}/{len(possible_urls)}: {test_url[:80]}...")
                    video_path = self._download_video_manually(test_url, media_pk, output_dir)
                    if video_path and video_path.exists() and video_path.stat().st_size > 1000:  # At least 1KB
                        return video_path
                except Exception:
                    continue
            
            # If constructed URLs don't work, try yt-dlp as external fallback
            logger.info("Trying external download with yt-dlp...")
            video_path = self._download_with_ytdlp(media_pk, output_dir)
            if video_path:
                return video_path
                    
            logger.warning("No download methods worked")
            return None
            
        except Exception as e:
            logger.warning(f"Download without metadata failed: {e}")
            return None
    
    def _download_with_ytdlp_first(self, url: str, media_pk: int, output_dir: Path) -> Optional[Path]:
        """Try downloading with yt-dlp using the original URL."""
        try:
            import subprocess
            
            logger.info(f"Trying yt-dlp download from original URL: {url}")
            
            # Use yt-dlp to download directly
            output_file = output_dir / f"video_{media_pk}.mp4"
            
            cmd = [
                "yt-dlp",
                "--no-warnings",
                "-o", str(output_file),
                "--no-playlist",
                "--format", "best[ext=mp4]/best",
                "--quiet",
                "--no-check-certificate",
                "--user-agent", self.client.user_agent,
                url
            ]
            
            # Add proxy if available
            if self.proxy:
                cmd.extend(["--proxy", self.proxy])
            
            # Add cookies if available
            if hasattr(self.client, 'cookie_jar') and self.client.cookie_jar:
                cookie_string = "; ".join([f"{k}={v}" for k, v in self.client.cookie_jar.items()])
                cmd.extend(["--add-header", f"Cookie: {cookie_string}"])
            
            # Run yt-dlp
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0 and output_file.exists() and output_file.stat().st_size > 1000:
                logger.info(f"yt-dlp successfully downloaded video: {output_file}")
                return output_file
            else:
                if result.stderr:
                    logger.warning(f"yt-dlp failed: {result.stderr}")
                if output_file.exists():
                    output_file.unlink()
                return None
                
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp timed out")
            return None
        except FileNotFoundError:
            logger.warning("yt-dlp not installed")
            return None
        except Exception as e:
            logger.warning(f"yt-dlp download failed: {e}")
            return None
    
    def _download_with_ytdlp(self, media_pk: int, output_dir: Path) -> Optional[Path]:
        """Try downloading with yt-dlp as a final fallback."""
        try:
            import subprocess
            import json
            
            # Construct Instagram URL from media PK
            # We need to reverse-engineer the shortcode from PK
            # This is a simplified approach - in reality, the conversion is more complex
            instagram_url = f"https://www.instagram.com/p/{self._pk_to_shortcode(media_pk)}/"
            
            logger.info(f"Trying yt-dlp download from: {instagram_url}")
            
            # Use yt-dlp to download
            output_template = str(output_dir / f"video_{media_pk}.%(ext)s")
            
            cmd = [
                "yt-dlp",
                "--no-warnings",
                "--extract-flat",
                "--print", "url",
                instagram_url
            ]
            
            # First, try to get the direct URL
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0 and result.stdout.strip():
                video_url = result.stdout.strip()
                # Ensure video_url is a string and safely slice it
                if isinstance(video_url, str) and video_url:
                    logger.info(f"yt-dlp found video URL: {video_url[:100]}...")
                    # Download the video manually using the URL
                    return self._download_video_manually(video_url, media_pk, output_dir)
                else:
                    logger.warning(f"Invalid video URL from yt-dlp: {type(video_url)}")
                    return None
            else:
                logger.warning(f"yt-dlp failed: {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp timed out")
            return None
        except ImportError:
            logger.warning("subprocess not available")
            return None
        except FileNotFoundError:
            logger.warning("yt-dlp not installed")
            return None
        except Exception as e:
            logger.warning(f"yt-dlp download failed: {e}")
            return None
    
    def _pk_to_shortcode(self, media_pk: int) -> str:
        """Convert media PK to Instagram shortcode (simplified version)."""
        # This is a simplified base64-like conversion
        # The actual Instagram algorithm is more complex
        import string
        
        # Ensure media_pk is an integer
        try:
            media_pk = int(media_pk)
        except (ValueError, TypeError):
            logger.warning(f"Invalid media_pk type: {type(media_pk)}, value: {media_pk}")
            return 'A'
        
        if media_pk <= 0:
            return 'A'
        
        alphabet = string.ascii_letters + string.digits + '-_'
        shortcode = ''
        
        while media_pk > 0:
            remainder = media_pk % 64
            shortcode = alphabet[remainder] + shortcode
            media_pk = media_pk // 64
            
        return shortcode or 'A'
    
    def _download_video_manually(self, video_url: str, media_pk: int, output_dir: Path) -> Optional[Path]:
        """Download video manually using requests, bypassing instagrapi."""
        try:
            import requests
            
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"video_{media_pk}.mp4"
            
            logger.info(f"Manually downloading video from: {video_url[:100]}...")
            
            # Use the same headers and session as the Instagram client
            headers = {
                'User-Agent': self.client.user_agent,
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Origin': 'https://www.instagram.com',
                'Referer': 'https://www.instagram.com/',
                'Sec-Fetch-Dest': 'video',
                'Sec-Fetch-Mode': 'no-cors',
                'Sec-Fetch-Site': 'same-origin',
            }
            
            # Add cookies from Instagram session
            if hasattr(self.client, 'cookie_jar'):
                headers['Cookie'] = '; '.join([f'{k}={v}' for k, v in self.client.cookie_jar.items()])
            
            # Use requests with proper proxy and headers
            response = requests.get(video_url, headers=headers, stream=True, timeout=30, 
                                  proxies={'http': self.proxy, 'https': self.proxy} if self.proxy else None)
            
            response.raise_for_status()
            
            # Check if we got video content
            content_type = response.headers.get('content-type', '')
            if not content_type.startswith('video/'):
                logger.warning(f"Unexpected content type: {content_type}")
            
            # Download the video
            with open(output_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # Verify the download
            if output_file.exists() and output_file.stat().st_size > 1000:  # At least 1KB
                logger.info(f"Successfully downloaded video: {output_file}")
                return output_file
            else:
                logger.warning("Downloaded file is too small or doesn't exist")
                if output_file.exists():
                    output_file.unlink()
                return None
                
        except Exception as e:
            logger.warning(f"Manual video download failed: {e}")
            if 'output_file' in locals() and output_file.exists():
                try:
                    output_file.unlink()
                except:
                    pass
            return None 

    def _load_session_into_client(self) -> Optional[dict]:
        """Load and apply stored session settings."""
        if not self.session_file.exists():
            return None

        try:
            session = self.client.load_settings(self.session_file)
        except Exception as exc:
            logger.warning(f"Failed to load session settings: {exc}")
            return None

        if not session:
            return None

        self._apply_session_to_client(session)
        self._session_settings = session
        return session

    def _apply_session_to_client(self, session: dict) -> None:
        """Apply stored session details to the current client instance."""
        try:
            self.client.set_settings(session)
        except Exception as exc:
            logger.warning(f"Failed to apply settings directly: {exc}")
            self.client.settings = session

        uuids = session.get("uuids")
        if uuids:
            try:
                self.client.set_uuids(uuids)
            except Exception as exc:
                logger.debug(f"Failed to set UUIDs via helper: {exc}")
                self.client.uuid = uuids.get("uuid")

        device_settings = session.get("device_settings")
        if device_settings:
            if hasattr(self.client, "set_device"):
                try:
                    self.client.set_device(device_settings)
                except Exception:
                    self.client.device_settings = device_settings
            else:
                self.client.device_settings = device_settings

        authorization = session.get("authorization_data")
        if authorization:
            self.client.authorization_data = authorization

    def _persist_session(self) -> None:
        """Persist the current client session to disk."""
        try:
            settings_data = self.client.get_settings()
            self._session_settings = settings_data
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            self.client.dump_settings(self.session_file)
        except Exception as exc:
            logger.warning(f"Failed to persist session settings: {exc}")

    def _perform_login(self) -> bool:
        """Execute login sequence and persist session."""
        verification_code = self._generate_two_factor_code()

        success = self.client.login(
            self.username,
            self.password,
            verification_code=verification_code,
        )

        if success:
            self._persist_session()
            return True

        return False

    def _is_session_valid(self) -> bool:
        """Check whether the currently loaded session is still valid."""
        try:
            self.client.get_timeline_feed()
            return True
        except LoginRequired:
            return False
        except PleaseWaitFewMinutes as exc:
            logger.error(f"Rate limited while validating session: {exc}")
            raise
        except ClientError as exc:
            logger.warning(f"Session validation returned client error: {exc}")
            return False
        except Exception as exc:
            logger.warning(f"Session validation failed: {exc}")
            return False

    def _generate_two_factor_code(self) -> Optional[str]:
        """Generate a TOTP code if a secret is configured."""
        if not self.totp_secret or not self.totp_secret.strip():
            return None

        try:
            import pyotp

            totp = pyotp.TOTP(self.totp_secret.strip())
            code = totp.now()
            logger.info("Using TOTP for 2FA")
            return code
        except Exception as exc:
            logger.warning(f"Failed to generate TOTP code: {exc}")
            return None
