"""Instagram authentication and cookie management utilities."""
import logging
import time
import platform
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

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
    
    # Use HTTP proxy only
    proxy_options = {
        'proxy': {
            'httpProxy': f"http://{proxy_string}",
            'proxyType': 'manual'
        }
    }
    
    logger.info(f"Using HTTP proxy: {settings.PROXY_HOST}:{settings.PROXY_PORT}")
    return proxy_options

def get_chrome_options() -> Options:
    """Configure Chrome options for headless operation."""
    chrome_options = Options()
    
    if platform.system().lower() == "linux":
        # Specific options for Ubuntu Chromium
        chrome_options.binary_location = "/usr/bin/chromium-browser"
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--user-data-dir=/tmp/chrome-data")
        
        # Add proxy directly to Chrome options for better HTTP proxy support
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
        # Use system's ChromeDriver
        service = Service("/usr/bin/chromedriver")
        
        # Get Chrome options
        options = get_chrome_options()
        
        # Add proxy settings to options
        proxy_options = get_proxy_options()
        if proxy_options:
            options.set_capability('proxy', proxy_options['proxy'])
        
        driver = webdriver.Chrome(
            service=service,
            options=options
        )
        
        # Set page load timeout
        driver.set_page_load_timeout(30)
        
        # Add stealth settings
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        # Enable network interception
        driver.execute_cdp_cmd('Network.enable', {})
        
        return driver
        
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {str(e)}")
        raise

def format_cookie_for_yt_dlp(cookie: Dict) -> str:
    """Format a cookie dictionary into Netscape format for yt-dlp."""
    return (
        f"{cookie['domain']}\tTRUE\t{cookie['path']}\t"
        f"{'TRUE' if cookie['secure'] else 'FALSE'}\t{cookie['expiry']}\t"
        f"{cookie['name']}\t{cookie['value']}"
    )

def save_cookies(cookies: List[Dict], output_file: Path) -> None:
    """Save cookies in Netscape format for yt-dlp."""
    try:
        with open(output_file, 'w', encoding='utf-8', newline='\n') as f:
            # Write the required Netscape header
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                if 'expiry' not in cookie:
                    cookie['expiry'] = int(time.time()) + 3600 * 24 * 365  # 1 year
                f.write(format_cookie_for_yt_dlp(cookie) + '\n')
        logger.info(f"Cookies saved to {output_file}")
    except Exception as e:
        raise InstagramAuthError(f"Failed to save cookies: {str(e)}")

def refresh_instagram_cookies(retry_count: int = 0) -> bool:
    """
    Refresh Instagram authentication cookies using Selenium.
    
    Returns:
        bool: True if cookies were successfully refreshed, False otherwise
    """
    try:
        logger.info("Starting Instagram authentication process")
        
        # Initialize Chrome driver with updated configuration
        driver = get_webdriver()
        wait = WebDriverWait(driver, 30)  # Increased timeout further
        
        try:
            # Navigate to Instagram login page
            driver.get('https://www.instagram.com/accounts/login/')
            logger.info("Loaded Instagram login page")
            
            # Wait for login form to be fully loaded
            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form[id='loginForm']"))
            )
            
            # Wait and fill in the login form
            username_input = wait.until(
                EC.element_to_be_clickable((By.NAME, "username"))
            )
            password_input = wait.until(
                EC.element_to_be_clickable((By.NAME, "password"))
            )
            
            # Clear and fill inputs
            username_input.clear()
            username_input.send_keys(settings.IG_USERNAME)
            password_input.clear()
            password_input.send_keys(settings.IG_PASSWORD)
            logger.info("Filled login credentials")
            
            # Find and click the login button
            login_button = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
            )
            login_button.click()
            logger.info("Clicked login button")
            
            # Handle 2FA if TOTP_SECRET is provided and non-empty
            if settings.TOTP_SECRET and settings.TOTP_SECRET.strip():
                try:
                    # Wait for 2FA input field
                    code_input = wait.until(
                        EC.presence_of_element_located((By.NAME, "verificationCode"))
                    )
                    
                    # Wait a moment for any animations to complete
                    time.sleep(2)
                    
                    # Generate a fresh 2FA code
                    auth = TwoFactorAuth()
                    remaining_time = 30 - (int(time.time()) % 30)
                    
                    # If code is about to expire, wait for new one
                    if remaining_time < 5:
                        logger.info(f"Waiting {remaining_time} seconds for fresh 2FA code")
                        time.sleep(remaining_time + 1)
                    
                    code = auth.get_current_code()
                    logger.info("Generated fresh 2FA code")
                    
                    # Clear and enter code
                    code_input.clear()
                    code_input.send_keys(code)
                    
                    # Find and click the verify button
                    verify_button = wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='button']"))
                    )
                    verify_button.click()
                    logger.info("2FA code submitted")
                    
                    # Wait a moment after submitting
                    time.sleep(2)
                    
                except Exception as e:
                    logger.error(f"Failed to handle 2FA: {str(e)}")
                    return False
            
            # Wait for successful login by checking multiple possible elements
            try:
                wait.until(lambda d: any([
                    len(d.find_elements(By.CSS_SELECTOR, "span[role='link']")) > 0,
                    len(d.find_elements(By.CSS_SELECTOR, "svg[aria-label='Home']")) > 0,
                    len(d.find_elements(By.CSS_SELECTOR, "a[href='/']")) > 0,
                    len(d.find_elements(By.CSS_SELECTOR, "svg[aria-label='Instagram']")) > 0
                ]))
                logger.info("Successfully logged in")
            except Exception as e:
                logger.error(f"Failed to verify login success: {str(e)}")
                # Check for error messages
                error_elements = driver.find_elements(By.CSS_SELECTOR, "p[role='alert'], div[role='alert']")
                if error_elements:
                    error_message = error_elements[0].text
                    logger.error(f"Login error message: {error_message}")
                    
                    # If code expired, try again up to 3 times
                    if "code is no longer valid" in error_message.lower():
                        if retry_count < 3:
                            logger.info(f"Code expired, retrying with fresh code (attempt {retry_count + 1}/3)")
                            return refresh_instagram_cookies(retry_count=retry_count+1)
                        else:
                            logger.error("Maximum retries for 2FA exhausted.")
                            return False
                return False
            
            # Get cookies
            cookies = driver.get_cookies()
            if not cookies:
                raise InstagramAuthError("No cookies found after login")
            
            # Save cookies
            save_cookies(cookies, settings.COOKIES_FILE)
            logger.info("Instagram authentication successful")
            return True
            
        finally:
            driver.quit()
            
    except Exception as e:
        logger.error(f"Instagram authentication failed: {str(e)}")
        return False