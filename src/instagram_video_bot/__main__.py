# src/instagram_video_bot/__main__.py
"""Main entry point for the Instagram Video Downloader Bot."""

import logging
import sys
from pathlib import Path

from .config.settings import settings
from .services.telegram_bot import TelegramBot

def setup_logging() -> None:
    """Configure logging for the application."""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper()),
        format=log_format,
        stream=sys.stdout,
    )

    # Set external loggers to WARNING level
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("instagrapi").setLevel(logging.INFO)  # Add instagrapi logging

def check_environment() -> None:
    """Verify that all required environment variables exist."""
    missing_vars = []
    
    # Always require BOT_TOKEN
    if not getattr(settings, "BOT_TOKEN"):
        missing_vars.append("BOT_TOKEN")
    
    # Check if accounts.txt exists for multi-account mode
    accounts_file = Path("/app/accounts.txt")
    if not accounts_file.exists():
        # Single account mode - require IG_USERNAME and IG_PASSWORD
        for var in ["IG_USERNAME", "IG_PASSWORD"]:
            if not getattr(settings, var):
                missing_vars.append(var)

    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )

def main() -> None:
    """Application entry point."""
    try:
        setup_logging()
        logger = logging.getLogger(__name__)
        logger.info("Starting Instagram Video Downloader Bot with instagrapi")

        check_environment()
        settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)

        # Check for account management (keep this logic)
        from .utils.account_manager import get_account_manager

        manager = get_account_manager()
        if manager:
            logger.info("Using multi-account mode")
            status = manager.get_status()
            logger.info(f"Loaded {status['total_accounts']} accounts ({status['available_accounts']} available)")

            if status["available_accounts"] > 0:
                if manager.rotate_account():
                    if manager.current_account:
                        logger.info(f"Using account: {manager.current_account.username}")
                else:
                    logger.error("Failed to setup any account")
                    sys.exit(1)
            else:
                logger.error("No available accounts!")
                sys.exit(1)
        else:
            logger.info("Using single account mode")

        # Start the bot
        bot = TelegramBot()
        bot.run()

    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Fatal error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()