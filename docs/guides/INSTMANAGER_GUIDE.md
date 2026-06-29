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
example_user:example_password|Instagram 345.0.0.48.95 Android (...)|android-device-id;uuid-a;uuid-b|Authorization=Bearer <instagram_bearer_token>;IG-U-DS-USER-ID=<id>;X-MID=<mid>;||<email_address>:<email_password>
```

Parts:
1. **Login**: `example_user:example_password`
2. **Device Info**: `Instagram 345.0.0.48.95 Android...` (we'll ignore this)
3. **Device IDs**: `android-device-id;uuid-a...` (we'll ignore this)
4. **Cookies**: `Authorization=Bearer IGT:...|IG-U-DS-USER-ID=...|...`
5. **Email**: `<email_address>:<email_password>`

## Step-by-Step Setup

### 1. Create Accounts File

Create `secrets/instmanager_accounts.txt` with your 10 accounts:

```bash
# Create the file under the ignored secrets directory
mkdir -p secrets
touch secrets/instmanager_accounts.txt
```

Add your accounts (one per line):
```
example_user:example_password||Authorization=Bearer <instagram_bearer_token>;IG-U-DS-USER-ID=<id>;X-MID=<mid>||<email_address>:<email_password>
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
example_user|example_password|your_totp_secret
username2|password2|your_totp_secret
username3|password3|your_totp_secret
```

Notes:
- Keep only the login credentials and a non-empty TOTP secret in `accounts.txt`.
- Do not rely on legacy exported cookie artifacts or `accounts_preauth.txt` as the primary setup path.
- Every managed account needs a password and a non-empty TOTP secret. If you cannot provide one, leave that account out of `accounts.txt` until it is ready to be used.

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
Current: example_user

+---------------------+--------+----------------------+-------------+
| Username            | Status | Last Used            | Has Cookies |
+=====================+========+======================+=============+
| example_user        | ✅     | Never                | Yes         |
| username2           | ✅     | Never                | Yes         |
+---------------------+--------+----------------------+-------------+
```

## Troubleshooting

### Session Setup Not Working

If you get authentication errors during setup or rotation:

1. **Check source account format**:
   ```bash
   sed -n '1,5p' secrets/instmanager_accounts.txt
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

1. **Format issues**: Make sure your `secrets/instmanager_accounts.txt` uses exactly:
   ```
   login:password||cookies||email:emailpassword
   ```

2. **Missing cookies**: Check that the cookies part contains:
   - `Authorization=Bearer IGT:...`
   - `IG-U-DS-USER-ID=...`
   - `X-MID=...`

3. **Retry after fixing one source line**:
   - Update the affected entry in `secrets/instmanager_accounts.txt`.
   - Regenerate `accounts.txt` from that corrected source entry.
   - Re-run `uv run python manage_accounts.py setup`.

## Account Management

### Manual Rotation
```bash
make accounts-rotate
```

### Reset Old Bans
```bash
make accounts-reset-old HOURS=24
```

### Check Specific Account
```bash
make accounts-status
```

## Best Practices

1. **Daily limits**: 50-100 downloads per account
2. **Rotation**: Let the bot auto-rotate every 50 downloads
3. **Monitoring**: Check `make accounts-status` daily
4. **Backup**: Keep your original `secrets/instmanager_accounts.txt` file outside git

## Source Format Details

The InstAccountsManager source lines may include:
- **Authorization**: Bearer token from the provider export
- **IG-U-DS-USER-ID**: Instagram user identifier
- **X-MID**: Device or browser identifier
- **IG-U-RUR**: Regional routing metadata

Those fields are part of the source export format, but the repo's active runtime workflow is session-based through `accounts.txt` and `manage_accounts.py`.
