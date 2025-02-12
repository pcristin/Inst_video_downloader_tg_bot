"""Instagram authentication and cookie management utilities."""
import logging
import time
import platform
import subprocess
from pathlib import Path
from typing import Dict, List

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from ..config.settings import settings
from .two_factor import TwoFactorAuth

logger = logging.getLogger(__name__)

class InstagramAuthError(Exception):
    """Raised when Instagram authentication fails."""
    pass

def check_chromium_installed() -> bool:
    """Check if Chromium is installed on Linux."""
    try:
        subprocess.run(['which', 'chromium-browser'], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        try:
            subprocess.run(['which', 'chromium'], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

def get_proxy_options() -> dict:
    """Get proxy configuration for WebDriver."""
    if not settings.PROXY_HOST or not settings.PROXY_PORT:
        return {}
    
    proxy_string = f"{settings.PROXY_HOST}:{settings.PROXY_PORT}"
    if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
        auth = f"{settings.PROXY_USERNAME}:{settings.PROXY_PASSWORD}@"
        proxy_string = f"{auth}{proxy_string}"
    
    proxy_options = {
        'proxy': {
            'httpProxy': f"http://{proxy_string}",
            'proxyType': 'manual'
        }
    }
    
    logger.info(f"Using HTTP proxy: {settings.PROXY_HOST}:{settings.PROXY_PORT}")
    return proxy_options

def get_chrome_options() -> Options:
    """Configure Chrome options for headless operation with some stealth settings."""
    chrome_options = Options()
    
    # Add some anti-detection flags
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    if platform.system().lower() == "linux":
        chrome_options.binary_location = "/usr/bin/chromium-browser"
        chrome_options.add_argument("--headless")  # For testing, you might remove headless mode.
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--user-data-dir=/tmp/chrome-data")
        
        if settings.PROXY_HOST and settings.PROXY_PORT:
            proxy_string = f"{settings.PROXY_HOST}:{settings.PROXY_PORT}"
            if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
                chrome_options.add_argument(f'--proxy-server=http://{settings.PROXY_USERNAME}:{settings.PROXY_PASSWORD}@{proxy_string}')
            else:
                chrome_options.add_argument(f'--proxy-server=http://{proxy_string}')
    else:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-gpu")
    
    # Common options
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-dev-tools")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--lang=en-US")
    
    return chrome_options

def get_webdriver() -> webdriver.Chrome:
    """Create and configure Chrome WebDriver based on OS."""
    try:
        service = Service("/usr/bin/chromedriver")
        options = get_chrome_options()
        
        # If proxy options exist, add them as capabilities.
        proxy_options = get_proxy_options()
        if proxy_options:
            options.set_capability('proxy', proxy_options['proxy'])
        
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(30)
        
        # Overwrite the navigator.webdriver property for stealth.
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
                """
            },
        )
        
        # Set a realistic user agent.
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36")
        })
        driver.execute_cdp_cmd('Network.enable', {})
        return driver
        
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {str(e)}")
        raise

def format_cookie_for_yt_dlp(cookie: Dict) -> str:
    """Format a cookie dictionary into Netscape format for yt-dlp."""
    return (
        f"{cookie['domain']}\tTRUE\t{cookie['path']}\t"
        f"{'TRUE' if cookie.get('secure', False) else 'FALSE'}\t{cookie.get('expiry', int(time.time()) + 31536000)}\t"
        f"{cookie['name']}\t{cookie['value']}"
    )

def save_cookies(cookies: List[Dict], output_file: Path) -> None:
    """Save cookies in Netscape format for yt-dlp."""
    try:
        with open(output_file, 'w', encoding='utf-8', newline='\n') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                # Ensure expiry exists; default to 1 year from now if missing.
                if 'expiry' not in cookie:
                    cookie['expiry'] = int(time.time()) + 3600 * 24 * 365
                f.write(format_cookie_for_yt_dlp(cookie) + '\n')
        logger.info(f"Cookies saved to {output_file}")
    except Exception as e:
        raise InstagramAuthError(f"Failed to save cookies: {str(e)}")

def refresh_instagram_cookies(retry_count: int = 0) -> bool:
    """Refresh Instagram authentication cookies using Selenium."""
    try:
        logger.info("Starting Instagram authentication process")
        driver = get_webdriver()
        # Increase the explicit wait timeout if needed.
        wait = WebDriverWait(driver, 30)
        
        try:
            driver.get('https://www.instagram.com/accounts/login/')
            logger.info("Instagram login page loaded")
            
            # Wait for the username field to be visible.
            # (Presence is not enough because Instagram sometimes hides the fields initially.)
            username_input = wait.until(
                EC.visibility_of_element_located((By.NAME, "username"))
            )
            
            # (Optional) Accept cookies if the popup appears.
            try:
                # Try a couple of possible selectors for the "Accept" button.
                accept_button = wait.until(EC.element_to_be_clickable((
                    By.XPATH, "//*[contains(text(), 'Accept') or contains(text(), 'Allow')]"
                )))
                accept_button.click()
                logger.info("Accepted cookies")
                # Give the page a moment to update after clicking.
                time.sleep(2)
            except Exception:
                logger.debug("Cookie acceptance not required or button not found.")
            
            # Wait for the password field to be visible.
            password_input = wait.until(
                EC.visibility_of_element_located((By.NAME, "password"))
            )
            
            # Fill in the login credentials.
            username_input.clear()
            username_input.send_keys(settings.IG_USERNAME)
            time.sleep(1)
            
            password_input.clear()
            password_input.send_keys(settings.IG_PASSWORD)
            time.sleep(1)
            logger.info("Filled in login credentials")
            
            # Locate and click the login/submit button.
            submit_button = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )
            submit_button.click()
            logger.info("Clicked login button")
            
            # Wait for an element that only appears when logged in.
            # Adjust the selectors if Instagram changes its layout.
            success_indicators = [
                (By.XPATH, "//div[@role='menuitem']"),
                (By.XPATH, "//span[contains(text(), 'Search')]"),
                (By.XPATH, "//a[contains(@href, '/direct/inbox/')]"),
                (By.XPATH, "//span[contains(@class, '_aaav')]"),
                (By.XPATH, "//a[contains(@href, '/explore/')]")
            ]
            
            login_successful = False
            for locator in success_indicators:
                try:
                    wait.until(EC.visibility_of_element_located(locator))
                    login_successful = True
                    logger.info(f"Login indicator found: {locator}")
                    break
                except Exception:
                    continue
            
            if not login_successful:
                # Try to capture possible error messages.
                error_selectors = [
                    (By.XPATH, "//p[@data-testid='login-error-message']"),
                    (By.XPATH, "//div[contains(@class, 'error')]"),
                    (By.XPATH, "//p[contains(text(), 'sorry')]"),
                    (By.XPATH, "//p[contains(text(), 'Please wait')]")
                ]
                for sel in error_selectors:
                    try:
                        error_elem = driver.find_element(*sel)
                        logger.error(f"Login error: {error_elem.text}")
                        return False
                    except Exception:
                        continue
                
                logger.error("Could not verify login success")
                return False
            
            # If login is successful, extract and save cookies.
            cookies = driver.get_cookies()
            if not cookies:
                raise InstagramAuthError("No cookies found after login")
            
            save_cookies(cookies, settings.COOKIES_FILE)
            logger.info("Instagram authentication successful")
            return True
            
        finally:
            driver.quit()
            
    except Exception as e:
        logger.error(f"Instagram authentication failed: {str(e)}")
        return False