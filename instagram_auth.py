import json
import time
import logging
import os
import tempfile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from config import IG_USERNAME, IG_PASSWORD

def convert_cookies_to_netscape(cookies_json, output_file):
    """
    Converts JSON cookies from Selenium to Netscape format for yt-dlp.
    """
    with open(output_file, 'w') as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies_json:
            # Ensure all required fields are present
            if 'domain' not in cookie or 'name' not in cookie or 'value' not in cookie:
                continue
            domain = cookie['domain']
            if domain.startswith('.'):
                domain = domain[1:]
            flag = 'TRUE' if cookie.get('secure', False) else 'FALSE'
            path = cookie.get('path', '/')
            secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
            expiration = str(int(cookie.get('expiry', 0)))
            name = cookie['name']
            value = cookie['value']
            f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiration}\t{name}\t{value}\n")

def get_instagram_cookies():
    """
    Automatically logs in to Instagram using Selenium and returns cookies in Netscape format.
    """
    # Create a unique temporary directory for user data
    with tempfile.TemporaryDirectory() as user_data_dir:
        chrome_options = Options()
        chrome_options.add_argument('--headless')  # Run in headless mode
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument(f'--user-data-dir={user_data_dir}')  # Unique user data directory
        chrome_options.binary_location = '/usr/bin/chromium'  # Update this path if different

        # Initialize the WebDriver
        driver = webdriver.Chrome(options=chrome_options)
        try:
            driver.get('https://www.instagram.com/accounts/login/')
            logging.info("Navigated to Instagram login page.")
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

            # Wait for login to complete (you might need to adjust the sleep duration)
            time.sleep(20)

            # Check for successful login by looking for the home page element
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