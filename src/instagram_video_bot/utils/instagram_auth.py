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
    """Refresh Instagram authentication cookies using Selenium."""
    try:
        logger.info("Starting Instagram authentication process")
        driver = get_webdriver()
        wait = WebDriverWait(driver, 30)  # Increased timeout
        
        try:
            # Navigate to Instagram login page and wait for it to load completely
            driver.get('https://www.instagram.com/accounts/login/')
            time.sleep(3)  # Initial wait
            
            # Try to find and click any "Accept Cookies" button if present
            try:
                cookie_buttons = driver.find_elements(By.XPATH, 
                    "//*[contains(text(), 'Accept') or contains(text(), 'Allow')]")
                for button in cookie_buttons:
                    if button.is_displayed():
                        button.click()
                        time.sleep(2)
                        break
            except Exception:
                pass
            
            logger.info("Loaded Instagram login page")
            
            # Updated selectors for username field with exact Instagram classes
            username_selectors = [
                "//input[@class='_aa4b _add6 _ac4d _ap35']",
                "//input[@name='username']",
                "//label[@class='_aa48']/input",
                "//input[@aria-label='Phone number, username or email address']"
            ]
            
            # Try to find username input
            username_input = None
            for selector in username_selectors:
                try:
                    username_input = wait.until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    if username_input and username_input.is_displayed():
                        # Verify we found the correct element
                        if username_input.get_attribute('class') == '_aa4b _add6 _ac4d _ap35':
                            break
                except:
                    continue
            
            if not username_input:
                # Try to refresh the page and try again
                driver.refresh()
                time.sleep(5)
                for selector in username_selectors:
                    try:
                        username_input = wait.until(
                            EC.presence_of_element_located((By.XPATH, selector))
                        )
                        if username_input and username_input.is_displayed():
                            if username_input.get_attribute('class') == '_aa4b _add6 _ac4d _ap35':
                                break
                    except:
                        continue
            
            if not username_input:
                raise InstagramAuthError("Could not find username input")
            
            # Updated selectors for password field with exact Instagram classes
            password_selectors = [
                "//input[@type='password' and @class='_aa4b _add6 _ac4d _ap35']",
                "//input[@name='password' and @class='_aa4b _add6 _ac4d _ap35']",
                "//label[@class='_aa48']/input[@type='password']"
            ]
            
            # Try to find password input
            password_input = None
            for selector in password_selectors:
                try:
                    password_input = wait.until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    if password_input.is_displayed():
                        break
                except:
                    continue
            
            if not password_input:
                raise InstagramAuthError("Could not find password input")
            
            # Clear and fill inputs with delays and retry on failure
            try:
                driver.execute_script("arguments[0].value = '';", username_input)
                username_input.send_keys(settings.IG_USERNAME)
                time.sleep(1)
                
                driver.execute_script("arguments[0].value = '';", password_input)
                password_input.send_keys(settings.IG_PASSWORD)
                time.sleep(1)
                
                logger.info("Filled login credentials")
            except Exception as e:
                logger.error(f"Failed to fill credentials: {str(e)}")
                return False
            
            # Updated login button selectors
            login_selectors = [
                "//button[@type='submit']",
                "//button[contains(text(), 'Log in')]",
                "//button[contains(text(), 'Log In')]",
                "//button[contains(@class, 'primary')]",
                "//div[contains(text(), 'Log in')]/parent::button"
            ]
            
            # Try to find and click login button
            login_button = None
            for selector in login_selectors:
                try:
                    login_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    if login_button.is_displayed():
                        break
                except:
                    continue
            
            if not login_button:
                raise InstagramAuthError("Could not find login button")
            
            # Try to click the button using different methods
            try:
                login_button.click()
            except:
                try:
                    driver.execute_script("arguments[0].click();", login_button)
                except:
                    actions = webdriver.ActionChains(driver)
                    actions.move_to_element(login_button).click().perform()
            
            time.sleep(5)  # Wait for login process
            logger.info("Clicked login button")
            
            # Wait for successful login
            success_selectors = [
                "//div[@role='menuitem']",  # Profile menu item
                "//span[contains(text(), 'Search')]",  # Search text
                "//a[@href='/direct/inbox/']",  # Direct messages link
                "//span[@class='_aaav']",  # Profile icon
                "//a[@href='/explore/']"  # Explore link
            ]
            
            login_successful = False
            for selector in success_selectors:
                try:
                    wait.until(EC.presence_of_element_located((By.XPATH, selector)))
                    login_successful = True
                    break
                except:
                    continue
            
            if not login_successful:
                error_selectors = [
                    "//p[@data-testid='login-error-message']",
                    "//div[contains(@class, 'error')]",
                    "//p[contains(text(), 'sorry')]",
                    "//p[contains(text(), 'Please wait')]"
                ]
                
                for selector in error_selectors:
                    try:
                        error_elem = driver.find_element(By.XPATH, selector)
                        logger.error(f"Login error: {error_elem.text}")
                        return False
                    except:
                        continue
                
                logger.error("Could not verify login success")
                return False
            
            # Get and save cookies
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