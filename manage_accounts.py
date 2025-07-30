#!/usr/bin/env python3
"""Account management script for Instagram video downloader bot."""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from instagram_video_bot.utils.account_manager import get_account_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def status_command():
    """Show account status."""
    manager = get_account_manager()
    if not manager:
        logger.error("No accounts found. Make sure accounts.txt exists.")
        return
    
    print(manager.get_detailed_status())

def setup_command():
    """Setup all accounts (login and create sessions)."""
    manager = get_account_manager()
    if not manager:
        logger.error("No accounts found. Make sure accounts.txt exists.")
        return
    
    available = manager.get_available_accounts()
    logger.info(f"Attempting to setup {len(available)} available accounts...")
    
    success_count = 0
    for account in available:
        logger.info(f"Setting up account: {account.username}")
        if manager.setup_account(account):
            success_count += 1
            logger.info(f"✅ Successfully setup: {account.username}")
        else:
            logger.error(f"❌ Failed to setup: {account.username}")
    
    logger.info(f"Setup complete: {success_count}/{len(available)} accounts successful")
    print("\nFinal status:")
    print(manager.get_detailed_status())

def rotate_command():
    """Rotate to next account."""
    manager = get_account_manager()
    if not manager:
        logger.error("No accounts found. Make sure accounts.txt exists.")
        return
    
    logger.info("Rotating to next available account...")
    if manager.rotate_account():
        if manager.current_account:
            logger.info(f"✅ Now using account: {manager.current_account.username}")
        else:
            logger.warning("⚠️ Rotation successful but no current account set")
    else:
        logger.error("❌ Failed to rotate to any account")
    
    print("\nCurrent status:")
    print(manager.get_detailed_status())

def reset_command():
    """Reset banned accounts."""
    manager = get_account_manager()
    if not manager:
        logger.error("No accounts found. Make sure accounts.txt exists.")
        return
    
    logger.info("Resetting all banned accounts...")
    manager.reset_banned_accounts()
    logger.info("✅ All banned accounts have been reset")
    
    print("\nStatus after reset:")
    print(manager.get_detailed_status())

def reset_old_command():
    """Reset accounts banned for more than specified hours."""
    parser = argparse.ArgumentParser(description="Reset old banned accounts")
    parser.add_argument("--hours", type=int, default=24, help="Reset accounts banned for more than this many hours (default: 24)")
    
    # Parse just the --hours argument from remaining argv
    remaining_args = [arg for arg in sys.argv[2:] if arg.startswith('--hours') or (sys.argv[sys.argv.index(arg)-1] == '--hours' if '--hours' in sys.argv else False)]
    if '--hours' in sys.argv:
        try:
            hours_index = sys.argv.index('--hours')
            if hours_index + 1 < len(sys.argv):
                hours = int(sys.argv[hours_index + 1])
            else:
                hours = 24
        except (ValueError, IndexError):
            hours = 24
    else:
        hours = 24
    
    manager = get_account_manager()
    if not manager:
        logger.error("No accounts found. Make sure accounts.txt exists.")
        return
    
    logger.info(f"Resetting accounts banned for more than {hours} hours...")
    manager.reset_old_banned_accounts(hours=hours)
    
    print("\nStatus after reset:")
    print(manager.get_detailed_status())

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Manage Instagram bot accounts")
    parser.add_argument(
        "command",
        choices=["status", "setup", "rotate", "reset", "reset-old"],
        help="Command to execute"
    )
    parser.add_argument("--hours", type=int, default=24, help="Hours for reset-old command (default: 24)")
    
    args = parser.parse_args()
    
    try:
        if args.command == "status":
            status_command()
        elif args.command == "setup":
            setup_command()
        elif args.command == "rotate":
            rotate_command()
        elif args.command == "reset":
            reset_command()
        elif args.command == "reset-old":
            reset_old_command()
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 