import json
import time
import logging
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
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.binary_location = '/usr/bin/chromium'  # Use Chromium instead of Chrome
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.get('https://www.instagram.com/accounts/login/')
        
        # Wait for the login form
        time.sleep(5)  # Let the page load completely
        
        # Find and fill username
        username_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        username_input.send_keys(IG_USERNAME)
        
        # Find and fill password
        password_input = driver.find_element(By.NAME, "password")
        password_input.send_keys(IG_PASSWORD)
        
        # Click login button
        login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        login_button.click()
        
        # Wait for login to complete
        time.sleep(10)
        
        # Get cookies
        cookies = driver.get_cookies()
        
        # Save cookies to file
        with open('instagram_cookies.txt', 'w') as f:
            json.dump(cookies, f)
        
        driver.quit()
        return True
        
    except Exception as e:
        logging.error(f"Error getting Instagram cookies: {str(e)}")
        if 'driver' in locals():
            driver.quit()
        return False 