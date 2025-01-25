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
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.binary_location = '/usr/bin/chromium'
    
    driver = webdriver.Chrome(options=chrome_options)
    try:
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
        with open('instagram_cookies.json', 'w') as f:
            json.dump(cookies, f)
        
        return 'instagram_cookies.json'
        
    finally:
        driver.quit() 