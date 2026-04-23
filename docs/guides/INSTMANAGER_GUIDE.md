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

### 2. Convert the Source List to `accounts.txt`

The repo's active workflow is session-based. Instead of importing cookie files, convert your InstAccountsManager source into the normal `accounts.txt` input used by `manage_accounts.py`.

Create `accounts.txt` with one account per line:
```
ms.stevenbaker682510|tGeltLAc02KDNxI|your_totp_secret
username2|password2|your_totp_secret
username3|password3|your_totp_secret
```

Notes:
- Keep only the login credentials and optional TOTP secret in `accounts.txt`.
- Do not rely on legacy exported cookie artifacts or `accounts_preauth.txt` as the primary setup path.
- If a specific account does not use 2FA, leave the third field empty: `username|password|`.

### 3. Initialize Account Sessions

Install dependencies and create sessions with the supported CLI:

```bash
uv sync
uv run python manage_accounts.py setup
uv run python manage_accounts.py status
```

This creates and verifies the session-based state the bot uses for account rotation.

### 4. Start the Bot

```bash
make build
make up
make logs
```

The bot will:
- Read configured accounts from `accounts.txt`
- Use the sessions created by `manage_accounts.py setup`
- Rotate between accounts automatically
- Persist refreshed account sessions between runs

## Verification

Check if everything works:

```bash
# Check account status
make accounts-status

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

### Session Setup Not Working

If you get authentication errors during setup or rotation:

1. **Check source account format**:
   ```bash
   sed -n '1,5p' instmanager_accounts.txt
   ```

2. **Validate the active account state**:
   ```bash
   make accounts-status
   ```

3. **Re-run session initialization**:
   ```bash
   uv run python manage_accounts.py setup
   uv run python manage_accounts.py status
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

3. **Retry after fixing one source line**:
   - Update the affected entry in `instmanager_accounts.txt`.
   - Regenerate `accounts.txt` from that corrected source entry.
   - Re-run `uv run python manage_accounts.py setup`.

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
make accounts-status
```

## Best Practices

1. **Daily limits**: 50-100 downloads per account
2. **Rotation**: Let the bot auto-rotate every 50 downloads
3. **Monitoring**: Check `make accounts-status` daily
4. **Backup**: Keep your original `instmanager_accounts.txt` file

## Source Format Details

The InstAccountsManager source lines may include:
- **Authorization**: Bearer token from the provider export
- **IG-U-DS-USER-ID**: Instagram user identifier
- **X-MID**: Device or browser identifier
- **IG-U-RUR**: Regional routing metadata

Those fields are part of the source export format, but the repo's active runtime workflow is session-based through `accounts.txt` and `manage_accounts.py`.
