"""Import cookies from purchased Instagram accounts."""
import json
import time
from pathlib import Path
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

def parse_account_line(account_line: str) -> Dict[str, Any]:
    """Parse a single account line from the provided format."""
    parts = account_line.strip().split(';')
    
    if len(parts) < 8:
        raise ValueError(f"Invalid account format. Expected at least 8 fields, got {len(parts)}")
    
    return {
        'username': parts[0],
        'password': parts[1],
        'email': parts[2],
        'token': parts[3],
        '2fa_secret': parts[4],
        'country': parts[5],
        'cookies_raw': parts[6],
        'additional_info': parts[7] if len(parts) > 7 else ''
    }

def parse_instaccountsmanager_format(account_line: str) -> Dict[str, Any]:
    """Parse InstAccountsManager format: username:password|user_agent|device_info|cookies||email:email_password"""
    try:
        # First split by || to separate main data from email
        main_split = account_line.strip().split('||')
        
        if len(main_split) < 2:
            raise ValueError(f"Invalid InstAccountsManager format. Expected 2 parts separated by ||, got {len(main_split)}")
        
        # Parse the main part (everything before ||)
        main_part = main_split[0]
        main_parts = main_part.split('|')
        
        if len(main_parts) < 4:
            raise ValueError(f"Invalid main part format. Expected 4 parts separated by |, got {len(main_parts)}")
        
        # Parse login credentials
        login_part = main_parts[0]
        if ':' not in login_part:
            raise ValueError("Invalid login format. Expected username:password")
        
        username, password = login_part.split(':', 1)
        
        # Extract user agent and device info (parts 1 and 2)
        user_agent = main_parts[1]
        device_info = main_parts[2]
        
        # Parse cookies (part 3)
        cookies_raw = main_parts[3]
        
        # Parse email credentials
        email_part = main_split[1]
        if ':' not in email_part:
            raise ValueError("Invalid email format. Expected email:password")
        
        email, email_password = email_part.split(':', 1)
        
        return {
            'username': username.strip(),
            'password': password.strip(),
            'user_agent': user_agent.strip(),
            'device_info': device_info.strip(),
            'email': email.strip(),
            'email_password': email_password.strip(),
            'cookies_raw': cookies_raw.strip(),
            'format': 'instaccountsmanager'
        }
        
    except Exception as e:
        logger.error(f"Error parsing InstAccountsManager format: {e}")
        raise

def parse_instagram_cookies_from_headers(cookies_raw: str) -> List[Dict[str, Any]]:
    """Parse Instagram cookies from the InstAccountsManager header format."""
    cookies = []
    
    # This format contains HTTP headers, we need to extract cookies
    # Look for Authorization, IG-U-DS-USER-ID, etc.
    
    # Split by semicolon and process each part
    parts = cookies_raw.split(';')
    
    for part in parts:
        part = part.strip()
        
        # Handle Authorization header
        if part.startswith('Authorization='):
            auth_value = part.replace('Authorization=', '')
            cookies.append({
                'name': 'Authorization',
                'value': auth_value,
                'domain': '.instagram.com',
                'path': '/',
                'secure': True,
                'httpOnly': True,
                'sameSite': 'Lax',
                'expires': int(time.time() + 365 * 24 * 60 * 60)
            })
        
        # Handle other IG headers as cookies
        elif part.startswith('IG-'):
            if '=' in part:
                name, value = part.split('=', 1)
                cookies.append({
                    'name': name,
                    'value': value,
                    'domain': '.instagram.com',
                    'path': '/',
                    'secure': True,
                    'httpOnly': False,
                    'sameSite': 'Lax',
                    'expires': int(time.time() + 365 * 24 * 60 * 60)
                })
        
        # Handle X-MID
        elif part.startswith('X-MID='):
            value = part.replace('X-MID=', '')
            cookies.append({
                'name': 'mid',
                'value': value,
                'domain': '.instagram.com',
                'path': '/',
                'secure': True,
                'httpOnly': False,
                'sameSite': 'Lax',
                'expires': int(time.time() + 365 * 24 * 60 * 60)
            })
        
        # Handle X-IG-WWW-Claim
        elif part.startswith('X-IG-WWW-Claim='):
            value = part.replace('X-IG-WWW-Claim=', '')
            cookies.append({
                'name': 'ig_www_claim',
                'value': value,
                'domain': '.instagram.com',
                'path': '/',
                'secure': True,
                'httpOnly': False,
                'sameSite': 'Lax',
                'expires': int(time.time() + 365 * 24 * 60 * 60)
            })
    
    return cookies

def parse_instagram_cookies(cookies_raw: str) -> List[Dict[str, Any]]:
    """Parse Instagram cookies from the raw cookie string."""
    cookies = []
    
    # Split by semicolon and process each cookie
    cookie_pairs = cookies_raw.split('; ')
    
    for cookie_pair in cookie_pairs:
        if '=' not in cookie_pair:
            continue
            
        name, value = cookie_pair.split('=', 1)
        name = name.strip()
        value = value.strip().strip('"')
        
        # Determine domain based on cookie name
        if name in ['csrftoken', 'sessionid', 'ds_user_id', 'rur', 'mid', 'ig_did', 'ig_nrcb']:
            domain = '.instagram.com'
        else:
            domain = '.instagram.com'  # Default domain
        
        # Create cookie dict
        cookie = {
            'name': name,
            'value': value,
            'domain': domain,
            'path': '/',
            'secure': True,
            'httpOnly': False,  # Most IG cookies aren't httpOnly
            'sameSite': 'Lax',
            'expires': int(time.time() + 365 * 24 * 60 * 60)  # 1 year from now
        }
        
        # Special handling for specific cookies
        if name == 'sessionid':
            cookie['httpOnly'] = True
        
        cookies.append(cookie)
    
    return cookies

def convert_to_netscape_format(cookies: List[Dict[str, Any]]) -> str:
    """Convert cookies to Netscape format for yt-dlp."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated file!  Do not edit.",
        ""
    ]
    
    for cookie in cookies:
        # Format: domain flag path secure expiry name value
        domain = cookie['domain']
        flag = 'TRUE'  # Include subdomains
        path = cookie['path']
        secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
        expiry = str(cookie.get('expires', int(time.time() + 365 * 24 * 60 * 60)))
        name = cookie['name']
        value = cookie['value']
        
        line = f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}"
        lines.append(line)
    
    return '\n'.join(lines)

def import_account_cookies(account_line: str, output_file: Path) -> bool:
    """Import cookies from an account line and save to file."""
    try:
        # Parse account data
        account_data = parse_account_line(account_line)
        logger.info(f"Importing cookies for account: {account_data['username']}")
        
        # Parse cookies
        cookies = parse_instagram_cookies(account_data['cookies_raw'])
        logger.info(f"Parsed {len(cookies)} cookies")
        
        # Convert to Netscape format
        netscape_cookies = convert_to_netscape_format(cookies)
        
        # Save to file
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(netscape_cookies)
        
        logger.info(f"Cookies saved to {output_file}")
        
        # Also save account info for reference
        info_file = output_file.parent / 'account_info.json'
        with open(info_file, 'w', encoding='utf-8') as f:
            json.dump({
                'username': account_data['username'],
                'email': account_data['email'],
                '2fa_secret': account_data['2fa_secret'],
                'country': account_data['country'],
                'import_time': time.time()
            }, f, indent=2)
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to import cookies: {str(e)}")
        return False

if __name__ == "__main__":
    # Example usage
    account_line = "denise.toledo1166;a558hqpf2ytcz3938;czkw5ktsv@storebanme.com;eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...;6C4O BC44 5WVU FE64 CN4K U3VG QTDT B3JY;USA;rur=\"VLL\\05475000915390\\0541780592826:01fe710a3325ed6c8eb4f14b3485e1407dddce18998d136aa0acc908830c11822ce1b3cc\"; ig_nrcb=1; sessionid=75000915390%3ApFfNzI7b845Q7g%3A21%3AAYfIjS1hXQzDi8IdXVrladU_cttYk0YcTICgahvp7A; mid=aEB8kAALAAFy7sGQGkQ5LjnpUGud; ig_did=3DBFFE0E-BA6D-42D0-85DA-7665CF193314; datr=kHxAaL0oDYZ_8ZhWpCIUGOAh; ds_user_id=75000915390; wd=1522x559; csrftoken=X6x4EOQE9hPu99gPgQ1Qkw;⚠️WARNING⚠️..."
    
    output_file = Path('/app/cookies/instagram_cookies.txt')
    success = import_account_cookies(account_line, output_file)
    
    if success:
        print("Cookies imported successfully!")
    else:
        print("Failed to import cookies") 