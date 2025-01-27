import json
import time
import logging
import os
import tempfile
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from config import IG_USERNAME, IG_PASSWORD

def convert_cookies_to_netscape(cookies_json, output_file):
    """
    Converts JSON cookies from Selenium to Netscape format for yt-dlp.
    Ensures proper newline format and header for the cookies file.
    """
    # Always use Unix-style newlines for cookie files
    with open(output_file, 'w', newline='\n', encoding='utf-8') as f:
        # Write the required header
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# https://curl.haxx.se/rfc/cookie_spec.html\n")
        f.write("# This is a generated file!  Do not edit.\n\n")
        
        for cookie in cookies_json:
            # Skip invalid cookies
            if not all(k in cookie for k in ('domain', 'name', 'value')):
                continue
                
            domain = cookie['domain']
            # Remove leading dot if present (we'll add it back)
            domain = domain.lstrip('.')
            # Always add leading dot for Instagram domains
            if 'instagram.com' in domain:
                domain = '.' + domain
                
            flag = 'TRUE'
            path = cookie.get('path', '/')
            secure = 'TRUE' if cookie.get('secure', True) else 'FALSE'
            expiry = int(cookie.get('expiry', 0))
            name = cookie['name']
            value = cookie['value']
            
            # Ensure proper escaping of values
            value = value.replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
            
            # Write cookie in exact Netscape format with tab separators
            cookie_line = f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
            f.write(cookie_line)

def get_instagram_cookies():
    """
    Automatically logs in to Instagram using Selenium and returns cookies in Netscape format.
    """
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument('--headless')  # Run in headless mode
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.binary_location = '/snap/bin/chromium'
    
    # Initialize the WebDriver with undetected-chromedriver
    driver = uc.Chrome(options=chrome_options)
    try:
        driver.get('https://www.instagram.com/accounts/login/')
        logging.info("Navigated to Instagram login page.")
        
        # Wait for the login form to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        logging.info("Login form located.")

        # Enter username
        username_input = driver.find_element(By.NAME, "username")
        username_input.send_keys(IG_USERNAME)
        logging.info("Username entered.")

        # Enter password
        password_input = driver.find_element(By.NAME, "password")
        password_input.send_keys(IG_PASSWORD)
        logging.info("Password entered.")

        # Click the login button
        login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        login_button.click()
        logging.info("Login button clicked.")

        # Wait for login to complete
        WebDriverWait(driver, 20).until(
            EC.url_changes('https://www.instagram.com/accounts/login/')
        )
        logging.info("Login process completed.")

        # Check if login was successful
        if "login" in driver.current_url:
            logging.error("Login failed. Please check your credentials.")
            return None

        # Retrieve cookies
        cookies = driver.get_cookies()
        logging.info(f"Retrieved {len(cookies)} cookies.")

        # Convert and save cookies to Netscape format
        cookies_file = 'instagram_cookies.txt'
        convert_cookies_to_netscape(cookies, cookies_file)
        logging.info(f"Cookies saved to {cookies_file} in Netscape format.")

        return cookies_file

    except Exception as e:
        logging.error(f"Error getting Instagram cookies: {str(e)}")
        return None

    finally:
        driver.quit()
        logging.info("WebDriver session closed.")

if __name__ == "__main__":
    get_instagram_cookies()