#!/usr/bin/env python3
"""Import cookies from purchased Instagram account."""
import sys
import os
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.instagram_video_bot.utils.cookie_importer import import_account_cookies

# Replace this with your actual account line
ACCOUNT_LINE = """denise.toledo1166;a558hqpf2ytcz3938;czkw5ktsv@storebanme.com;eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJlIjoicHprS1pUOGxNM3lpTEhIMXBTRVZxeFcySTN5aUZ5QXdvMU9KTXlNNkZKcVpGYXl6Rm1XR3JSMUlJM3lqWjFjMkRhTUtxM1c2TW1BT0Z6cGpwUUFBR2FObUVKeWpyeHkyR1JiMU0wMVRBS3FpWndPMkpJT0taMDFYSTJ1WkZ3UzVGbVdTbko5WEgyQWlxeUwySXpTU00wa1hySk1pWjFNYkdRVjVNMU0ybmFNQUlSeGxveGNPcklNM0xhTWtaeHkySWFNZHF4MVhaS0loRnpnYkdIZ2twMjVYRVVNUHEzdWZESHEwb1JXRUZRQU1IU3BrcFFXV29SZmxyS3VKcTJXZHAwZ2VvSFdVcVFJam53MDkifQ.--zCQGa96YpffT7TGfy8B3bBM9rhkGwERSYyfQqa4aI;6C4O BC44 5WVU FE64 CN4K U3VG QTDT B3JY;USA;rur="VLL\05475000915390\0541780592826:01fe710a3325ed6c8eb4f14b3485e1407dddce18998d136aa0acc908830c11822ce1b3cc"; ig_nrcb=1; sessionid=75000915390%3ApFfNzI7b845Q7g%3A21%3AAYfIjS1hXQzDi8IdXVrladU_cttYk0YcTICgahvp7A; mid=aEB8kAALAAFy7sGQGkQ5LjnpUGud; ig_did=3DBFFE0E-BA6D-42D0-85DA-7665CF193314; datr=kHxAaL0oDYZ_8ZhWpCIUGOAh; ds_user_id=75000915390; wd=1522x559; csrftoken=X6x4EOQE9hPu99gPgQ1Qkw;⚠️WARNING⚠️ Use Proxy/VPN from the location highlighted in the country field to avoid 'incorrect password' issues or common login issues, to use the email add the passwordmail after the last slash of the url 'https://tmailor.com/token/check/#accesstoken/eyJ0eXAiOi...' + passwordmail and access the site"""

# Determine output file path
if os.path.exists('/.dockerenv'):
    # Running in Docker
    output_file = Path('/app/cookies/instagram_cookies.txt')
else:
    # Running on host - use the same path as settings.py expects
    output_file = Path('cookies/instagram_cookies.txt')

# Handle case where instagram_cookies.txt is a directory
old_dir = Path('instagram_cookies.txt')
if old_dir.exists() and old_dir.is_dir():
    print(f"⚠️  Found directory named 'instagram_cookies.txt'")
    print(f"You should remove it with: rm -rf instagram_cookies.txt")
    print(f"Using cookies directory instead: {output_file}")

print(f"Importing cookies to: {output_file}")
success = import_account_cookies(ACCOUNT_LINE, output_file)

if success:
    print("✅ Cookies imported successfully!")
    print(f"Cookies saved to: {output_file}")
    
    # Also show the account info
    info_file = output_file.parent / 'account_info.json'
    if info_file.exists():
        print(f"Account info saved to: {info_file}")
else:
    print("❌ Failed to import cookies")
    sys.exit(1) 