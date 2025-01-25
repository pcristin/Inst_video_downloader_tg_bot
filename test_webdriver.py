import logging
import undetected_chromedriver as uc

def test_webdriver():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument('--headless')  # Run in headless mode
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    
    try:
        driver = uc.Chrome(options=chrome_options)
        logging.info("WebDriver session started successfully.")
        
        driver.get('https://www.google.com')
        logging.info(f"Page title: {driver.title}")
        
    except Exception as e:
        logging.error(f"Error initializing WebDriver: {str(e)}")
    finally:
        driver.quit()
        logging.info("WebDriver session closed.")

if __name__ == "__main__":
    test_webdriver()
