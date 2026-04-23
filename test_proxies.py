#!/usr/bin/env python3
"""Test all configured proxies and show account assignments."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.instagram_video_bot.utils.proxy_manager import (
    get_proxy_for_account,
    proxy_manager,
    test_all_proxies as run_all_proxy_checks,
)

def main():
    """Test all proxies and show account assignments."""
    print("🌐 Proxy Configuration Test")
    print("=" * 50)
    
    proxies = proxy_manager.get_all_proxies()
    
    if not proxies:
        print("❌ No proxies configured!")
        print("\n💡 Configure proxies using one of these methods:")
        print("1. Single proxy (legacy):")
        print("   PROXY_HOST=1.2.3.4")
        print("   PROXY_PORT=8080")
        print("   PROXY_USERNAME=user")
        print("   PROXY_PASSWORD=pass")
        print()
        print("2. Multiple proxies (recommended format):")
        print("   PROXY_1=user:pass@1.2.3.4:8080")
        print("   PROXY_2=user2:pass2@5.6.7.8:8080")
        print("   ...")
        print()
        print("3. Proxy list:")
        print("   PROXY_LIST=\"")
        print("   user:pass@1.2.3.4:8080")
        print("   user2:pass2@5.6.7.8:8080")
        print("   \"")
        return
    
    print(f"Found {len(proxies)} configured proxies:\n")
    
    # Test all proxies
    print("🔍 Testing proxy connectivity...")
    results = run_all_proxy_checks()
    
    working_count = 0
    for i, (proxy, is_working) in enumerate(results, 1):
        status = "✅ Working" if is_working else "❌ Failed"
        print(f"  {i}. {proxy.host}:{proxy.port} - {status}")
        if is_working:
            working_count += 1
    
    print(f"\n📊 Summary: {working_count}/{len(proxies)} proxies working")
    
    if working_count == 0:
        print("❌ No working proxies found! Check your proxy configuration.")
        return
    
    # Show account assignments
    accounts_file = Path('accounts_preauth.txt')
    if accounts_file.exists():
        print(f"\n👥 Account → Proxy Assignments:")
        print("-" * 40)
        
        try:
            with open(accounts_file, 'r') as f:
                for line in f:
                    username = line.strip()
                    if username and not username.startswith('#'):
                        proxy = get_proxy_for_account(username)
                        if proxy:
                            print(f"  {username} → {proxy.host}:{proxy.port}")
                        else:
                            print(f"  {username} → No proxy")
        except Exception as e:
            print(f"❌ Error reading accounts file: {e}")
    else:
        print(f"\n⚠️  No accounts_preauth.txt found")
        print("   Create this file with your account usernames to see assignments")
    
    print(f"\n💡 Tips:")
    print("- Each account gets consistently assigned to the same proxy")
    print("- This avoids IP switching detection by Instagram")
    print("- Test proxies regularly as they may go offline")
    print("- Use residential proxies for best results")

if __name__ == "__main__":
    main()
