#!/usr/bin/env python3
"""Helper script to format InstAccountsManager accounts for import."""

def format_account_line(raw_line: str) -> str:
    """Convert full account format to simplified format for import."""
    # Your full format:
    # example_user:example_password|Instagram 345.0.0.48.95 Android (...)|android-device-id;uuid-a|Authorization=Bearer <instagram_bearer_token>|...|<email_address>:<email_password>
    
    parts = raw_line.split('|')
    
    if len(parts) < 6:
        raise ValueError(f"Expected at least 6 parts, got {len(parts)}")
    
    # Extract parts we need:
    login = parts[0]  # example_user:example_password
    # Skip device info (parts[1])
    # Skip device IDs (parts[2])
    
    # Find the Authorization part (should start with "Authorization=")
    auth_start_idx = None
    for i, part in enumerate(parts):
        if part.startswith('Authorization='):
            auth_start_idx = i
            break
    
    if auth_start_idx is None:
        raise ValueError("Could not find Authorization header")
    
    # Find email (last part)
    email = parts[-1]
    
    # Combine all cookie parts from Authorization to email (exclusive)
    cookie_parts = parts[auth_start_idx:-1]
    cookies = '|'.join(cookie_parts)
    
    # Format: login:password||cookies||email:emailpassword
    return f"{login}||{cookies}||{email}"

def main():
    """Process accounts from input file."""
    print("🔧 InstAccountsManager Account Formatter")
    print("=" * 50)
    
    # Read raw accounts
    raw_file = 'secrets/raw_accounts.txt'
    output_file = 'secrets/instmanager_accounts.txt'
    
    try:
        with open(raw_file, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        print(f"❌ {raw_file} not found!")
        print("\nCreate secrets/raw_accounts.txt with your full account data:")
        print("Example:")
        print("example_user:example_password|Instagram 345.0.0.48.95 Android...|android-device-id...|Authorization=Bearer <instagram_bearer_token>|...|<email_address>:<email_password>")
        return
    
    formatted_lines = []
    success_count = 0
    
    for line_num, raw_line in enumerate(raw_lines, 1):
        raw_line = raw_line.strip()
        if not raw_line or raw_line.startswith('#'):
            continue
        
        try:
            formatted = format_account_line(raw_line)
            formatted_lines.append(formatted)
            success_count += 1
            print(f"✅ Formatted account {success_count}")
        except Exception as e:
            print(f"❌ Error on line {line_num}: {e}")
    
    # Save formatted accounts
    if formatted_lines:
        with open(output_file, 'w', encoding='utf-8') as f:
            for line in formatted_lines:
                f.write(line + '\n')
        
        print(f"\n✅ Successfully formatted {success_count} accounts")
        print(f"📁 Saved to: {output_file}")
        print("\n🔄 Next steps:")
        print("1. Run: make import-instmanager")
        print("2. Run: make create-preauth")
        print("3. Run: make up")
    else:
        print("❌ No accounts were formatted")

if __name__ == "__main__":
    main()
