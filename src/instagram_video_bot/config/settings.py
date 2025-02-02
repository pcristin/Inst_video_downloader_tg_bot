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
    
    # Bot settings
    BOT_TOKEN: str
    
    # Instagram credentials
    IG_USERNAME: str
    IG_PASSWORD: str
    
    # Two-factor authentication
    TOTP_SECRET: Optional[str] = None
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent.parent
    TEMP_DIR: Path = BASE_DIR / "temp"
    COOKIES_FILE: Path = BASE_DIR / "instagram_cookies.txt"
    
    # Video processing settings
    VIDEO_WIDTH: int = 320
    VIDEO_HEIGHT: int = 480
    VIDEO_BITRATE: str = "192k"
    VIDEO_CRF: str = "23"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.TEMP_DIR.mkdir(exist_ok=True)

# Create global settings instance
settings = Settings() 