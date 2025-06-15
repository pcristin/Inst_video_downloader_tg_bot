"""Initialize Instagram authentication on startup."""
import asyncio
import logging
from pathlib import Path
from ..config.settings import settings
from .instagram_auth import refresh_instagram_cookies

logger = logging.getLogger(__name__)

async def initialize_authentication():
    """Initialize Instagram authentication if cookies don't exist or are invalid."""
    cookies_file = Path(settings.COOKIES_FILE)
    
    # Check if cookies file exists and has content
    if cookies_file.exists() and cookies_file.stat().st_size > 100:
        # Check if cookies contain actual Instagram cookies
        try:
            with open(cookies_file, 'r') as f:
                content = f.read()
                if '.instagram.com' in content:
                    logger.info("Valid Instagram cookies found, skipping authentication")
                    return True
        except Exception as e:
            logger.warning(f"Error reading cookies file: {e}")
    
    logger.info("No valid Instagram cookies found, authenticating...")
    try:
        success = await refresh_instagram_cookies()
        if success:
            logger.info("Instagram authentication successful")
        else:
            logger.error("Instagram authentication failed")
        return success
    except Exception as e:
        logger.error(f"Error during Instagram authentication: {e}")
        return False

def initialize_auth_sync():
    """Synchronous wrapper for initialize_authentication."""
    return asyncio.run(initialize_authentication())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = initialize_auth_sync()
    exit(0 if success else 1) 