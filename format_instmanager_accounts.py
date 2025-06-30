#!/usr/bin/env python3
"""Helper script to format InstAccountsManager accounts for import."""

def format_account_line(raw_line: str) -> str:
    """Convert full account format to simplified format for import."""
    # Your full format:
    # ms.stevenbaker682510:tGeltLAc02KDNxI|Instagram 345.0.0.48.95 Android (31/12; 120dpi; 1080x2162; Samsung; SM-A510F; a5xelte; qcom; en_US; 634108168)|android-1c9f3387a1a2a978;160b1ca9-305b-485b-3ef2-cd21165ec318;487714a2-9663-4f9a-3d8b-2bf61007f950;7cc09762-2be7-446a-aead-7904d265f530|Authorization=Bearer IGT:...|...|xonoxtsm@wildbmail.com:neoszgkeA!9944
    
    parts = raw_line.split('|')
    
    if len(parts) < 6:
        raise ValueError(f"Expected at least 6 parts, got {len(parts)}")
    
    # Extract parts we need:
    login = parts[0]  # ms.stevenbaker682510:tGeltLAc02KDNxI
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
    raw_file = 'raw_accounts.txt'
    output_file = 'instmanager_accounts.txt'
    
    try:
        with open(raw_file, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        print(f"❌ {raw_file} not found!")
        print("\nCreate raw_accounts.txt with your full account data:")
        print("Example:")
        print("ms.stevenbaker682510:tGeltLAc02KDNxI|Instagram 345.0.0.48.95 Android...|android-1c9f...|Authorization=Bearer IGT:...|...|xonoxtsm@wildbmail.com:neoszgkeA!9944")
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