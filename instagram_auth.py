import json
import time
import logging
import os
import random
import tempfile
from time import sleep
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
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

def human_like_delay():
    """Simulates human-like delay between actions"""
    sleep(random.uniform(0.5, 2.0))

def random_mouse_movement(driver, element):
    """Performs random mouse movements before clicking"""
    action = ActionChains(driver)
    
    # Move to random position first
    rand_x = random.randint(100, 500)
    rand_y = random.randint(100, 500)
    action.move_by_offset(rand_x, rand_y)
    
    # Then move to element with some randomness
    action.move_to_element_with_offset(
        element,
        random.randint(-5, 5),
        random.randint(-5, 5)
    )
    action.perform()
    human_like_delay()

def human_like_typing(element, text):
    """Types text in a human-like manner with random delays"""
    for char in text:
        element.send_keys(char)
        sleep(random.uniform(0.1, 0.3))  # Random delay between keystrokes

def get_instagram_cookies():
    """
    Automatically logs in to Instagram using Selenium with human-like behavior
    """
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.binary_location = '/snap/bin/chromium'
    
    # Add random user agent
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0'
    ]
    chrome_options.add_argument(f'user-agent={random.choice(user_agents)}')
    
    # Add language and timezone to appear more natural
    chrome_options.add_argument('--lang=en-US,en;q=0.9')
    chrome_options.add_argument('--timezone=America/New_York')
    
    driver = uc.Chrome(options=chrome_options)
    try:
        # Random starting viewport size
        driver.set_window_size(
            random.randint(1024, 1920),
            random.randint(768, 1080)
        )
        
        # Visit Instagram homepage first, like a real user
        driver.get('https://www.instagram.com')
        human_like_delay()
        
        # Then go to login page
        driver.get('https://www.instagram.com/accounts/login/')
        logging.info("Navigated to Instagram login page.")
        
        # Add some random scrolling
        driver.execute_script(f"window.scrollTo(0, {random.randint(50, 200)})")
        human_like_delay()
        
        # Wait for login form with random timeout
        username_input = WebDriverWait(driver, random.randint(8, 12)).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        logging.info("Login form located.")
        
        # Move mouse to username field and click
        random_mouse_movement(driver, username_input)
        username_input.click()
        human_like_delay()
        
        # Type username like a human
        human_like_typing(username_input, IG_USERNAME)
        human_like_delay()
        
        # Find and fill password with human-like behavior
        password_input = driver.find_element(By.NAME, "password")
        random_mouse_movement(driver, password_input)
        password_input.click()
        human_like_typing(password_input, IG_PASSWORD)
        
        # Random delay before clicking login
        sleep(random.uniform(1.0, 2.0))
        
        # Find and click login button
        login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        random_mouse_movement(driver, login_button)
        login_button.click()
        logging.info("Login button clicked.")
        
        # Add random delay after login
        sleep(random.uniform(3.0, 5.0))
        
        # Wait for login to complete with random timeout
        WebDriverWait(driver, random.randint(15, 25)).until(
            EC.url_changes('https://www.instagram.com/accounts/login/')
        )
        logging.info("Login process completed.")
        
        # Random delay before getting cookies
        sleep(random.uniform(2.0, 4.0))
        
        # Simulate some random scrolling after login
        scroll_amount = random.randint(100, 500)
        driver.execute_script(f"window.scrollTo(0, {scroll_amount})")
        human_like_delay()
        driver.execute_script(f"window.scrollTo({scroll_amount}, 0)")
        
        # Check login success
        if "login" in driver.current_url:
            logging.error("Login failed. Please check your credentials.")
            return None
            
        # Get cookies
        cookies = driver.get_cookies()
        logging.info(f"Retrieved {len(cookies)} cookies.")
        
        # Save cookies
        cookies_file = 'instagram_cookies.txt'
        convert_cookies_to_netscape(cookies, cookies_file)
        logging.info(f"Cookies saved to {cookies_file} in Netscape format.")
        
        return cookies_file
        
    except Exception as e:
        logging.error(f"Error getting Instagram cookies: {str(e)}")
        return None
        
    finally:
        # Random delay before quitting
        sleep(random.uniform(1.0, 2.0))
        driver.quit()
        logging.info("WebDriver session closed.")

if __name__ == "__main__":
    get_instagram_cookies()