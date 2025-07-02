"""Main entry point for the Instagram Video Downloader Bot."""
import logging
import sys
from pathlib import Path

from .config.settings import settings
from .services.telegram_bot import TelegramBot
from .utils.initialize_auth import initialize_auth_sync

def setup_logging() -> None:
    """Configure logging for the application."""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper()),
        format=log_format,
        stream=sys.stdout
    )
    
    # Set external loggers to WARNING level
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)

def check_environment() -> None:
    """Verify that all required environment variables and files exist."""
    missing_vars = []
    for var in ['BOT_TOKEN', 'IG_USERNAME', 'IG_PASSWORD']:
        if not getattr(settings, var):
            missing_vars.append(var)
    
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )

def main() -> None:
    """Application entry point."""
    try:
        # Setup logging
        setup_logging()
        logger = logging.getLogger(__name__)
        logger.info("Starting Instagram Video Downloader Bot")
        
        # Check environment
        check_environment()
        
        # Create required directories
        settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        
        # Check for account management
            from .utils.account_manager import get_account_manager
            
            manager = get_account_manager()
        if manager:
            logger.info("Using multi-account mode")
            status = manager.get_status()
            
            logger.info(f"Loaded {status['total_accounts']} accounts ({status['available_accounts']} available)")
            
            # Setup first available account
            if status['available_accounts'] > 0:
                if manager.rotate_account():
                    logger.info(f"Using account: {manager.current_account.username}")
                else:
                    logger.error("Failed to setup any account")
                    logger.error("Run: python3 manage_accounts.py setup")
                    sys.exit(1)
            else:
                logger.error("No available accounts!")
                logger.error("Run: python3 manage_accounts.py status")
                sys.exit(1)
        else:
            # Single account mode (legacy)
            logger.info("Using single account mode")
            
            # Check if cookies file exists
            if settings.COOKIES_FILE.exists():
                logger.info(f"Found cookies file: {settings.COOKIES_FILE}")
                logger.info("Bot will use existing cookies for Instagram authentication")
            else:
                logger.warning(f"No cookies file found at: {settings.COOKIES_FILE}")
                logger.warning("You need to import cookies before the bot can work")
                logger.warning("Run: python3 import_cookies.py")
        
        logger.info("Note: Automatic cookie refresh is disabled to prevent login loops")
        logger.info("If authentication fails, manually refresh cookies or rotate accounts")
        
        # Start the bot
        bot = TelegramBot()
        bot.run()
        
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Fatal error: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 