#!/usr/bin/env python3
"""Check if Instagram cookies are still valid."""
import sys
import requests
import base64
import json
from pathlib import Path
from typing import Dict, List, Tuple

def parse_cookies_file(cookies_file: Path) -> dict:
    """Parse Netscape format cookies file."""
    cookies = {}
    
    if not cookies_file.exists():
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
        print(f"❌ Error reading {cookies_file}: {e}")
    
    return cookies

def extract_sessionid_from_jwt(auth_token: str) -> str:
    """Extract sessionid from Instagram JWT Authorization token."""
    try:
        # Instagram JWT format: "Bearer IGT:2:base64_payload"
        if not auth_token.startswith('Bearer IGT:2:'):
            return ""
        
        # Extract base64 payload
        jwt_payload = auth_token.replace('Bearer IGT:2:', '')
        
        # Decode base64
        decoded = base64.b64decode(jwt_payload + '==').decode('utf-8')
        
        # Parse JSON
        data = json.loads(decoded)
        
        # Extract sessionid
        return data.get('sessionid', '')
        
    except Exception:
        return ""

def check_instagram_session(cookies: dict, account_name: str = "") -> bool:
    """Check if Instagram session is valid."""
    # Check for modern auth format (Authorization Bearer + IG-U-DS-USER-ID)
    has_modern_auth = 'Authorization' in cookies and 'IG-U-DS-USER-ID' in cookies
    
    # Check for legacy auth format (sessionid + ds_user_id)
    has_legacy_auth = 'sessionid' in cookies
    
    if not (has_modern_auth or has_legacy_auth):
        return False
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    # Add Authorization header if we have modern auth
    if has_modern_auth:
        headers['Authorization'] = cookies['Authorization']
    
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
                return True
            elif 'login' in content and 'password' in content:
                return False
            else:
                # Status unclear - might be rate limited
                return False
        else:
            return False
            
    except Exception as e:
        if account_name:
            print(f"⚠️  Error checking {account_name}: {e}")
        return False

def get_account_cookie_files() -> List[Tuple[str, Path]]:
    """Get list of account cookie files."""
    # Check if running in Docker
    if Path('/.dockerenv').exists():
        cookies_dir = Path('/app/cookies')
        preauth_file = Path('/app/accounts_preauth.txt')
    else:
        cookies_dir = Path('cookies')
        preauth_file = Path('accounts_preauth.txt')
    
    account_files = []
    
    # Check if we're in multi-account mode
    if preauth_file.exists():
        # Multi-account mode - check individual account files
        try:
            with open(preauth_file, 'r') as f:
                for line in f:
                    username = line.strip()
                    if username and not username.startswith('#'):
                        cookie_file = cookies_dir / f"{username}_cookies.txt"
                        account_files.append((username, cookie_file))
        except Exception as e:
            print(f"❌ Error reading {preauth_file}: {e}")
    else:
        # Single account mode - check the main cookies file
        cookie_file = cookies_dir / 'instagram_cookies.txt'
        account_files.append(("main", cookie_file))
    
    return account_files

def check_single_account(username: str, cookie_file: Path) -> Dict[str, any]:
    """Check a single account's cookies."""
    result = {
        'username': username,
        'cookie_file': cookie_file,
        'exists': False,
        'cookie_count': 0,
        'has_sessionid': False,
        'has_csrftoken': False,
        'has_ds_user_id': False,
        'has_authorization': False,
        'has_ig_user_id': False,
        'auth_format': 'none',
        'session_valid': False
    }
    
    if not cookie_file.exists():
        return result
    
    result['exists'] = True
    cookies = parse_cookies_file(cookie_file)
    result['cookie_count'] = len(cookies)
    
    # Check for modern Instagram auth format
    result['has_authorization'] = 'Authorization' in cookies
    result['has_ig_user_id'] = 'IG-U-DS-USER-ID' in cookies
    
    # Check for legacy auth format
    result['has_sessionid'] = 'sessionid' in cookies
    result['has_csrftoken'] = 'csrftoken' in cookies
    result['has_ds_user_id'] = 'ds_user_id' in cookies
    
    # Determine auth format
    if result['has_authorization'] and result['has_ig_user_id']:
        result['auth_format'] = 'modern'
        # Extract sessionid from JWT for verification
        if result['has_authorization']:
            sessionid = extract_sessionid_from_jwt(cookies['Authorization'])
            result['extracted_sessionid'] = bool(sessionid)
    elif result['has_sessionid']:
        result['auth_format'] = 'legacy'
    else:
        result['auth_format'] = 'none'
    
    # Test session if we have valid auth
    if result['auth_format'] != 'none':
        result['session_valid'] = check_instagram_session(cookies, username)
    
    return result

def main():
    """Main function."""
    print("🔍 Instagram Cookie Checker")
    print("=" * 50)
    
    account_files = get_account_cookie_files()
    
    if not account_files:
        print("❌ No account files found")
        print("\n💡 Make sure you have either:")
        print("- accounts_preauth.txt (multi-account mode)")
        print("- cookies/instagram_cookies.txt (single-account mode)")
        sys.exit(1)
    
    if len(account_files) == 1 and account_files[0][0] == "main":
        print("📱 Single Account Mode")
    else:
        print(f"📱 Multi-Account Mode ({len(account_files)} accounts)")
    
    print()
    
    all_results = []
    valid_accounts = 0
    
    for username, cookie_file in account_files:
        result = check_single_account(username, cookie_file)
        all_results.append(result)
        
        if len(account_files) > 1:
            print(f"🔍 Checking: {username}")
        
        if not result['exists']:
            print(f"  ❌ Cookie file not found: {cookie_file}")
            continue
        
        print(f"  📊 Found {result['cookie_count']} cookies")
        
        # Show auth format and status
        if result['auth_format'] == 'modern':
            print(f"  🆕 Modern Instagram auth format")
            print(f"  ✅ Authorization Bearer token")
            print(f"  ✅ IG-U-DS-USER-ID: {result['has_ig_user_id']}")
            if result.get('extracted_sessionid'):
                print(f"  ✅ SessionID found in JWT token")
        elif result['auth_format'] == 'legacy':
            print(f"  🔄 Legacy Instagram auth format")
            status_sessionid = "✅" if result['has_sessionid'] else "❌"
            status_csrf = "✅" if result['has_csrftoken'] else "❌"
            status_userid = "✅" if result['has_ds_user_id'] else "❌"
            print(f"  {status_sessionid} sessionid")
            print(f"  {status_csrf} csrftoken") 
            print(f"  {status_userid} ds_user_id")
        else:
            print(f"  ❌ No valid authentication format found")
        
        if result['auth_format'] != 'none':
            if result['session_valid']:
                print(f"  ✅ Session is valid")
                valid_accounts += 1
            else:
                print(f"  ❌ Session is expired/invalid")
        else:
            print(f"  ❌ Cannot test session - no auth cookies")
        
        if len(account_files) > 1:
            print()
    
    # Summary
    print("=" * 50)
    if len(account_files) > 1:
        print(f"📊 Summary: {valid_accounts}/{len(account_files)} accounts have valid sessions")
        
        if valid_accounts == 0:
            print("\n❌ No accounts have valid sessions!")
        elif valid_accounts < len(account_files):
            print(f"\n⚠️  {len(account_files) - valid_accounts} accounts need fresh cookies")
        else:
            print("\n✅ All accounts have valid sessions!")
    else:
        result = all_results[0]
        if result['session_valid']:
            print("\n✅ Cookies are working! Bot should be able to download videos.")
        else:
            print("\n❌ Cookies are expired or invalid!")
    
    if valid_accounts == 0:
        print("\n💡 To fix this:")
        print("1. Get fresh cookies from InstAccountsManager")
        print("2. Run: make import-instmanager")
        print("3. Restart your bot")
        sys.exit(1)

if __name__ == "__main__":
    main() 