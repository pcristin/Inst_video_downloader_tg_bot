"""Instagram authentication and cookie management utilities."""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, List

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright.async_api._generated import Playwright

from ..config.settings import settings
from .two_factor import TwoFactorAuth

logger = logging.getLogger(__name__)

class InstagramAuthError(Exception):
    """Raised when Instagram authentication fails."""
    pass

async def setup_browser_context(playwright: Playwright) -> tuple[Browser, BrowserContext]:
    """Setup browser and context with proper configuration."""
    browser_args = []
    
    if settings.PROXY_HOST and settings.PROXY_PORT:
        proxy = {
            'server': f'http://{settings.PROXY_HOST}:{settings.PROXY_PORT}'
        }
        if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
            proxy.update({
                'username': settings.PROXY_USERNAME,
                'password': settings.PROXY_PASSWORD
            })
    else:
        proxy = None

    browser = await playwright.chromium.launch(
        headless=True,
        args=browser_args
    )
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        proxy=proxy
    )
    
    # Disable WebDriver flag
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)
    
    return browser, context

def format_cookie_for_yt_dlp(cookie: Dict) -> str:
    """Format a cookie dictionary into Netscape format for yt-dlp."""
    return (
        f"{cookie['domain']}\tTRUE\t{cookie['path']}\t"
        f"{'TRUE' if cookie.get('secure', False) else 'FALSE'}\t{cookie.get('expires', int(time.time()) + 31536000)}\t"
        f"{cookie['name']}\t{cookie['value']}"
    )

def save_cookies(cookies: List[Dict], output_file: Path) -> None:
    """Save cookies in Netscape format for yt-dlp."""
    try:
        with open(output_file, 'w', encoding='utf-8', newline='\n') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                if 'expires' not in cookie:
                    cookie['expires'] = int(time.time()) + 3600 * 24 * 365
                f.write(format_cookie_for_yt_dlp(cookie) + '\n')
        logger.info(f"Cookies saved to {output_file}")
    except Exception as e:
        raise InstagramAuthError(f"Failed to save cookies: {str(e)}")

async def handle_cookie_consent(page: Page) -> None:
    """Handle cookie consent dialog if present."""
    try:
        consent_button = page.get_by_role("button", name="Allow all cookies")
        if await consent_button.is_visible(timeout=5000):
            await consent_button.click()
            await page.wait_for_timeout(1000)  # Wait for dialog to disappear
    except Exception as e:
        logger.debug(f"No cookie consent dialog found or already handled: {e}")

async def login_to_instagram(page: Page) -> None:
    """Handle the Instagram login process."""
    logger.info("Starting Instagram login process")
    
    # Wait for and fill username field
    username_input = page.get_by_label("Username or email")
    await username_input.fill(settings.IG_USERNAME)
    await page.wait_for_timeout(1000)
    
    # Wait for and fill password field
    password_input = page.get_by_label("Password")
    await password_input.fill(settings.IG_PASSWORD)
    await page.wait_for_timeout(1000)
    
    # Click login button
    login_button = page.get_by_role("button", name="Log in")
    await login_button.click()
    
    # Wait for successful login indicators
    try:
        await page.wait_for_selector('a[href="/direct/inbox/"]', timeout=10000)
        logger.info("Successfully logged in to Instagram")
    except Exception as e:
        logger.error(f"Login verification failed: {e}")
        raise InstagramAuthError("Failed to verify successful login")

async def refresh_instagram_cookies(retry_count: int = 0) -> bool:
    """Refresh Instagram authentication cookies using Playwright."""
    try:
        async with async_playwright() as p:
            browser, context = await setup_browser_context(p)
            
            try:
                page = await context.new_page()
                await page.goto('https://www.instagram.com/accounts/login/')
                
                # Handle cookie consent if present
                await handle_cookie_consent(page)
                
                # Perform login
                await login_to_instagram(page)
                
                # Get cookies after successful login
                cookies = await context.cookies()
                
                # Save cookies for yt-dlp
                save_cookies(cookies, Path('instagram_cookies.txt'))
                
                return True
                
            except Exception as e:
                if retry_count < 2:
                    logger.warning(f"Login attempt failed, retrying... Error: {str(e)}")
                    await asyncio.sleep(5)
                    return await refresh_instagram_cookies(retry_count + 1)
                else:
                    logger.error(f"Login failed after {retry_count + 1} attempts: {str(e)}")
                    raise InstagramAuthError(f"Failed to authenticate with Instagram: {str(e)}")
            
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logger.error(f"Failed to initialize Playwright: {str(e)}")
        raise InstagramAuthError(f"Failed to initialize browser: {str(e)}")

# For synchronous usage in other parts of the code
def refresh_instagram_cookies_sync(retry_count: int = 0) -> bool:
    """Synchronous wrapper for refresh_instagram_cookies."""
    return asyncio.run(refresh_instagram_cookies(retry_count))