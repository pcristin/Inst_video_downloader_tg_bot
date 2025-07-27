# src/instagram_video_bot/config/settings.py
"""Application settings and configuration management."""
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
    # Each proxy format: http://user:pass@host:port or http://host:port
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
        return [proxy.strip() for proxy in self.PROXIES.split(',') if proxy.strip()]
    
    def get_single_proxy(self) -> Optional[str]:
        """Get single proxy from old-style settings (backward compatibility)."""
        if self.PROXY_HOST and self.PROXY_PORT:
            if self.PROXY_USERNAME and self.PROXY_PASSWORD:
                return f'http://{self.PROXY_USERNAME}:{self.PROXY_PASSWORD}@{self.PROXY_HOST}:{self.PROXY_PORT}'
            else:
                return f'http://{self.PROXY_HOST}:{self.PROXY_PORT}'
        return None

settings = Settings()