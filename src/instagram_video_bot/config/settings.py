# src/instagram_video_bot/config/settings.py
"""Application settings and configuration management."""
import logging
import os
from pathlib import Path
from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    """Application settings."""
    
    # Development mode
    DEV_MODE: bool = False
    
    # Bot settings
    BOT_TOKEN: str = ""
    
    # Instagram credentials
    IG_USERNAME: str = ""
    IG_PASSWORD: str = ""
    
    # Two-factor authentication
    TOTP_SECRET: Optional[str] = None
    
    # Proxy settings (single proxy for backward compatibility)
    PROXY_HOST: Optional[str] = None
    PROXY_PORT: Optional[int] = None
    PROXY_USERNAME: Optional[str] = None
    PROXY_PASSWORD: Optional[str] = None
    
    # Multiple proxy support (format: proxy1,proxy2,proxy3...)
    # Each proxy format: user:pass@host:port or host:port
    PROXIES: Optional[str] = None
    
    # Paths - simplified
    BASE_DIR: Path = Path(__file__).parent.parent.parent.parent
    TEMP_DIR: Path = Path(os.getenv('TEMP_DIR', BASE_DIR / "temp"))
    
    # Note: No longer need COOKIES_FILE - instagrapi uses session files
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Docker-specific settings
    RUNNING_IN_DOCKER: bool = os.path.exists('/.dockerenv')
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure directories exist
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        # Create sessions directory
        (self.BASE_DIR / "sessions").mkdir(parents=True, exist_ok=True)
    
    def get_proxy_list(self) -> List[str]:
        """Get list of proxies from PROXIES setting."""
        if not self.PROXIES:
            return []

        proxies: List[str] = []
        for raw_proxy in self.PROXIES.split(','):
            raw_proxy = raw_proxy.strip()
            if not raw_proxy:
                continue

            normalized = self._normalize_proxy(raw_proxy)
            if normalized:
                proxies.append(normalized)
            else:
                logging.getLogger(__name__).warning(
                    "Skipping invalid proxy definition: %s", raw_proxy
                )
        return proxies
    
    def get_single_proxy(self) -> Optional[str]:
        """Get single proxy from old-style settings (backward compatibility)."""
        if self.PROXY_HOST and self.PROXY_PORT:
            if self.PROXY_USERNAME and self.PROXY_PASSWORD:
                return f'http://{self.PROXY_USERNAME}:{self.PROXY_PASSWORD}@{self.PROXY_HOST}:{self.PROXY_PORT}'
            else:
                return f'http://{self.PROXY_HOST}:{self.PROXY_PORT}'
        return None

    @staticmethod
    def _normalize_proxy(proxy: str) -> Optional[str]:
        """Normalize various proxy formats into a URL with credentials."""
        proxy = proxy.strip()
        if not proxy:
            return None

        scheme = "http"
        remainder = proxy

        if "://" in proxy:
            scheme, remainder = proxy.split("://", 1)
            scheme = scheme or "http"

        if "@" in remainder:
            # Already contains credentials separator
            return f"{scheme}://{remainder}"

        parts = remainder.split(":")
        if len(parts) == 2:
            host, port = parts
            return f"{scheme}://{host}:{port}"

        if len(parts) == 4:
            host, port, username, password = parts
            return f"{scheme}://{username}:{password}@{host}:{port}"

        return None

settings = Settings()
