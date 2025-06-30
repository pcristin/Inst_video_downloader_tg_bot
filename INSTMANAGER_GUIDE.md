# InstAccountsManager Accounts Guide

## Overview

You have 10 accounts in InstAccountsManager format with pre-existing cookies. This format is:
```
login:password||cookies||mail:mailpassword
```

This guide shows how to import these accounts and use them with the bot.

## Format Details

Your account format:
```
ms.stevenbaker682510:tGeltLAc02KDNxI|Instagram 345.0.0.48.95 Android (31/12; 120dpi; 1080x2162; Samsung; SM-A510F; a5xelte; qcom; en_US; 634108168)|android-1c9f3387a1a2a978;160b1ca9-305b-485b-3ef2-cd21165ec318;487714a2-9663-4f9a-3d8b-2bf61007f950;7cc09762-2be7-446a-aead-7904d265f530|Authorization=Bearer IGT:2:eyJkc191c2VyX2lkIjoiNzE2NzYzMTYwMDAiLCJzZXNzaW9uaWQiOiI3MTY3NjMxNjAwMCUzQVUxTG1hUm53RElZYlB2JTNBMTAlM0FBWWM1eUtKRldpejlyUVp4T2QxNEJlQUxiTmZTMHprdFA5bm1VemZoVXcifQ==;IG-U-DS-USER-ID=71676316000;IG-INTENDED-USER-ID=71676316000;IG-U-RUR=RVA,71676316000,1781795419:01fe0e0bb24e5687f0db3f88fe5eca64bc63eb94d828e820487cceda0574e2b85ce3c258;X-MID=aCS6IAABAAFjvtUwad4WoZAEUUhl;X-IG-WWW-Claim=hmac.AR3gO2TnlQGiln15xgihbMcch3sOvuNYuIFo-ur_1ssQ8Ub1;||xonoxtsm@wildbmail.com:neoszgkeA!9944
```

Parts:
1. **Login**: `ms.stevenbaker682510:tGeltLAc02KDNxI`
2. **Device Info**: `Instagram 345.0.0.48.95 Android...` (we'll ignore this)
3. **Device IDs**: `android-1c9f3387a1a2a978;160b1ca9...` (we'll ignore this)
4. **Cookies**: `Authorization=Bearer IGT:...|IG-U-DS-USER-ID=...|...`
5. **Email**: `xonoxtsm@wildbmail.com:neoszgkeA!9944`

## Step-by-Step Setup

### 1. Create Accounts File

Create `instmanager_accounts.txt` with your 10 accounts:

```bash
# Create the file
touch instmanager_accounts.txt
```

Add your accounts (one per line):
```
ms.stevenbaker682510:tGeltLAc02KDNxI||Authorization=Bearer IGT:2:eyJkc191c2VyX2lkIjoiNzE2NzYzMTYwMDAiLCJzZXNzaW9uaWQiOiI3MTY3NjMxNjAwMCUzQVUxTG1hUm53RElZYlB2JTNBMTAlM0FBWWM1eUtKRldpejlyUVp4T2QxNEJlQUxiTmZTMHprdFA5bm1VemZoVXcifQ==;IG-U-DS-USER-ID=71676316000;IG-INTENDED-USER-ID=71676316000;IG-U-RUR=RVA,71676316000,1781795419:01fe0e0bb24e5687f0db3f88fe5eca64bc63eb94d828e820487cceda0574e2b85ce3c258;X-MID=aCS6IAABAAFjvtUwad4WoZAEUUhl;X-IG-WWW-Claim=hmac.AR3gO2TnlQGiln15xgihbMcch3sOvuNYuIFo-ur_1ssQ8Ub1||xonoxtsm@wildbmail.com:neoszgkeA!9944
# Add your other 9 accounts here...
```

**Note**: Extract only the cookies part from your full format. The format you need is:
```
login:password||cookies||email:emailpassword
```

### 2. Import All Accounts

Run the import script:

```bash
python3 import_cookies_instmanager.py
```

This will:
- Parse each account
- Extract cookies from the header format
- Save cookies to `cookies/username_cookies.txt`
- Save account info to `cookies/username_account_info.json`

Expected output:
```
--- Processing account 1 (line 1) ---
✅ Parsed account: ms.stevenbaker682510
✅ Parsed 6 cookies
✅ Cookies saved to cookies/ms.stevenbaker682510_cookies.txt
✅ Account info saved to cookies/ms.stevenbaker682510_account_info.json
✅ Successfully imported ms.stevenbaker682510

==================================================
Import completed: 10/10 accounts successful

✅ Successfully imported 10 accounts!

Cookie files created in cookies/ directory:
  - cookies/ms.stevenbaker682510_cookies.txt
  - cookies/account2_cookies.txt
  ...
```

### 3. Create Pre-Auth Accounts File

Create `accounts_preauth.txt` with just the usernames:

```bash
# Extract usernames from imported accounts
ls cookies/*_cookies.txt | sed 's/cookies\///g' | sed 's/_cookies.txt//g' > accounts_preauth.txt
```

Or manually create:
```
ms.stevenbaker682510
username2
username3
# ... all 10 usernames
```

### 4. Start the Bot

```bash
make build
make up
make logs
```

The bot will:
- Detect `accounts_preauth.txt`
- Use pre-authenticated mode (no login required)
- Rotate between accounts automatically
- Use existing cookies for each account

## Verification

Check if everything works:

```bash
# Check account status
make accounts-status

# Check if cookies are valid
make check-cookies

# View logs
make logs
```

Expected status output:
```
📊 Account Status
==================================================
Total accounts: 10
Available: 10
Banned: 0
Current: ms.stevenbaker682510

+---------------------+--------+----------------------+-------------+
| Username            | Status | Last Used            | Has Cookies |
+=====================+========+======================+=============+
| ms.stevenbaker682510| ✅     | Never                | Yes         |
| username2           | ✅     | Never                | Yes         |
+---------------------+--------+----------------------+-------------+
```

## Troubleshooting

### Cookies Not Working

If you get "white screen" or authentication errors:

1. **Check cookie format**:
   ```bash
   head -5 cookies/ms.stevenbaker682510_cookies.txt
   ```

2. **Validate cookies**:
   ```bash
   make check-cookies
   ```

3. **Check account info**:
   ```bash
   cat cookies/ms.stevenbaker682510_account_info.json
   ```

### Import Errors

1. **Format issues**: Make sure your `instmanager_accounts.txt` uses exactly:
   ```
   login:password||cookies||email:emailpassword
   ```

2. **Missing cookies**: Check that the cookies part contains:
   - `Authorization=Bearer IGT:...`
   - `IG-U-DS-USER-ID=...`
   - `X-MID=...`

3. **Re-import single account**:
   ```bash
   python3 -c "
   from src.instagram_video_bot.utils.cookie_importer import *
   import_instmanager_account('your_account_line_here', 'username')
   "
   ```

## Account Management

### Manual Rotation
```bash
make accounts-rotate
```

### Reset Banned Account
```bash
make accounts-reset-one USERNAME=ms.stevenbaker682510
```

### Check Specific Account
```bash
python3 check_cookies.py cookies/ms.stevenbaker682510_cookies.txt
```

## Best Practices

1. **Daily limits**: 50-100 downloads per account
2. **Rotation**: Let the bot auto-rotate every 50 downloads
3. **Monitoring**: Check `make accounts-status` daily
4. **Backup**: Keep your original `instmanager_accounts.txt` file

## Cookie Format Details

Your cookies contain:
- **Authorization**: Bearer token for API access
- **IG-U-DS-USER-ID**: User ID for Instagram
- **X-MID**: Machine/browser ID
- **IG-U-RUR**: Regional routing info

These are automatically converted to Netscape format for yt-dlp compatibility. 