#!/usr/bin/env python3
"""Import cookies from purchased Instagram account."""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.instagram_video_bot.utils.cookie_importer import import_account_cookies

# Replace this with your actual account line
ACCOUNT_LINE = os.getenv('ACCOUNT_LINE')

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