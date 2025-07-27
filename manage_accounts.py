#!/usr/bin/env python3
"""Manage multiple Instagram accounts."""
import sys
import asyncio
from pathlib import Path
from tabulate import tabulate

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.instagram_video_bot.utils.account_manager import get_account_manager

def show_status():
    """Show status of all accounts."""
    manager = get_account_manager()
    status = manager.get_status()
    
    print("\n📊 Account Status")
    print("=" * 50)
    print(f"Total accounts: {status['total_accounts']}")
    print(f"Available: {status['available_accounts']}")
    print(f"Banned: {status['banned_accounts']}")
    print(f"Current: {status['current_account'] or 'None'}")
    
    # Create table of accounts
    headers = ["Username", "Status", "Proxy", "Last Used", "Has Session"]
    rows = []
    
    for acc in status['accounts']:
        status_emoji = "❌" if acc['is_banned'] else "✅"
        has_session = "Yes" if acc['has_session'] else "No"
        last_used = acc['last_used'] or "Never"
        proxy = acc.get('proxy', 'None')
        
        rows.append([
            acc['username'],
            status_emoji,
            proxy,
            last_used,
            has_session
        ])
    
    print("\n" + tabulate(rows, headers=headers, tablefmt="grid"))

def setup_all_accounts():
    """Setup all accounts by logging in and generating cookies."""
    manager = get_account_manager()
    
    print("\n🔧 Setting up all accounts")
    print("=" * 50)
    
    success_count = 0
    
    for i, account in enumerate(manager.accounts):
        if account.is_banned:
            print(f"\n[{i+1}/{len(manager.accounts)}] Skipping banned account: {account.username}")
            continue
            
        print(f"\n[{i+1}/{len(manager.accounts)}] Setting up: {account.username}")
        
        if manager.setup_account(account):
            success_count += 1
            print(f"✅ Success: {account.username}")
            
            # Wait between accounts to avoid detection
            if i < len(manager.accounts) - 1:
                print("⏳ Waiting 30 seconds before next account...")
                import time
                time.sleep(30)
        else:
            print(f"❌ Failed: {account.username}")
    
    print(f"\n✅ Successfully setup {success_count}/{len(manager.accounts)} accounts")

def rotate_account():
    """Manually rotate to next account."""
    manager = get_account_manager()
    
    print("\n🔄 Rotating account")
    print("=" * 50)
    
    current = manager.current_account
    if current:
        print(f"Current account: {current.username}")
    
    if manager.rotate_account():
        print(f"✅ Rotated to: {manager.current_account.username}")
    else:
        print("❌ Failed to rotate account")

def reset_banned(username: str = None):
    """Reset banned status for account(s)."""
    manager = get_account_manager()
    
    if username:
        # Reset specific account
        for account in manager.accounts:
            if account.username == username:
                account.is_banned = False
                manager._save_state()
                print(f"✅ Reset banned status for: {username}")
                return
        print(f"❌ Account not found: {username}")
    else:
        # Reset all accounts
        for account in manager.accounts:
            account.is_banned = False
        manager._save_state()
        print(f"✅ Reset banned status for all accounts")

def warmup_account(username: str = None):
    """Warm up specific account or current account."""
    import subprocess
    
    manager = get_account_manager()
    
    if username:
        # Find and setup specific account
        for account in manager.accounts:
            if account.username == username:
                if not manager.setup_account(account):
                    print(f"❌ Failed to setup account: {username}")
                    return
                break
        else:
            print(f"❌ Account not found: {username}")
            return
    elif not manager.current_account:
        print("❌ No current account set. Use 'setup' first.")
        return
    
    print(f"🔥 Warming up account: {manager.current_account.username}")
    
    # Run warmup script
    result = subprocess.run([sys.executable, 'warmup_account.py'])
    
    if result.returncode == 0:
        print("✅ Warmup completed successfully")
    else:
        print("❌ Warmup failed")

def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage Instagram accounts")
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Status command
    subparsers.add_parser('status', help='Show account status')
    
    # Setup command
    subparsers.add_parser('setup', help='Setup all accounts')
    
    # Rotate command
    subparsers.add_parser('rotate', help='Rotate to next account')
    
    # Reset command
    reset_parser = subparsers.add_parser('reset', help='Reset banned status')
    reset_parser.add_argument('username', nargs='?', help='Username to reset (all if not specified)')
    
    # Warmup command
    warmup_parser = subparsers.add_parser('warmup', help='Warm up account')
    warmup_parser.add_argument('username', nargs='?', help='Username to warm up (current if not specified)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Check if accounts file exists
    accounts_file = Path('accounts.txt')
    
    if not accounts_file.exists():
        print("❌ No accounts file found!")
        print("\nCreate accounts.txt with the following format:")
        print("   Format: username|password|totp_secret")
        print("   Example:")
        print("   samosirarlene|@encore05|4NPFTMJUVP7NPXPZC3MDZ26SVZTW5GUL")
        print("   john_doe|mypassword123|ABCD1234EFGH5678IJKL")
        print()
        print("💡 Tips:")
        print("   - Each line represents one account")
        print("   - Proxies are automatically assigned from PROXIES in .env")
        print("   - Sessions are stored in sessions/ directory")
        sys.exit(1)
    
    # Execute command
    if args.command == 'status':
        show_status()
    elif args.command == 'setup':
        setup_all_accounts()
    elif args.command == 'rotate':
        rotate_account()
    elif args.command == 'reset':
        reset_banned(args.username)
    elif args.command == 'warmup':
        warmup_account(args.username)

if __name__ == "__main__":
    main() 