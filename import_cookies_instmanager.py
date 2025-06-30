#!/usr/bin/env python3
"""Import cookies from InstAccountsManager format accounts."""
import sys
import os
import json
import time
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.instagram_video_bot.utils.cookie_importer import parse_instaccountsmanager_format, parse_instagram_cookies_from_headers, convert_to_netscape_format

def import_instmanager_account(account_line: str, username: str) -> bool:
    """Import cookies from InstAccountsManager format and save to specific username file."""
    try:
        # Parse account data
        account_data = parse_instaccountsmanager_format(account_line)
        print(f"✅ Parsed account: {account_data['username']}")
        
        # Parse cookies from headers
        cookies = parse_instagram_cookies_from_headers(account_data['cookies_raw'])
        print(f"✅ Parsed {len(cookies)} cookies")
        
        # Convert to Netscape format
        netscape_cookies = convert_to_netscape_format(cookies)
        
        # Save cookies to specific username file
        cookies_dir = Path('cookies')
        cookies_dir.mkdir(exist_ok=True)
        
        cookies_file = cookies_dir / f"{username}_cookies.txt"
        
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write(netscape_cookies)
        
        print(f"✅ Cookies saved to {cookies_file}")
        
        # Save account info for reference
        info_file = cookies_dir / f"{username}_account_info.json"
        with open(info_file, 'w', encoding='utf-8') as f:
            json.dump({
                'username': account_data['username'],
                'password': account_data['password'],
                'email': account_data['email'],
                'email_password': account_data['email_password'],
                'format': 'instaccountsmanager',
                'import_time': time.time()
            }, f, indent=2)
        
        print(f"✅ Account info saved to {info_file}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to import cookies: {str(e)}")
        return False

def main():
    """Main function to import multiple accounts."""
    accounts_file = Path('instmanager_accounts.txt')
    
    if not accounts_file.exists():
        print("❌ instmanager_accounts.txt file not found!")
        print("\nCreate instmanager_accounts.txt with your accounts in format:")
        print("login:password||cookies||mail:mailpassword")
        print("\nExample:")
        print("ms.stevenbaker682510:tGeltLAc02KDNxI||Authorization=Bearer IGT:...|IG-U-DS-USER-ID=...|..||xonoxtsm@wildbmail.com:neoszgkeA!9944")
        sys.exit(1)
    
    success_count = 0
    total_count = 0
    
    with open(accounts_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            total_count += 1
            print(f"\n--- Processing account {total_count} (line {line_num}) ---")
            
            try:
                # Parse to get username for filename
                account_data = parse_instaccountsmanager_format(line)
                username = account_data['username']
                
                if import_instmanager_account(line, username):
                    success_count += 1
                    print(f"✅ Successfully imported {username}")
                else:
                    print(f"❌ Failed to import {username}")
                    
            except Exception as e:
                print(f"❌ Error processing line {line_num}: {e}")
    
    print(f"\n{'='*50}")
    print(f"Import completed: {success_count}/{total_count} accounts successful")
    
    if success_count > 0:
        print(f"\n✅ Successfully imported {success_count} accounts!")
        print("\nCookie files created in cookies/ directory:")
        cookies_dir = Path('cookies')
        for cookie_file in cookies_dir.glob('*_cookies.txt'):
            print(f"  - {cookie_file}")
        
        print("\n🔄 Next steps:")
        print("1. Create accounts_preauth.txt with usernames (no passwords needed)")
        print("2. Start the bot: make up")
        print("3. Check logs: make logs")
    else:
        print("❌ No accounts were successfully imported")

if __name__ == "__main__":
    main() 