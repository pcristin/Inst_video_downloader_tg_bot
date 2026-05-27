# src/instagram_video_bot/config/settings.py
"""Application settings and configuration management."""
import logging
import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    """Application settings."""
    
    # Development mode
    DEV_MODE: bool = False
    
    # Bot settings
    BOT_TOKEN: str = ""
    BOT_OWNER_USER_ID: Optional[int] = None
    ACCOUNT_FAILURE_THRESHOLD: int = 2
    ACCOUNT_LOW_WATERMARK: int = 3
    ACCOUNT_ALERT_COOLDOWN_SECONDS: int = 60 * 60
    
    # Instagram credentials
    IG_USERNAME: str = ""
    IG_PASSWORD: str = ""
    
    # Two-factor authentication
    TOTP_SECRET: Optional[str] = None

    # Fast Instagram extraction (primary path before authenticated fallback)
    IG_FAST_METHOD_ENABLED: bool = True
    IG_FAST_TIMEOUT_CONNECT: int = 10
    IG_FAST_TIMEOUT_READ: int = 45
    IG_FAST_TOTAL_BUDGET_SECONDS: float = 10.0
    IG_FAST_METADATA_TIMEOUT_CONNECT_SECONDS: float = 5.0
    IG_FAST_METADATA_TIMEOUT_READ_SECONDS: float = 8.0
    IG_FALLBACK_YTDLP_TIMEOUT_SECONDS: float = 15.0
    IG_FAST_MIN_DELAY_BETWEEN_DOWNLOADS: float = 0.5
    IG_FAST_RANDOM_DELAY_MIN_SECONDS: float = 0.0
    IG_FAST_RANDOM_DELAY_MAX_SECONDS: float = 0.0
    IG_FAST_MAX_MEDIA_DOWNLOAD_WORKERS: int = 4
    
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
    CACHE_DIR: Path = Path(os.getenv('CACHE_DIR', TEMP_DIR / "result_cache"))
    STATE_DB_PATH: Path = Path(os.getenv('STATE_DB_PATH', TEMP_DIR / "bot_state.sqlite3"))
    
    # Note: No longer need COOKIES_FILE - instagrapi uses session files
    
    # Logging
    LOG_LEVEL: str = "INFO"

    # Feature flags
    QUEUE_MANAGER_ENABLED: bool = True
    RESULT_CACHE_ENABLED: bool = True
    GROUP_STATS_ENABLED: bool = True
    YOUTUBE_SHORTS_ENABLED: bool = True
    DUPLICATE_SUPPRESSION_ENABLED: bool = True
    INLINE_MODE_ENABLED: bool = True
    INLINE_SUBSCRIPTION_REQUIRED: bool = True
    INLINE_SUBSCRIPTION_STARS: int = 1
    INLINE_SUBSCRIPTION_PERIOD_SECONDS: int = 30 * 24 * 60 * 60
    INLINE_ONE_TIME_ENABLED: bool = False
    INLINE_ONE_TIME_STARS: int = 1
    INLINE_SESSION_TTL_SECONDS: int = 15 * 60
    INLINE_STORAGE_CHAT_ID: Optional[int] = None

    # Concurrency and caching
    GLOBAL_MAX_CONCURRENT_JOBS: int = 3
    CHAT_MAX_CONCURRENT_JOBS: int = 2
    USER_MAX_ACTIVE_JOBS: int = 1
    INSTAGRAM_MAX_CONCURRENT_JOBS: int = 2
    TWITTER_MAX_CONCURRENT_JOBS: int = 3
    YOUTUBE_SHORTS_MAX_CONCURRENT_JOBS: int = 3
    INSTAGRAM_PROVIDER_TIMEOUT_SECONDS: float = 180.0
    INSTAGRAM_DETACHED_WORKER_LEASE_SECONDS: float = 300.0
    PROVIDER_TRANSIENT_RETRY_ATTEMPTS: int = 2
    PROVIDER_RETRY_BACKOFF_SECONDS: float = 0.8
    INSTAGRAM_ACCOUNT_LEASE_WAIT_SECONDS: float = 20.0
    RECENT_RESULT_TTL_SECONDS: int = 60 * 60 * 4
    MAX_LINKS_PER_MESSAGE: int = 5
    TELEGRAM_CONCURRENT_UPDATES: int = 4
    TELEGRAM_CONNECTION_POOL_SIZE: int = 16
    TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS: float = 60.0
    
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
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Create sessions directory
        (self.BASE_DIR / "sessions").mkdir(parents=True, exist_ok=True)
        self.STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
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
