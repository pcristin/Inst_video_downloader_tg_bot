"""Two-factor authentication utilities using PyOTP."""
import logging
import qrcode.main
from pathlib import Path
import pyotp
from ..config.settings import settings

logger = logging.getLogger(__name__)

class TwoFactorAuth:
    """Handles 2FA operations using TOTP."""
    
    def __init__(self):
        """Initialize 2FA with a secret key."""
        self.secret = settings.TOTP_SECRET or pyotp.random_base32()
        self.totp = pyotp.TOTP(self.secret)
    
    def generate_qr(self, save_path: Path) -> None:
        """
        Generate and save QR code for Google Authenticator.
        
        Args:
            save_path: Path where to save the QR code image
        """
        provisioning_uri = self.totp.provisioning_uri(
            name=settings.IG_USERNAME,
            issuer_name="Instagram Video Bot"
        )
        
        qr = qrcode.main.QRCode(version=1, box_size=10, border=5)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(save_path)
        logger.info(f"QR code saved to {save_path}")
        
        # Print the secret for manual entry if needed
        logger.info(f"Manual entry secret: {self.secret}")
    
    def verify_code(self, code: str) -> bool:
        """
        Verify a TOTP code.
        
        Args:
            code: The code to verify
            
        Returns:
            bool: True if code is valid, False otherwise
        """
        try:
            return self.totp.verify(code)
        except Exception as e:
            logger.error(f"Error verifying 2FA code: {str(e)}")
            return False
    
    def get_current_code(self) -> str:
        """Get current TOTP code."""
        return self.totp.now() 