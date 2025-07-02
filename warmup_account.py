#!/usr/bin/env python3
"""Warm up Instagram account to avoid detection."""
import asyncio
import argparse
import random
import sys
import os
from pathlib import Path
from playwright.async_api import async_playwright
import logging

# Add the src directory to the path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.instagram_video_bot.utils.proxy_manager import get_proxy_for_account

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def warmup_account(username: str):
    """Warm up Instagram account by browsing like a human."""
    cookies_file = Path(f'cookies/{username}_cookies.txt')
    
    if not cookies_file.exists():
        logger.error(f"No cookies found for account {username} at {cookies_file}")
        logger.error("Make sure the account is imported and has valid cookies")
        return False
    
    # Get proxy for this specific account
    proxy_config = get_proxy_for_account(username)
    playwright_proxy = None
    
    if proxy_config:
        playwright_proxy = proxy_config.playwright_config
        logger.info(f"Using proxy for {username}: {proxy_config.host}:{proxy_config.port}")
    else:
        logger.warning(f"No proxy available for {username} - running without proxy")
    
    async with async_playwright() as p:
        browser_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-features=site-per-process',
            '--disable-dev-shm-usage',
            '--no-sandbox',  # Required for Docker
            '--disable-setuid-sandbox',  # Required for Docker
        ]
        
        browser = await p.chromium.launch(
            headless=True,
            args=browser_args,
            proxy=playwright_proxy
        )
        
        # Random user agent from realistic options
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
        ]
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=random.choice(user_agents),
            locale='en-US',
            timezone_id='America/New_York'
        )
        
        # Add anti-detection scripts
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Hide automation indicators
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)
            
        # Parse and add cookies
        cookies = []
        logger.info(f"Loading cookies for account: {username}")
        
        try:
            with open(cookies_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain, flag, path, secure, expires, name, value = parts[:7]
                        if 'instagram.com' in domain:
                            cookies.append({
                                'name': name,
                                'value': value,
                                'domain': domain.lstrip('.'),
                                'path': path,
                                'expires': int(expires) if expires != '0' and expires.isdigit() else None,
                                'httpOnly': False,
                                'secure': secure.upper() == 'TRUE'
                            })
            
            if cookies:
                await context.add_cookies(cookies)
                logger.info(f"Loaded {len(cookies)} cookies for {username}")
            else:
                logger.warning(f"No valid cookies found for {username}")
                return False
                
        except Exception as e:
            logger.error(f"Error loading cookies: {e}")
            return False
        
        page = await context.new_page()
        
        try:
            logger.info(f"Starting warmup for account: {username}")
            
            # 1. Visit Instagram homepage
            logger.info("📱 Visiting Instagram homepage...")
            await page.goto('https://www.instagram.com/', wait_until='networkidle', timeout=45000)
            
            # Random delay
            await asyncio.sleep(random.uniform(3, 7))
            
            # Check if we're logged in
            content = await page.content()
            if 'login' in content.lower() and 'password' in content.lower():
                logger.warning("⚠️  Appears to be logged out, continuing anyway...")
            elif 'logged_in":true' in content.lower():
                logger.info("✅ Successfully logged in")
            else:
                logger.warning("⚠️  May not be fully logged in, continuing anyway...")
            
            # 2. Scroll through feed
            logger.info("📜 Scrolling through feed...")
            for i in range(5):
                await asyncio.sleep(random.uniform(2, 4))
                await page.evaluate('window.scrollBy(0, window.innerHeight * 0.8)')
                logger.info(f"   Scroll {i+1}/5")
            
            # 3. Visit explore page
            logger.info("🔍 Visiting explore page...")
            await page.goto('https://www.instagram.com/explore/', wait_until='networkidle', timeout=45000)
            await asyncio.sleep(random.uniform(3, 6))
            
            # Light scrolling on explore
            for i in range(3):
                await asyncio.sleep(random.uniform(2, 4))
                await page.evaluate('window.scrollBy(0, window.innerHeight * 0.6)')
            
            # 4. Visit a random profile (if we can find usernames in the page)
            try:
                # Look for profile links
                profile_links = await page.query_selector_all('a[href*="/"][href*="/p/"]')
                if profile_links:
                    # Extract a username from a post link
                    for link in profile_links[:3]:
                        href = await link.get_attribute('href')
                        if href and '/p/' in href:
                            # Extract username from post URL like /user/p/post_id/
                            parts = href.strip('/').split('/')
                            if len(parts) >= 1 and parts[0] != 'p':
                                username_to_visit = parts[0]
                                logger.info(f"👤 Visiting profile: {username_to_visit}")
                                await page.goto(f'https://www.instagram.com/{username_to_visit}/', 
                                              wait_until='networkidle', timeout=45000)
                                await asyncio.sleep(random.uniform(4, 8))
                                break
            except Exception as e:
                logger.warning(f"Could not visit random profile: {e}")
            
            # 5. Return to homepage
            logger.info("🏠 Returning to homepage...")
            await page.goto('https://www.instagram.com/', wait_until='networkidle', timeout=45000)
            await asyncio.sleep(random.uniform(2, 5))
            
            logger.info(f"✅ Warmup completed successfully for {username}!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Warmup failed for {username}: {e}")
            return False
        finally:
            await browser.close()

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Warm up Instagram account')
    parser.add_argument('username', help='Username of the account to warm up')
    parser.add_argument('--timeout', type=int, default=45, 
                       help='Timeout in seconds for page loads (default: 45)')
    
    args = parser.parse_args()
    
    print("🔥 Instagram Account Warmup")
    print("=" * 50)
    print(f"Account: {args.username}")
    print()
    print("🤖 Starting human-like browsing session...")
    print("This helps avoid detection when using the bot.")
    print()
    
    try:
        success = asyncio.run(warmup_account(args.username))
        if success:
            print(f"\n✅ Warmup completed successfully for {args.username}!")
            print("\n💡 Wait at least 10-15 minutes before using the bot")
            print("   to let the warmup settle in.")
        else:
            print(f"\n❌ Warmup failed for {args.username}!")
            print("Check the logs above for details")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️  Warmup interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        print(f"\n❌ Warmup failed for {args.username}!")
        print("Check the logs above for details")
        sys.exit(1)

if __name__ == "__main__":
    main() 