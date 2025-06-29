#!/usr/bin/env python3
"""Manual cookie refresh script."""
import sys
from pathlib import Path

def main():
    """Main function."""
    print("🔄 Manual Cookie Refresh")
    print("=" * 40)
    
    print("\n📋 To refresh your Instagram cookies:")
    print("1. Get fresh account data from your provider")
    print("2. Update the account.txt file with new data")
    print("3. Run: python3 import_cookies.py")
    print("4. Restart your bot: docker-compose restart")
    
    print("\n🔍 To check current cookie status:")
    print("   python3 check_cookies.py")
    
    print("\n⚠️  Note: Automatic cookie refresh has been disabled")
    print("   to prevent infinite login loops. You must manually")
    print("   import fresh cookies when they expire.")
    
    # Check if account.txt exists
    account_file = Path('account.txt')
    if account_file.exists():
        print(f"\n✅ Found account.txt file")
        
        # Ask if user wants to import now
        try:
            response = input("\n❓ Import cookies from account.txt now? (y/n): ").lower().strip()
            if response in ['y', 'yes']:
                print("\n🔄 Importing cookies...")
                import subprocess
                result = subprocess.run([sys.executable, 'import_cookies.py'], 
                                      capture_output=True, text=True)
                
                if result.returncode == 0:
                    print("✅ Cookies imported successfully!")
                    print("\n🔄 Now restart your bot:")
                    print("   docker-compose restart")
                else:
                    print("❌ Failed to import cookies:")
                    print(result.stderr)
            else:
                print("⏭️  Skipping import")
        except KeyboardInterrupt:
            print("\n⏹️  Cancelled")
    else:
        print(f"\n❌ account.txt not found")
        print("   Create it with your account data first")

if __name__ == "__main__":
    main() 