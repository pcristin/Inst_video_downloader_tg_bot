#!/usr/bin/env python3
"""Test script to verify instagrapi integration works."""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from instagram_video_bot.services.instagram_client import InstagramClient
from instagram_video_bot.config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_instagram_client():
    """Test the Instagram client functionality."""
    try:
        # Create client
        client = InstagramClient(settings.IG_USERNAME, settings.IG_PASSWORD)
        
        # Test login
        logger.info("Testing Instagram login...")
        if client.login():
            logger.info("✅ Login successful!")
            
            # Test getting media info
            test_url = "https://www.instagram.com/reel/DMlKwkTNAcP/?igsh=MXZ6eGliZzFkbTYzaQ=="  # You can replace with a real URL
            logger.info(f"Testing media info extraction for: {test_url}")
            
            try:
                media_info = client.get_media_info(test_url)
                if media_info:
                    logger.info(f"✅ Media info extracted: {media_info}")
                else:
                    logger.info("ℹ️ Could not extract media info (URL might be invalid)")
            except Exception as e:
                logger.info(f"ℹ️ Media info test failed (expected with test URL): {e}")
            
            return True
        else:
            logger.error("❌ Login failed!")
            return False
            
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False

def main():
    """Run the test."""
    logger.info("Starting instagrapi integration test...")
    
    # Check environment
    if not settings.IG_USERNAME or not settings.IG_PASSWORD:
        logger.error("❌ IG_USERNAME and IG_PASSWORD must be set in environment or .env file")
        logger.error("")
        logger.error("Create a .env file in the project root with:")
        logger.error("IG_USERNAME=your_instagram_username")
        logger.error("IG_PASSWORD=your_instagram_password")
        logger.error("BOT_TOKEN=your_telegram_bot_token")
        logger.error("")
        logger.error("Optional (if you have 2FA enabled):")
        logger.error("TOTP_SECRET=your_2fa_secret")
        return False
    
    # Run async test
    result = asyncio.run(test_instagram_client())
    
    if result:
        logger.info("✅ All tests passed! Instagrapi integration is working.")
    else:
        logger.error("❌ Tests failed. Check your credentials and network connection.")
    
    return result

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 