"""Test TOTP code generation."""
import logging
from .utils.two_factor import TwoFactorAuth
from .config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_totp():
    """Test TOTP code generation with existing secret."""
    if not settings.TOTP_SECRET:
        logger.error("No TOTP_SECRET found in environment variables")
        return
    
    logger.info(f"Using TOTP_SECRET: {settings.TOTP_SECRET}")
    
    auth = TwoFactorAuth()
    code = auth.get_current_code()
    
    logger.info(f"Current TOTP code: {code}")
    logger.info("This code changes every 30 seconds")
    logger.info("Use this code when Instagram asks for 2FA verification")

if __name__ == "__main__":
    test_totp() 