# Multi-Account Management Guide

## Overview

The bot now supports managing multiple Instagram accounts with automatic rotation and ban detection. This is essential for avoiding detection and maintaining continuous operation.

## Setup

### 1. Create accounts.txt

Create a file named `accounts.txt` with your accounts in this format:

```
username|password|totp_secret
```

Example with 20 accounts:
```
samosirarlene|@encore05|4NPFTMJUVP7NPXPZC3MDZ26SVZTW5GUL
johndoe123|Pass123!|5KPFTMJUVP7NPXPZC3MDZ26SVZTW5ABC
janedoe456|SecurePass|6LPFTMJUVP7NPXPZC3MDZ26SVZTW5DEF
# Add all 20 accounts...
```

### 2. Setup All Accounts

This will login to each account and generate cookies:

```bash
python3 manage_accounts.py setup
```

**Note**: This process will:
- Login to each account one by one
- Generate and save cookies for each
- Wait 30 seconds between accounts to avoid detection
- Skip any accounts marked as banned

### 3. Check Account Status

```bash
python3 manage_accounts.py status
```

Output example:
```
📊 Account Status
==================================================
Total accounts: 20
Available: 18
Banned: 2
Current: samosirarlene

+----------------+--------+----------------------+-------------+
| Username       | Status | Last Used            | Has Cookies |
+================+========+======================+=============+
| samosirarlene  | ✅     | 2024-01-15 10:30:00 | Yes         |
| johndoe123     | ✅     | 2024-01-15 09:15:00 | Yes         |
| janedoe456     | ❌     | 2024-01-14 22:00:00 | Yes         |
+----------------+--------+----------------------+-------------+
```

## Account Management Commands

### View Status
```bash
python3 manage_accounts.py status
```

### Setup All Accounts
```bash
python3 manage_accounts.py setup
```

### Manually Rotate Account
```bash
python3 manage_accounts.py rotate
```

### Reset Banned Status
```bash
# Reset specific account
python3 manage_accounts.py reset username

# Reset all accounts
python3 manage_accounts.py reset
```

### Warm Up Account
```bash
# Warm up current account
python3 manage_accounts.py warmup

# Warm up specific account
python3 manage_accounts.py warmup username
```

## How It Works

### Automatic Rotation

1. **On Authentication Failure**: When the bot detects authentication issues, it automatically:
   - Marks the current account as banned
   - Rotates to the next available account
   - Retries the download

2. **Smart Selection**: The account manager:
   - Tracks when each account was last used
   - Selects accounts that haven't been used recently
   - Avoids using the same account repeatedly

3. **State Persistence**: Account status is saved in `accounts_state.json`:
   - Last used timestamp
   - Banned status
   - Cookie file locations

### Ban Detection

Accounts are marked as banned when:
- Authentication fails (cookies expired)
- Rate limit errors occur
- Instagram returns login required errors

### Cookie Management

- Each account gets its own cookie file: `cookies/username_cookies.txt`
- Cookies are generated during setup by logging in
- Cookie files are checked before use

## Best Practices

### 1. Initial Setup

```bash
# 1. Create accounts.txt with all accounts
# 2. Setup all accounts (this may take 10+ minutes for 20 accounts)
python3 manage_accounts.py setup

# 3. Start with a warmup
python3 manage_accounts.py warmup

# 4. Wait 30+ minutes before heavy usage
```

### 2. Daily Maintenance

```bash
# Check account health
python3 manage_accounts.py status

# Rotate if needed
python3 manage_accounts.py rotate
```

### 3. Usage Patterns

- **Downloads per account**: 50-100 per day max
- **Account rotation**: Every 50 downloads or 2 hours
- **Total daily limit**: 1000-2000 downloads across all accounts
- **Break pattern**: Stop for 10 minutes every hour

### 4. Recovery

If many accounts get banned:

```bash
# 1. Stop the bot
docker-compose down

# 2. Wait 24 hours

# 3. Reset all accounts
python3 manage_accounts.py reset

# 4. Re-setup accounts
python3 manage_accounts.py setup

# 5. Warm up before use
python3 manage_accounts.py warmup
```

## Monitoring

### Check Logs

```bash
# Bot logs
docker-compose logs -f

# Account state
cat accounts_state.json | jq
```

### Health Monitoring

The bot will log:
- Which account is being used
- When accounts are rotated
- When accounts are marked as banned

## Troubleshooting

### "No available accounts"

All accounts are banned. Reset and re-setup:
```bash
python3 manage_accounts.py reset
python3 manage_accounts.py setup
```

### "Failed to setup account"

Instagram may be blocking logins. Try:
1. Use a different proxy
2. Wait a few hours
3. Setup accounts one by one with longer delays

### Accounts Get Banned Quickly

Check:
- [ ] Using proxy from account's country?
- [ ] Warming up accounts before use?
- [ ] Following rate limits?
- [ ] Taking regular breaks?

## Advanced Usage

### Custom Account File

```bash
# Use different account file
ACCOUNTS_FILE=vip_accounts.txt python3 manage_accounts.py setup
```

### Parallel Downloads

With 20 accounts, you can potentially run multiple bot instances:
```bash
# Instance 1 (accounts 1-10)
BOT_INSTANCE=1 docker-compose up -d

# Instance 2 (accounts 11-20)  
BOT_INSTANCE=2 docker-compose -p bot2 up -d
```

**Warning**: This increases detection risk. Use carefully.

## Summary

With 20 accounts properly managed:
- ✅ 1000-2000 downloads per day possible
- ✅ Automatic failover when accounts fail
- ✅ Continuous operation even if some accounts get banned
- ✅ Better distribution of load

Remember: Even with multiple accounts, follow safety guidelines to maximize account lifespan. 