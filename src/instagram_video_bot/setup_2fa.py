"""Script to set up two-factor authentication."""
import logging
from pathlib import Path

from .utils.two_factor import TwoFactorAuth
from .config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_2fa():
    """Set up two-factor authentication and generate QR code."""
    try:
        # Initialize 2FA
        auth = TwoFactorAuth()
        
        # Generate and save QR code
        qr_path = settings.BASE_DIR / "2fa_qr.png"
        auth.generate_qr(qr_path)
        
        logger.info(
            "\n"
            "Two-factor authentication setup:\n"
            "1. Scan the QR code in 2fa_qr.png with Google Authenticator\n"
            "2. Add the following to your .env file:\n"
            f"TOTP_SECRET={auth.secret}\n"
            "3. Delete the QR code image after setup\n"
        )
        
    except Exception as e:
        logger.error(f"Failed to set up 2FA: {str(e)}")

if __name__ == "__main__":
    setup_2fa() 