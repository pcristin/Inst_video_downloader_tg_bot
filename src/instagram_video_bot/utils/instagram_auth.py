"""Instagram authentication and cookie management utilities."""
import asyncio
import json
import logging
import time
import random
import math
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
            # Add random delay
            await page.wait_for_timeout(random.randint(1000, 2000))
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
        '--password-store=basic',
        '--disable-blink-features=AutomationControlled',
        '--disable-extensions',
        '--disable-plugins',
        '--disable-images',
        '--disable-javascript-harmony-shipping',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-features=TranslateUI',
        '--disable-ipc-flooding-protection',
        '--disable-hang-monitor',
        '--disable-client-side-phishing-detection',
        '--disable-component-update',
        '--no-default-browser-check',
        '--disable-default-apps',
        '--disable-domain-reliability',
        '--disable-background-networking',
        '--disable-breakpad',
        '--disable-component-extensions-with-background-pages',
        '--disable-back-forward-cache',
        '--disable-logging',
        '--disable-permissions-api',
        '--hide-scrollbars',
        '--mute-audio'
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
        headless=True,  # Back to headless for VPS compatibility
        args=browser_args
    )
    
    # More realistic user agents
    user_agents = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    ]
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent=random.choice(user_agents),  # Random user agent
        proxy=proxy,
        java_script_enabled=True,
        locale='en-US',
        timezone_id='America/Los_Angeles',
        color_scheme='light',
        reduced_motion='no-preference',
        forced_colors='none',
        extra_http_headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
    )
    
    # Advanced anti-detection script
    await context.add_init_script("""
        // Remove webdriver property
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Mock plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Mock languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        
        // Mock permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // Mock chrome runtime
        if (!window.chrome) {
            window.chrome = {};
        }
        if (!window.chrome.runtime) {
            window.chrome.runtime = {};
        }
        
        // Hide automation traces
        delete window.navigator.__proto__.webdriver;
        
        // Override the plugins property to use a custom getter
        Object.defineProperty(navigator, 'plugins', {
            get: function() {
                return [1, 2, 3, 4, 5];
            },
        });
        
        // Mock battery API
        Object.defineProperty(navigator, 'getBattery', {
            get: () => () => Promise.resolve({
                charging: true,
                chargingTime: 0,
                dischargingTime: Infinity,
                level: 1
            })
        });
        
        // Mock connection
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false
            })
        });
        
        // Mock hardware concurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 4
        });
        
        // Mock device memory
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8
        });
        
        // Override toString to hide headless nature
        const elementDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
            ...elementDescriptor,
            get: function() {
                if (this.tagName === 'BODY') {
                    return Math.max(document.documentElement.clientHeight, window.innerHeight || 0);
                }
                return elementDescriptor.get.apply(this);
            }
        });
        
        // Mock screen properties
        Object.defineProperty(screen, 'availTop', { get: () => 0 });
        Object.defineProperty(screen, 'availLeft', { get: () => 0 });
        Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
        Object.defineProperty(screen, 'availHeight', { get: () => 1080 });
        Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
        Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
        
        // Mock notification permission
        Object.defineProperty(Notification, 'permission', {
            get: () => 'default'
        });
        
        // Add some realistic properties
        Object.defineProperty(window, 'outerHeight', { get: () => 1080 });
        Object.defineProperty(window, 'outerWidth', { get: () => 1920 });
        
        // Mock speech synthesis
        if (!window.speechSynthesis) {
            window.speechSynthesis = {
                getVoices: () => [],
                speak: () => {},
                cancel: () => {},
                pause: () => {},
                resume: () => {},
                speaking: false,
                pending: false,
                paused: false
            };
        }
    """)
    
    return browser, context

async def human_like_type(page: Page, selector: str, text: str) -> None:
    """Type text with human-like delays and patterns."""
    element = await page.wait_for_selector(selector)
    
    # Move mouse to element before clicking
    box = await element.bounding_box()
    if box:
        # Add some randomness to click position
        x = box['x'] + box['width'] / 2 + random.randint(-20, 20)
        y = box['y'] + box['height'] / 2 + random.randint(-5, 5)
        await page.mouse.move(x, y)
        await page.wait_for_timeout(random.randint(100, 300))
    
    await element.click()
    
    # Clear existing text
    await element.evaluate('el => el.value = ""')
    
    # Type with random delays between characters
    for char in text:
        await page.keyboard.type(char, delay=random.randint(50, 200))
        # Occasionally pause as humans do
        if random.random() < 0.1:  # 10% chance
            await page.wait_for_timeout(random.randint(200, 500))

async def human_like_click(page: Page, element) -> None:
    """Click an element with human-like mouse movement."""
    box = await element.bounding_box()
    if box:
        # Add randomness to click position
        x = box['x'] + box['width'] / 2 + random.randint(-10, 10)
        y = box['y'] + box['height'] / 2 + random.randint(-3, 3)
        
        # Move mouse in a slightly curved path
        current_pos = await page.evaluate('() => [window.mouseX || 0, window.mouseY || 0]')
        start_x, start_y = current_pos[0], current_pos[1]
        
        # Move in 3-5 steps
        steps = random.randint(3, 5)
        for i in range(steps):
            progress = (i + 1) / steps
            # Add slight curve
            curve_offset = math.sin(progress * math.pi) * random.randint(-5, 5)
            intermediate_x = start_x + (x - start_x) * progress + curve_offset
            intermediate_y = start_y + (y - start_y) * progress
            
            await page.mouse.move(intermediate_x, intermediate_y)
            await page.wait_for_timeout(random.randint(20, 50))
        
        # Final position and click
        await page.mouse.move(x, y)
        await page.wait_for_timeout(random.randint(50, 150))
    
    await element.click()

async def login_to_instagram(page: Page) -> None:
    """Handle the Instagram login process with human-like behavior."""
    try:
        logger.info("Starting Instagram login process")
        await page.wait_for_load_state('networkidle')
        
        # Add random initial delay
        await page.wait_for_timeout(random.randint(2000, 4000))
        
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
        
        # Human-like username typing
        await username_input.evaluate('el => el.value = ""')
        await username_input.click()
        await page.wait_for_timeout(random.randint(500, 1000))
        
        for char in settings.IG_USERNAME:
            await page.keyboard.type(char, delay=random.randint(80, 250))
            if random.random() < 0.05:  # 5% chance to pause
                await page.wait_for_timeout(random.randint(100, 300))
        
        # Random delay before password
        await page.wait_for_timeout(random.randint(800, 1500))
        
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
        
        # Human-like password typing
        await password_input.evaluate('el => el.value = ""')
        await password_input.click()
        await page.wait_for_timeout(random.randint(300, 800))
        
        for char in settings.IG_PASSWORD:
            await page.keyboard.type(char, delay=random.randint(70, 200))
            if random.random() < 0.03:  # 3% chance to pause
                await page.wait_for_timeout(random.randint(100, 400))
        
        # Random delay before clicking login
        await page.wait_for_timeout(random.randint(1000, 2000))
        
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
        
        await human_like_click(page, login_button)
        
        # Wait longer to see if 2FA is required
        await page.wait_for_timeout(random.randint(4000, 6000))
        
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
            
            # Human-like 2FA code entry
            await two_fa_input.click()
            await page.wait_for_timeout(random.randint(500, 1000))
            
            for char in code:
                await page.keyboard.type(char, delay=random.randint(150, 300))
                if random.random() < 0.1:  # 10% chance to pause
                    await page.wait_for_timeout(random.randint(200, 500))
            
            # Wait before confirming
            await page.wait_for_timeout(random.randint(1000, 2000))
            
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
                        await human_like_click(page, button)
                        break
                except Exception:
                    continue
            
            # Wait longer after 2FA
            await page.wait_for_timeout(random.randint(5000, 8000))
        
        # Check for successful login with longer timeout
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
                    await page.wait_for_selector(f"xpath={selector}", timeout=15000)
                else:
                    await page.wait_for_selector(selector, timeout=15000)
                logged_in = True
                break
            except Exception:
                continue
        
        if logged_in:
            logger.info("Successfully logged in to Instagram")
            # Add final random delay to look more human
            await page.wait_for_timeout(random.randint(2000, 4000))
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
                
                # Add random delay after page load
                await page.wait_for_timeout(random.randint(2000, 4000))
                
                await handle_cookie_consent(page)
                await login_to_instagram(page)
                
                logger.debug("Visiting additional Instagram pages...")
                for url in [
                    'https://www.instagram.com/',
                    'https://www.instagram.com/direct/inbox/',
                    'https://www.instagram.com/explore/'
                ]:
                    await page.goto(url)
                    # Random delay between page visits
                    await page.wait_for_timeout(random.randint(1500, 3000))
                
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
                    # Longer delay between retries to avoid looking automated
                    await asyncio.sleep(random.randint(10, 20))
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