"""Application settings and configuration management."""
import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings(BaseSettings):
    """Application settings."""
    
    # Development mode
    DEV_MODE: bool = False
    
    # Bot settings
    BOT_TOKEN: str
    
    # Instagram credentials
    IG_USERNAME: str
    IG_PASSWORD: str
    
    # Two-factor authentication
    TOTP_SECRET: Optional[str] = None
    
    # Proxy settings
    PROXY_HOST: Optional[str] = None
    PROXY_PORT: Optional[int] = None
    PROXY_USERNAME: Optional[str] = None
    PROXY_PASSWORD: Optional[str] = None
    
    # Paths - with Docker support
    BASE_DIR: Path = Path(__file__).parent.parent.parent.parent
    TEMP_DIR: Path = Path(os.getenv('TEMP_DIR', BASE_DIR / "temp"))
    COOKIES_FILE: Path = Path(os.getenv('COOKIES_FILE', BASE_DIR / "instagram_cookies.txt"))
    
    # Video processing settings
    VIDEO_WIDTH: int = 320
    VIDEO_HEIGHT: int = 480
    VIDEO_BITRATE: str = "192k"
    VIDEO_CRF: str = "23"
    
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
        # Create cookies file if it doesn't exist
        if not self.COOKIES_FILE.exists():
            self.COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.COOKIES_FILE.touch()

# Create global settings instance
settings = Settings() 