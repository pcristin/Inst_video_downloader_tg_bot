#!/usr/bin/env python3
"""Test script to verify the Pydantic validation error fix."""

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

async def test_validation_fix():
    """Test the validation error fix with the URLs that were failing."""
    
    # URLs that were failing in the logs
    test_urls = [
        "https://www.instagram.com/reel/DMpomAdxlUG/?igsh=MXR2bzNiZnNheWMxMw==",
        "https://www.instagram.com/reel/DMPlvudtHAP/?igsh=NGMxeTVrNHE4azho"
    ]
    
    try:
        # Create client
        client = InstagramClient(
            username=settings.IG_USERNAME, 
            password=settings.IG_PASSWORD,
            totp_secret=settings.TOTP_SECRET
        )
        
        # Test login
        logger.info("Testing Instagram login...")
        if not client.login():
            logger.error("❌ Login failed! Cannot test validation fix.")
            return False
            
        logger.info("✅ Login successful!")
        
        # Test each URL that was failing
        for i, url in enumerate(test_urls, 1):
            logger.info(f"\n--- Testing URL {i}/{len(test_urls)} ---")
            logger.info(f"URL: {url}")
            
            try:
                # Test media info extraction
                logger.info("Testing media info extraction...")
                media_info = client.get_media_info(url)
                
                if media_info:
                    logger.info(f"✅ Media info extracted successfully!")
                    logger.info(f"   Title: {media_info.get('title', 'No title')[:50]}...")
                    logger.info(f"   User: {media_info.get('user', 'Unknown')}")
                    logger.info(f"   Media Type: {media_info.get('media_type')} ({'Video' if media_info.get('is_video') else 'Photo'})")
                    logger.info(f"   Duration: {media_info.get('duration', 0)} seconds")
                    
                    # Test download (without actually downloading)
                    logger.info("Testing download readiness...")
                    media_pk = media_info.get('pk')
                    if media_pk:
                        logger.info(f"✅ Media PK available: {media_pk}")
                    else:
                        logger.warning("⚠️ No media PK available")
                    
                else:
                    logger.error(f"❌ Failed to extract media info for URL {i}")
                    
            except Exception as e:
                logger.error(f"❌ Test failed for URL {i}: {e}")
                continue
                
        logger.info(f"\n🎉 Test completed! Check logs above for results.")
        return True
        
    except Exception as e:
        logger.error(f"❌ Test setup failed: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_validation_fix()) 