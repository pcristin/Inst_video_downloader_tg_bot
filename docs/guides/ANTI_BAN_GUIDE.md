# Instagram Anti-Ban Guide

## Why Accounts Get Banned

Instagram bans accounts that exhibit bot-like behavior. Common triggers include:

1. **Outdated User-Agents**: Using old browser versions (like Chrome 94 from 2021)
2. **No Proxy**: Not using a proxy from the account's country
3. **Immediate Usage**: Using accounts immediately after creation/purchase
4. **High Request Rate**: Downloading too many videos too quickly
5. **Missing Headers**: Not sending proper HTTP headers
6. **Direct API Access**: Making requests that look automated

## Prevention Steps

### 1. Use a Proxy (CRITICAL)

Your account data shows it's from the USA. You MUST use a USA proxy:

```bash
# Add to your .env file:
PROXY_HOST=your.proxy.host
PROXY_PORT=8080
PROXY_USERNAME=proxyuser
PROXY_PASSWORD=proxypass
```

**Recommended proxy types:**
- Residential proxies (best)
- Mobile proxies (good)
- Datacenter proxies (risky)

### 2. Initialize New Accounts

Before using the bot, initialize the account with the supported account-manager workflow:

```bash
uv run python manage_accounts.py setup
uv run python manage_accounts.py status
```

Every managed account needs a password and a non-empty `totp_secret`. Empty third fields stay unavailable and will not be used for rotation.

### Authenticated Fast Fallback

The bot can optionally try a lightweight Instagram cookie/token context after public fast extraction misses and before full account fallback. Use a read-only JSON file mounted outside the repo:

```json
{
  "instagram": ["mid=<mid>; csrftoken=<csrf>; sessionid=<session>"],
  "instagram_bearer": ["token=<instagram_bearer_token>"]
}
```

Set `IG_AUTH_COOKIES_FILE=/run/secrets/instagram_auth.json` and project the host file as a read-only Compose secret owned by the container bot user. The file is loaded at startup, so rotate it by replacing the host file and restarting the container. Do not commit cookie or token files.

To gather cookie contexts from the configured `accounts.txt` workflow without copying browser cookies by hand, run:

```bash
make accounts-export-auth
docker compose up -d instagram-video-bot
```

The export command logs in or reuses instagrapi sessions for configured accounts, writes only the Cobalt-compatible `instagram` cookie array to `secrets/instagram_auth.json`, preserves any existing `instagram_bearer` entries, and avoids printing cookie values.

### 3. Initialize Accounts Properly

```bash
uv sync
# Create accounts.txt if you want multi-account rotation
uv run python manage_accounts.py setup
uv run python manage_accounts.py status
```

### 4. Configure Rate Limiting

The updated bot now includes:
- Minimum 10 seconds between downloads
- Random 1-3 second delays
- Download speed limiting (500KB/s)
- Rotating User-Agents

### 5. Usage Best Practices

**DO:**
- ✅ Use residential proxy from account's country
- ✅ Wait 10+ seconds between downloads
- ✅ Limit to 50-100 downloads per day
- ✅ Take breaks (stop bot for hours)
- ✅ Monitor account health regularly

**DON'T:**
- ❌ Use account immediately after purchase
- ❌ Download videos back-to-back
- ❌ Use without proxy
- ❌ Download 24/7 without breaks
- ❌ Use datacenter IPs if possible

### 6. Account Rotation

If you have multiple accounts:

1. Create multiple account files:
   ```
   account1.txt
   account2.txt
   account3.txt
   ```

2. Rotate between them:
   ```bash
   # Use account 1
   cp account1.txt accounts.txt
   uv run python manage_accounts.py setup
   make restart
   
   # Later, switch to account 2
   cp account2.txt accounts.txt
   uv run python manage_accounts.py setup
   make restart
   ```

### 7. Monitor Account Health

Run this regularly:
```bash
make accounts-status
```

### 8. If Account Gets Banned

1. **Stop using it immediately**
2. **Don't try to login** - this can trigger more security
3. **Switch to a different account**
4. **Review your usage patterns**
5. **Ensure proxy is working**
6. **After cooldown, use `make accounts-reset-old HOURS=24` if you need to clear stale bans**

## Technical Improvements Made

1. **Updated User-Agents**: Now using Chrome 122 (latest)
2. **Proper Headers**: Added all security headers Instagram expects
3. **Rate Limiting**: Built-in delays between downloads
4. **Proxy Support**: Integrated proxy configuration
5. **Random Delays**: Human-like behavior patterns
6. **Download Speed Limit**: Prevents suspiciously fast downloads

## Recommended Setup

```bash
# 1. Get USA residential proxy
# 2. Update .env with proxy details
# 3. Initialize account sessions
uv run python manage_accounts.py setup

# 4. Start bot with the current uv-native workflow
make up

# 5. Monitor health
make accounts-status
```

## Emergency Checklist

If accounts keep getting banned:

- [ ] Are you using a proxy?
- [ ] Is the proxy from the account's country?
- [ ] Are you waiting between downloads?
- [ ] Are you limiting daily downloads?
- [ ] Is the User-Agent up to date?

Remember: Instagram's detection is sophisticated. Even with all precautions, accounts may still get flagged. Always have backup accounts ready.
