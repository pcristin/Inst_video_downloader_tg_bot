#!/usr/bin/env python3
"""Warm up Instagram account to avoid detection."""
import asyncio
import random
import sys
from pathlib import Path
from playwright.async_api import async_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def warmup_account():
    """Warm up Instagram account by browsing like a human."""
    async with async_playwright() as p:
        browser_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-features=site-per-process',
            '--disable-dev-shm-usage'
        ]
        
        browser = await p.chromium.launch(
            headless=True,
            args=browser_args
        )
        
        # Random user agent
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        ]
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=random.choice(user_agents),
            locale='en-US',
            timezone_id='America/New_York'
        )
        
        # Add anti-detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        # Load cookies
        cookies_file = Path('cookies/instagram_cookies.txt')
        if not cookies_file.exists():
            logger.error("No cookies found! Run import_cookies.py first")
            return False
            
        # Parse and add cookies
        cookies = []
        with open(cookies_file, 'r') as f:
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    cookies.append({
                        'name': parts[5],
                        'value': parts[6],
                        'domain': parts[0],
                        'path': parts[2],
                        'secure': parts[3] == 'TRUE',
                        'httpOnly': False,
                        'sameSite': 'Lax'
                    })
        
        await context.add_cookies(cookies)
        
        page = await context.new_page()
        
        try:
            logger.info("Starting account warmup...")
            
            # 1. Visit Instagram homepage
            logger.info("Visiting Instagram homepage...")
            await page.goto('https://www.instagram.com/', wait_until='networkidle')
            await page.wait_for_timeout(random.randint(3000, 5000))
            
            # 2. Scroll a bit
            logger.info("Scrolling feed...")
            for _ in range(random.randint(2, 4)):
                await page.evaluate('window.scrollBy(0, window.innerHeight * 0.8)')
                await page.wait_for_timeout(random.randint(2000, 4000))
            
            # 3. Visit explore page
            logger.info("Visiting explore page...")
            await page.goto('https://www.instagram.com/explore/', wait_until='networkidle')
            await page.wait_for_timeout(random.randint(3000, 5000))
            
            # 4. Visit a few random profiles
            profiles = [
                'instagram',  # Official Instagram account
                'cristiano',  # Popular account
                'therock',    # Another popular account
            ]
            
            for profile in random.sample(profiles, 2):
                logger.info(f"Visiting profile: {profile}")
                await page.goto(f'https://www.instagram.com/{profile}/', wait_until='networkidle')
                await page.wait_for_timeout(random.randint(3000, 5000))
                
                # Scroll a bit
                await page.evaluate('window.scrollBy(0, 500)')
                await page.wait_for_timeout(random.randint(2000, 3000))
            
            # 5. Go back to home
            logger.info("Returning to home...")
            await page.goto('https://www.instagram.com/', wait_until='networkidle')
            await page.wait_for_timeout(random.randint(2000, 3000))
            
            logger.info("✅ Account warmup completed successfully!")
            logger.info("Wait at least 30 minutes before using the bot")
            
            return True
            
        except Exception as e:
            logger.error(f"Warmup failed: {e}")
            return False
        finally:
            await browser.close()

def main():
    """Main function."""
    print("🔥 Instagram Account Warmup")
    print("=" * 40)
    print("\nThis will browse Instagram like a human to warm up the account.")
    print("This helps avoid detection when using the bot.\n")
    
    try:
        success = asyncio.run(warmup_account())
        if success:
            print("\n✅ Warmup completed!")
            print("⏰ Wait at least 30 minutes before using the bot")
            print("💡 For best results, use a USA proxy matching the account location")
        else:
            print("\n❌ Warmup failed!")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️  Warmup cancelled")

if __name__ == "__main__":
    main() 