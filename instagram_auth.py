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

def get_instagram_cookies():
    """
    Automatically logs in to Instagram using Selenium and returns cookies
    """
    # Create a temporary directory for user data
    user_data_dir = tempfile.mkdtemp()
    
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument(f'--user-data-dir={user_data_dir}')
    chrome_options.binary_location = '/usr/bin/chromium'
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.get('https://www.instagram.com/accounts/login/')
        
        logging.info("Waiting for login form...")
        time.sleep(5)
        
        # Find and fill username
        username_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        username_input.send_keys(IG_USERNAME)
        logging.info("Username entered")
        
        # Find and fill password
        password_input = driver.find_element(By.NAME, "password")
        password_input.send_keys(IG_PASSWORD)
        logging.info("Password entered")
        
        # Click login button
        login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        login_button.click()
        logging.info("Login button clicked")
        
        # Wait for login to complete
        time.sleep(10)
        
        # Get cookies
        cookies = driver.get_cookies()
        logging.info(f"Got {len(cookies)} cookies")
        
        # Save cookies to file
        with open('instagram_cookies.txt', 'w') as f:
            json.dump(cookies, f)
        logging.info("Cookies saved to file")
        
        driver.quit()
        
        # Cleanup
        try:
            import shutil
            shutil.rmtree(user_data_dir)
        except Exception as e:
            logging.warning(f"Failed to remove temporary directory: {e}")
            
        return True
        
    except Exception as e:
        logging.error(f"Error getting Instagram cookies: {str(e)}")
        if 'driver' in locals():
            driver.quit()
        # Cleanup on error
        try:
            import shutil
            shutil.rmtree(user_data_dir)
        except Exception as cleanup_error:
            logging.warning(f"Failed to remove temporary directory: {cleanup_error}")
        return False 