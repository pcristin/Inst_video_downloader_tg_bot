"""Health check utilities for Docker container monitoring."""
import sys
import logging
from pathlib import Path
from ..config.settings import settings

logger = logging.getLogger(__name__)

def check_health() -> bool:
    """
    Perform health checks for the application.
    
    Returns:
        bool: True if healthy, False otherwise
    """
    try:
        # Check if required directories exist
        if not settings.TEMP_DIR.exists():
            logger.error(f"Temp directory does not exist: {settings.TEMP_DIR}")
            return False
        
        # Check if we can write to temp directory
        test_file = settings.TEMP_DIR / ".health_check"
        try:
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            logger.error(f"Cannot write to temp directory: {e}")
            return False
        
        # Check if cookies file is accessible
        if not settings.COOKIES_FILE.parent.exists():
            logger.error(f"Cookies directory does not exist: {settings.COOKIES_FILE.parent}")
            return False
        
        # Check environment variables
        if not settings.BOT_TOKEN:
            logger.error("BOT_TOKEN is not set")
            return False
        
        if not settings.IG_USERNAME or not settings.IG_PASSWORD:
            logger.error("Instagram credentials are not set")
            return False
        
        logger.info("Health check passed")
        return True
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False

if __name__ == "__main__":
    # Can be run directly for Docker health check
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if check_health() else 1) 