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
    if domain and not domain.startswith('.') and domain.count('.') >= 1:
        domain = '.' + domain

    return {
        'domain': domain,
        'path': cookie.get('path', '/'),
        'name': cookie.get('name', ''),
        'value': cookie.get('value', ''),
        'secure': cookie.get('secure', False),
        'expires': int(cookie.get('expires', time.time() + 31536000))
    }

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
            await page.wait_for_timeout(1000)
    except Exception:
        pass

async def setup_browser_context(playwright: Playwright) -> tuple[Browser, BrowserContext]:
    """Setup browser and context with proper configuration."""
    browser_args = [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-infobars',
        '--window-position=0,0',
        '--disable-notifications',
        '--disable-popup-blocking',
        '--disable-dev-shm-usage',
        '--disable-gpu',
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
        headless=True,
        args=browser_args
    )
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        proxy=proxy,
        java_script_enabled=True,
        locale='en-US',
        timezone_id='America/Los_Angeles'
    )
    
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
    """)
    
    return browser, context

async def login_to_instagram(page: Page) -> None:
    """Handle the Instagram login process."""
    try:
        logger.info("Starting Instagram login process")
        await page.wait_for_load_state('networkidle')
        
        username_selectors = [
            'input[name="username"]',
            'input[aria-label="Phone number, username, or email"]',
            'input[aria-label="Username or email"]',
            '//input[@name="username"]',
            '//input[contains(@aria-label, "username")]'
        ]
        
        username_input = None
        for selector in username_selectors:
            try:
                if selector.startswith('//'):
                    element = await page.wait_for_selector(f"xpath={selector}", timeout=5000)
                else:
                    element = await page.wait_for_selector(selector, timeout=5000)
                if element:
                    username_input = element
                    break
            except Exception:
                continue
        
        if not username_input:
            raise InstagramAuthError("Could not find username input field")
        
        await username_input.evaluate('el => el.value = ""')
        await username_input.click()
        await page.keyboard.type(settings.IG_USERNAME, delay=100)
        
        password_selectors = [
            'input[name="password"]',
            'input[aria-label="Password"]',
            '//input[@name="password"]',
            '//input[@type="password"]'
        ]
        
        password_input = None
        for selector in password_selectors:
            try:
                if selector.startswith('//'):
                    element = await page.wait_for_selector(f"xpath={selector}", timeout=5000)
                else:
                    element = await page.wait_for_selector(selector, timeout=5000)
                if element:
                    password_input = element
                    break
            except Exception:
                continue
        
        if not password_input:
            raise InstagramAuthError("Could not find password input field")
        
        await password_input.evaluate('el => el.value = ""')
        await password_input.click()
        await page.keyboard.type(settings.IG_PASSWORD, delay=100)
        
        login_button_selectors = [
            'button[type="submit"]',
            'button:has-text("Log in")',
            'button:has-text("Log In")',
            '//button[@type="submit"]',
            '//button[contains(text(), "Log")]'
        ]
        
        login_button = None
        for selector in login_button_selectors:
            try:
                if selector.startswith('//'):
                    element = await page.wait_for_selector(f"xpath={selector}", timeout=5000)
                else:
                    element = await page.wait_for_selector(selector, timeout=5000)
                if element:
                    login_button = element
                    break
            except Exception:
                continue
        
        if not login_button:
            raise InstagramAuthError("Could not find login button")
        
        await login_button.click()
        
        # Wait a bit to see if 2FA is required
        await page.wait_for_timeout(3000)
        
        # Check for 2FA prompt
        two_fa_selectors = [
            'input[name="verificationCode"]',
            'input[aria-label="Security code"]',
            'input[placeholder*="code"]',
            '//input[@name="verificationCode"]',
            '//input[contains(@aria-label, "code")]'
        ]
        
        two_fa_input = None
        for selector in two_fa_selectors:
            try:
                if selector.startswith('//'):
                    element = await page.wait_for_selector(f"xpath={selector}", timeout=3000)
                else:
                    element = await page.wait_for_selector(selector, timeout=3000)
                if element:
                    two_fa_input = element
                    break
            except Exception:
                continue
        
        if two_fa_input and settings.TOTP_SECRET:
            logger.info("2FA required, generating code...")
            auth = TwoFactorAuth()
            code = auth.get_current_code()
            logger.info(f"Using 2FA code: {code}")
            
            await two_fa_input.click()
            await page.keyboard.type(code, delay=100)
            
            # Find and click confirm button
            confirm_selectors = [
                'button[type="button"]:has-text("Confirm")',
                'button:has-text("Confirm")',
                'button:has-text("Submit")',
                '//button[contains(text(), "Confirm")]',
                '//button[@type="button" and contains(text(), "Confirm")]'
            ]
            
            for selector in confirm_selectors:
                try:
                    if selector.startswith('//'):
                        button = await page.wait_for_selector(f"xpath={selector}", timeout=3000)
                    else:
                        button = await page.wait_for_selector(selector, timeout=3000)
                    if button:
                        await button.click()
                        break
                except Exception:
                    continue
            
            # Wait for navigation after 2FA
            await page.wait_for_timeout(3000)
        
        success_selectors = [
            'a[href="/direct/inbox/"]',
            'a[href="/explore/"]',
            'svg[aria-label="Home"]',
            '//a[@href="/direct/inbox/"]',
            '//a[contains(@href, "/explore")]'
        ]
        
        logged_in = False
        for selector in success_selectors:
            try:
                if selector.startswith('//'):
                    await page.wait_for_selector(f"xpath={selector}", timeout=10000)
                else:
                    await page.wait_for_selector(selector, timeout=10000)
                logged_in = True
                break
            except Exception:
                continue
        
        if logged_in:
            logger.info("Successfully logged in to Instagram")
        else:
            error_content = await page.content()
            if "challenge" in error_content.lower():
                raise InstagramAuthError("Instagram is requesting additional verification")
            elif "suspicious" in error_content.lower():
                raise InstagramAuthError("Instagram detected suspicious activity")
            else:
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
                await page.goto('https://www.instagram.com/accounts/login/')
                await handle_cookie_consent(page)
                await login_to_instagram(page)
                
                logger.debug("Visiting additional Instagram pages...")
                for url in [
                    'https://www.instagram.com/',
                    'https://www.instagram.com/direct/inbox/',
                    'https://www.instagram.com/explore/'
                ]:
                    await page.goto(url)
                    await page.wait_for_timeout(1000)
                
                cookies = await context.cookies([
                    'https://www.instagram.com',
                    'https://instagram.com',
                    'https://i.instagram.com',
                    'https://graph.instagram.com'
                ])
                logger.info(f"Collected {len(cookies)} cookies")
                
                save_cookies(cookies, Path(settings.COOKIES_FILE))
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