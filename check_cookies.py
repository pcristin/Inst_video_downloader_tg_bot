#!/usr/bin/env python3
"""Check if Instagram cookies are still valid."""
import sys
import requests
from pathlib import Path

def parse_cookies_file(cookies_file: Path) -> dict:
    """Parse Netscape format cookies file."""
    cookies = {}
    
    if not cookies_file.exists():
        print(f"❌ Cookies file not found: {cookies_file}")
        return cookies
    
    try:
        with open(cookies_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                
                parts = line.split('\t')
                if len(parts) >= 7:
                    domain, flag, path, secure, expires, name, value = parts[:7]
                    if 'instagram.com' in domain:
                        cookies[name] = value
    except Exception as e:
        print(f"❌ Error reading cookies file: {e}")
    
    return cookies

def check_instagram_session(cookies: dict) -> bool:
    """Check if Instagram session is valid."""
    if not cookies.get('sessionid'):
        print("❌ No sessionid cookie found")
        return False
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    try:
        # Try to access Instagram's main page
        response = requests.get(
            'https://www.instagram.com/',
            cookies=cookies,
            headers=headers,
            timeout=10
        )
        
        # Check if we're logged in
        if response.status_code == 200:
            content = response.text.lower()
            
            # Look for signs we're logged in
            if 'logged_in":true' in content or '"is_logged_in":true' in content:
                print("✅ Cookies are valid - logged in successfully")
                return True
            elif 'login' in content and 'password' in content:
                print("❌ Cookies are invalid - redirected to login page")
                return False
            else:
                print("⚠️  Cookies status unclear - might be rate limited")
                return False
        else:
            print(f"❌ HTTP {response.status_code} - cookies might be invalid")
            return False
            
    except Exception as e:
        print(f"❌ Error checking cookies: {e}")
        return False

def main():
    """Main function."""
    # Check if running in Docker
    if Path('/.dockerenv').exists():
        cookies_file = Path('/app/cookies/instagram_cookies.txt')
    else:
        cookies_file = Path('cookies/instagram_cookies.txt')
    
    print(f"🔍 Checking cookies in: {cookies_file}")
    
    cookies = parse_cookies_file(cookies_file)
    
    if not cookies:
        print("❌ No valid cookies found")
        print("\n💡 To fix this:")
        print("1. Update your account.txt with fresh account data")
        print("2. Run: python3 import_cookies.py")
        sys.exit(1)
    
    print(f"📊 Found {len(cookies)} Instagram cookies")
    
    # Show important cookies
    important_cookies = ['sessionid', 'csrftoken', 'ds_user_id']
    for cookie_name in important_cookies:
        if cookie_name in cookies:
            value = cookies[cookie_name]
            print(f"  ✓ {cookie_name}: {value[:20]}...")
        else:
            print(f"  ❌ {cookie_name}: missing")
    
    print("\n🔍 Testing session validity...")
    
    if check_instagram_session(cookies):
        print("\n✅ Cookies are working! Bot should be able to download videos.")
    else:
        print("\n❌ Cookies are expired or invalid!")
        print("\n💡 To fix this:")
        print("1. Get fresh account data")
        print("2. Update account.txt")
        print("3. Run: python3 import_cookies.py")
        print("4. Restart your bot")

if __name__ == "__main__":
    main() 