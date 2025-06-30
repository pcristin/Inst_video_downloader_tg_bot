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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def warmup_account(username: str):
    """Warm up Instagram account by browsing like a human."""
    cookies_file = Path(f'cookies/{username}_cookies.txt')
    
    if not cookies_file.exists():
        logger.error(f"No cookies found for account {username} at {cookies_file}")
        logger.error("Make sure the account is imported and has valid cookies")
        return False
    
    # Get proxy settings from environment
    proxy_config = None
    proxy_host = os.getenv('PROXY_HOST')
    proxy_port = os.getenv('PROXY_PORT')
    proxy_username = os.getenv('PROXY_USERNAME')
    proxy_password = os.getenv('PROXY_PASSWORD')
    
    if proxy_host and proxy_port:
        proxy_config = {
            'server': f'http://{proxy_host}:{proxy_port}',
        }
        if proxy_username and proxy_password:
            proxy_config['username'] = proxy_username
            proxy_config['password'] = proxy_password
        logger.info(f"Using proxy: {proxy_host}:{proxy_port}")
    
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
            proxy=proxy_config
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
        logger.info(f"Loaded {len(cookies)} cookies for {username}")
        
        page = await context.new_page()
        
        try:
            logger.info(f"Starting warmup for account: {username}")
            
            # 1. Visit Instagram homepage
            logger.info("📱 Visiting Instagram homepage...")
            await page.goto('https://www.instagram.com/', wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(random.randint(4000, 7000))
            
            # Check if we're logged in
            try:
                await page.wait_for_selector('svg[aria-label="Home"]', timeout=5000)
                logger.info("✅ Successfully logged in")
            except:
                logger.warning("⚠️  May not be fully logged in, continuing anyway...")
            
            # 2. Scroll through feed naturally
            logger.info("📜 Scrolling through feed...")
            scroll_count = random.randint(3, 6)
            for i in range(scroll_count):
                scroll_distance = random.randint(400, 800)
                await page.evaluate(f'window.scrollBy(0, {scroll_distance})')
                await page.wait_for_timeout(random.randint(2500, 4500))
                logger.info(f"   Scroll {i+1}/{scroll_count}")
            
            # 3. Visit explore page
            logger.info("🔍 Visiting explore page...")
            await page.goto('https://www.instagram.com/explore/', wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(random.randint(4000, 6000))
            
            # Light scrolling on explore
            for _ in range(random.randint(2, 3)):
                await page.evaluate('window.scrollBy(0, 500)')
                await page.wait_for_timeout(random.randint(3000, 5000))
            
            # 4. Visit some popular profiles (less suspicious than random ones)
            popular_profiles = [
                'instagram',    # Official Instagram
                'natgeo',      # National Geographic
                'therock',     # The Rock
                'arianagrande', # Ariana Grande
                'selenagomez', # Selena Gomez
                'cristiano',   # Cristiano Ronaldo
            ]
            
            profiles_to_visit = random.sample(popular_profiles, random.randint(2, 3))
            
            for profile in profiles_to_visit:
                logger.info(f"👤 Visiting profile: {profile}")
                try:
                    await page.goto(f'https://www.instagram.com/{profile}/', wait_until='networkidle', timeout=30000)
                    await page.wait_for_timeout(random.randint(4000, 7000))
                    
                    # Light scrolling on profile
                    scroll_distance = random.randint(300, 600)
                    await page.evaluate(f'window.scrollBy(0, {scroll_distance})')
                    await page.wait_for_timeout(random.randint(3000, 5000))
                    
                except Exception as e:
                    logger.warning(f"Failed to visit {profile}: {e}")
                    continue
            
            # 5. Visit stories (if available)
            logger.info("📱 Checking for stories...")
            await page.goto('https://www.instagram.com/', wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(random.randint(3000, 5000))
            
            # 6. Final scroll through home feed
            logger.info("🏠 Final browse through home feed...")
            for _ in range(random.randint(2, 4)):
                await page.evaluate('window.scrollBy(0, window.innerHeight * 0.7)')
                await page.wait_for_timeout(random.randint(3000, 5000))
            
            logger.info(f"✅ Account warmup completed successfully for {username}!")
            logger.info("⏰ Account should appear more human-like now")
            logger.info("💡 Wait 30-60 minutes before using this account in the bot")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Warmup failed for {username}: {e}")
            return False
        finally:
            await browser.close()

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='Warm up Instagram account to avoid detection')
    parser.add_argument('username', help='Instagram account username to warm up')
    parser.add_argument('--delay', type=int, default=0, help='Additional delay in seconds before starting')
    
    args = parser.parse_args()
    
    print("🔥 Instagram Account Warmup")
    print("=" * 50)
    print(f"Account: {args.username}")
    
    if args.delay > 0:
        print(f"⏰ Waiting {args.delay} seconds before starting...")
        import time
        time.sleep(args.delay)
    
    print("\n🤖 Starting human-like browsing session...")
    print("This helps avoid detection when using the bot.\n")
    
    try:
        success = asyncio.run(warmup_account(args.username))
        if success:
            print(f"\n✅ Warmup completed for {args.username}!")
            print("⏰ Wait 30-60 minutes before using this account in the bot")
            print("💡 The account should now appear more human-like to Instagram")
        else:
            print(f"\n❌ Warmup failed for {args.username}!")
            print("Check the logs above for details")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️  Warmup cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 