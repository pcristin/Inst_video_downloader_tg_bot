"""Health check utilities for Docker container monitoring."""
import sys
import logging

from ..config.settings import settings

logger = logging.getLogger(__name__)

def check_health() -> bool:
    """
    Perform health checks for the application.

    Returns:
        bool: True if healthy, False otherwise
    """
    try:
        if not settings.TEMP_DIR.exists():
            logger.error(f"Temp directory does not exist: {settings.TEMP_DIR}")
            return False

        test_file = settings.TEMP_DIR / ".health_check"
        try:
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            logger.error(f"Cannot write to temp directory: {e}")
            return False

        sessions_dir = settings.BASE_DIR / "sessions"
        if not sessions_dir.exists():
            logger.error(f"Sessions directory does not exist: {sessions_dir}")
            return False

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
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if check_health() else 1)
