"""Instagram authentication and cookie management utilities."""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Any, TypedDict, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, ProxySettings, Cookie
from playwright.async_api._generated import Playwright

from ..config.settings import settings
from .two_factor import TwoFactorAuth

logger = logging.getLogger(__name__)

class InstagramAuthError(Exception):
    """Raised when Instagram authentication fails."""
    pass

class YtDlpCookie(TypedDict):
    """Type definition for yt-dlp cookie format."""
    domain: str
    path: str
    name: str
    value: str
    secure: bool
    expires: int

def convert_playwright_cookie_to_ytdlp(cookie: Cookie) -> YtDlpCookie:
    """Convert a Playwright cookie to yt-dlp format."""
    domain = cookie.get('domain', '')
    # Ensure domain starts with a dot for non-specific subdomains
    if domain and not domain.startswith('.') and domain.count('.') >= 1:
        domain = '.' + domain

    return {
        'domain': domain,
        'path': cookie.get('path', '/'),
        'name': cookie.get('name', ''),
        'value': cookie.get('value', ''),
        'secure': cookie.get('secure', False),
        'expires': int(cookie.get('expires', time.time() + 31536000))  # Default to 1 year
    }

async def setup_browser_context(playwright: Playwright) -> tuple[Browser, BrowserContext]:
    """Setup browser and context with proper configuration."""
    browser_args = [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-infobars',
        '--window-position=0,0',
        '--ignore-certifcate-errors',
        '--ignore-certifcate-errors-spki-list',
        '--disable-notifications',
        '--disable-popup-blocking',
        '--disable-dev-shm-usage',  # Required for headless environments
        '--disable-gpu',            # Required for some headless environments
        '--no-first-run',
        '--no-service-autorun',
        '--password-store=basic'
    ]
    
    proxy: Optional[ProxySettings] = None
    if settings.PROXY_HOST and settings.PROXY_PORT:
        proxy = {
            'server': f'http://{settings.PROXY_HOST}:{settings.PROXY_PORT}'
        }
        if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
            proxy.update({
                'username': settings.PROXY_USERNAME,
                'password': settings.PROXY_PASSWORD
            })

    browser = await playwright.chromium.launch(
        headless=True,  # Always use headless mode for VPS
        args=browser_args
    )
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        proxy=proxy,
        java_script_enabled=True,
        locale='en-US',
        timezone_id='Europe/London',
        geolocation={'latitude': 51.5074, 'longitude': -0.1278},  # London coordinates
        permissions=['geolocation']
    )
    
    # Additional stealth setup
    await context.add_init_script("""
        // Overwrite the 'webdriver' property
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Overwrite the plugins length
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Overwrite the languages property
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        
        // Add webgl vendor
        Object.defineProperty(navigator, 'vendor', {
            get: () => 'Google Inc.'
        });
        
        // Add platform
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
    """)
    
    # Set default timeout
    context.set_default_timeout(60000)  # 60 seconds
    
    return browser, context

def format_cookie_for_yt_dlp(cookie: YtDlpCookie) -> str:
    """Format a cookie dictionary into Netscape format for yt-dlp."""
    return (
        f"{cookie['domain']}\tTRUE\t{cookie['path']}\t"
        f"{'TRUE' if cookie['secure'] else 'FALSE'}\t{cookie['expires']}\t"
        f"{cookie['name']}\t{cookie['value']}"
    )

def save_cookies(cookies: List[Cookie], output_file: Path) -> None:
    """Save cookies in Netscape format for yt-dlp."""
    try:
        with open(output_file, 'w', encoding='utf-8', newline='\n') as f:
            f.write("# Netscape HTTP Cookie File\n# https://curl.haxx.se/rfc/cookie_spec.html\n# This is a generated file!  Do not edit.\n\n")
            
            # Convert and filter Instagram-related cookies
            yt_dlp_cookies = [
                convert_playwright_cookie_to_ytdlp(cookie)
                for cookie in cookies
                if '.instagram.com' in cookie.get('domain', '')
            ]
            
            for cookie in yt_dlp_cookies:
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
    try:
        logger.info("Starting Instagram login process")
        logger.info(f"Using username: {settings.IG_USERNAME}")
        
        # Wait for the page to be fully loaded
        await page.wait_for_load_state('networkidle')
        logger.debug("Page fully loaded")
        
        # Wait for the login form with increased timeout
        logger.debug("Waiting for login form...")
        try:
            await page.wait_for_selector('input[name="username"]', timeout=60000)
            logger.debug("Login form found")
        except Exception as e:
            logger.error("Could not find login form, checking page content...")
            page_content = await page.content()
            if "challenge" in page_content.lower():
                raise InstagramAuthError("Instagram is requesting verification. Please log in manually first.")
            raise
        
        # Add random delays between actions
        await asyncio.sleep(2)
        
        # Fill username with human-like typing
        logger.debug("Filling username...")
        username_input = page.get_by_label("Username or email")
        await username_input.type(settings.IG_USERNAME, delay=100)  # Type with delay
        await asyncio.sleep(1.5)
        
        # Fill password with human-like typing
        logger.debug("Filling password...")
        password_input = page.get_by_label("Password")
        await password_input.type(settings.IG_PASSWORD, delay=100)  # Type with delay
        await asyncio.sleep(2)
        
        # Click login button
        logger.debug("Clicking login button...")
        login_button = page.get_by_role("button", name="Log in")
        await login_button.click(timeout=10000)
        logger.debug("Login button clicked")
        
        # Wait for successful login indicators with increased timeout
        logger.debug("Waiting for login confirmation...")
        try:
            await page.wait_for_selector('a[href="/direct/inbox/"]', timeout=60000)
            logger.info("Successfully logged in to Instagram")
            
        except Exception as e:
            page_content = await page.content()
            
            if "challenge" in page_content.lower():
                raise InstagramAuthError("Instagram is requesting additional verification. Please log in manually first.")
            
            if "suspicious" in page_content.lower():
                raise InstagramAuthError("Instagram detected suspicious activity. Please log in manually first.")
            
            # Check for error messages
            error_selectors = [
                'text="Sorry, your password was incorrect."',
                'text="The username you entered doesn\'t belong to an account."',
                'text="Please wait a few minutes before you try again."',
                '[data-testid="login-error-message"]',
                'text="Suspicious Login Attempt"',
                'text="We detected an unusual login attempt"'
            ]
            
            for selector in error_selectors:
                try:
                    error_text = await page.text_content(selector, timeout=5000)
                    if error_text:
                        logger.error(f"Login error: {error_text}")
                        raise InstagramAuthError(f"Login failed: {error_text}")
                except Exception:
                    continue
            
            # If no specific error found, raise the original error
            logger.error(f"Login verification failed: {e}")
            raise InstagramAuthError("Failed to verify successful login")
            
    except Exception as e:
        if not isinstance(e, InstagramAuthError):
            logger.error(f"Unexpected error during login: {str(e)}")
            raise InstagramAuthError(f"Login process failed: {str(e)}")
        raise

async def refresh_instagram_cookies(retry_count: int = 0) -> bool:
    """Refresh Instagram authentication cookies using Playwright."""
    try:
        logger.info("Starting Instagram cookie refresh process")
        async with async_playwright() as p:
            browser, context = await setup_browser_context(p)
            
            try:
                page = await context.new_page()
                logger.debug("Opening Instagram login page...")
                await page.goto('https://www.instagram.com/accounts/login/')
                
                # Take screenshot for debugging
                await page.screenshot(path='login_page.png')
                logger.debug("Login page screenshot saved as login_page.png")
                
                # Handle cookie consent if present
                await handle_cookie_consent(page)
                
                # Perform login
                await login_to_instagram(page)
                
                # Visit multiple Instagram pages to ensure all necessary cookies are set
                logger.debug("Visiting additional Instagram pages...")
                for url in [
                    'https://www.instagram.com/',
                    'https://www.instagram.com/direct/inbox/',
                    'https://www.instagram.com/explore/'
                ]:
                    await page.goto(url)
                    await page.wait_for_timeout(1000)
                    logger.debug(f"Visited {url}")
                
                # Get cookies for all Instagram domains
                logger.debug("Collecting cookies...")
                cookies = await context.cookies([
                    'https://www.instagram.com',
                    'https://instagram.com',
                    'https://i.instagram.com',
                    'https://graph.instagram.com'
                ])
                logger.info(f"Collected {len(cookies)} cookies")
                
                # Log cookie domains for debugging
                domains = set(cookie.get('domain', '') for cookie in cookies)
                logger.debug(f"Cookie domains: {domains}")
                
                # Save cookies for yt-dlp
                logger.debug(f"Saving cookies to {settings.COOKIES_FILE}")
                save_cookies(cookies, Path(settings.COOKIES_FILE))
                logger.info(f"Saved {len(cookies)} Instagram cookies to {settings.COOKIES_FILE}")
                
                # Take screenshot of logged-in state
                await page.screenshot(path='logged_in.png')
                logger.debug("Logged-in state screenshot saved as logged_in.png")
                
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

def refresh_instagram_cookies_sync(retry_count: int = 0) -> bool:
    """Synchronous wrapper for refresh_instagram_cookies."""
    try:
        return asyncio.run(refresh_instagram_cookies(retry_count))
    except Exception as e:
        logger.error(f"Failed to refresh cookies synchronously: {str(e)}")
        return False