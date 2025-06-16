"""Debug script to inspect Instagram login page and find correct selectors."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def debug_instagram_login():
    """Debug Instagram login page to find correct selectors."""
    async with async_playwright() as p:
        # Launch browser in headful mode for debugging
        browser = await p.chromium.launch(
            headless=False,  # Set to False to see what's happening
            args=['--disable-blink-features=AutomationControlled']
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        )
        
        # Add anti-detection script
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()
        
        try:
            logger.info("Navigating to Instagram login page...")
            await page.goto('https://www.instagram.com/accounts/login/', wait_until='networkidle')
            
            # Wait for page to fully load
            await page.wait_for_timeout(5000)
            
            # Save screenshot
            await page.screenshot(path='instagram_login_page.png')
            logger.info("Screenshot saved as instagram_login_page.png")
            
            # Get page content
            content = await page.content()
            with open('instagram_login_page.html', 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info("Page HTML saved as instagram_login_page.html")
            
            # Try to find all input fields
            logger.info("\n=== Looking for input fields ===")
            inputs = await page.query_selector_all('input')
            for i, input_elem in enumerate(inputs):
                attrs = await input_elem.evaluate('''(el) => {
                    return {
                        name: el.name,
                        type: el.type,
                        placeholder: el.placeholder,
                        ariaLabel: el.getAttribute('aria-label'),
                        id: el.id,
                        className: el.className,
                        autocomplete: el.autocomplete,
                        isVisible: el.offsetParent !== null
                    }
                }''')
                logger.info(f"Input {i}: {json.dumps(attrs, indent=2)}")
            
            # Try to find all buttons
            logger.info("\n=== Looking for buttons ===")
            buttons = await page.query_selector_all('button')
            for i, button in enumerate(buttons):
                attrs = await button.evaluate('''(el) => {
                    return {
                        text: el.textContent,
                        type: el.type,
                        ariaLabel: el.getAttribute('aria-label'),
                        id: el.id,
                        className: el.className,
                        isVisible: el.offsetParent !== null
                    }
                }''')
                logger.info(f"Button {i}: {json.dumps(attrs, indent=2)}")
            
            # Check for forms
            logger.info("\n=== Looking for forms ===")
            forms = await page.query_selector_all('form')
            logger.info(f"Found {len(forms)} forms")
            
            # Wait for manual inspection
            logger.info("\n=== Browser will stay open for 30 seconds for manual inspection ===")
            await page.wait_for_timeout(30000)
            
        except Exception as e:
            logger.error(f"Error during debug: {str(e)}")
            await page.screenshot(path='error_screenshot.png')
        
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_instagram_login()) 