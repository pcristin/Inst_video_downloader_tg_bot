"""Main entry point for the Instagram Video Downloader Bot."""
import logging
import sys
from pathlib import Path

from .config.settings import settings
from .services.telegram_bot import TelegramBot

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
        
        # Start the bot
        bot = TelegramBot()
        bot.run()
        
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Fatal error: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 