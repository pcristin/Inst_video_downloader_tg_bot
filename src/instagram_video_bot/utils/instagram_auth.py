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
        chrome_options.binary_location = "/snap/bin/chromium"
        chrome_options.add_argument("--headless")  # Use old headless mode for Ubuntu's Chromium
    else:
        chrome_options.add_argument("--headless=new")
        
    # Common options
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-software-rasterizer")
    
    return chrome_options

def get_webdriver() -> webdriver.Chrome:
    """Create and configure Chrome WebDriver based on OS."""
    try:
        os_name = platform.system().lower()
        
        if os_name == "linux":
            # Use specific ChromeDriver version for Ubuntu's Chromium
            driver_manager = ChromeDriverManager(
                chrome_type=ChromeType.CHROMIUM,
                driver_version="85.0.4183.83"  # Match Ubuntu's Chromium version
            )
        else:
            driver_manager = ChromeDriverManager()
        
        driver_path = driver_manager.install()
        service = Service(driver_path)
        
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
        
        try:
            # Navigate to Instagram login page
            driver.get('https://www.instagram.com/accounts/login/')
            wait = WebDriverWait(driver, 10)
            
            # Wait for and fill in the login form
            username_input = wait.until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            password_input = wait.until(
                EC.presence_of_element_located((By.NAME, "password"))
            )
            
            username_input.send_keys(settings.IG_USERNAME)
            password_input.send_keys(settings.IG_PASSWORD)
            
            # Submit the form
            password_input.submit()
            
            # Handle 2FA if configured
            if settings.TOTP_SECRET:
                try:
                    # Wait for 2FA input field
                    code_input = wait.until(
                        EC.presence_of_element_located((By.NAME, "verificationCode"))
                    )
                    
                    # Generate and enter 2FA code
                    auth = TwoFactorAuth()
                    code = auth.get_current_code()
                    code_input.send_keys(code)
                    code_input.submit()
                    
                    logger.info("2FA code submitted")
                except Exception as e:
                    logger.error(f"Failed to handle 2FA: {str(e)}")
                    return False
            
            # Wait for successful login
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "span[role='link']")
                )
            )
            
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