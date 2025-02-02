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
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

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

def get_chrome_options() -> Options:
    """Configure Chrome options for headless operation."""
    chrome_options = Options()
    
    if platform.system().lower() == "linux":
        # Specific options for Ubuntu Chromium
        chrome_options.binary_location = "/usr/bin/chromium-browser"  # Changed back to standard path
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--user-data-dir=/tmp/chrome-data")
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
    
    return chrome_options

def get_webdriver() -> webdriver.Chrome:
    """Create and configure Chrome WebDriver based on OS."""
    try:
        # Use system's ChromeDriver
        service = Service("/usr/bin/chromedriver")
        
        driver = webdriver.Chrome(
            service=service,
            options=get_chrome_options()
        )
        
        # Set page load timeout
        driver.set_page_load_timeout(30)
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
        with open(output_file, 'w', encoding='utf-8') as f:
            for cookie in cookies:
                if 'expiry' not in cookie:
                    cookie['expiry'] = int(time.time()) + 3600 * 24 * 365  # 1 year
                f.write(format_cookie_for_yt_dlp(cookie) + '\n')
        logger.info(f"Cookies saved to {output_file}")
    except Exception as e:
        raise InstagramAuthError(f"Failed to save cookies: {str(e)}")

def refresh_instagram_cookies() -> bool:
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
            
            # Handle 2FA if configured
            if settings.TOTP_SECRET:
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
                    
                    # If code expired, try one more time
                    if "code is no longer valid" in error_message.lower():
                        logger.info("Code expired, retrying with fresh code")
                        return refresh_instagram_cookies()
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