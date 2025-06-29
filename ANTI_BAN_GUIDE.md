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

### 2. Warm Up New Accounts

Before using the bot, warm up the account:

```bash
python3 warmup_account.py
```

Then wait at least 30 minutes before first use.

### 3. Import Cookies Properly

```bash
# 1. Create account.txt with your account data
# 2. Import cookies
python3 import_cookies.py

# 3. Check cookies are valid
python3 check_cookies.py
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
- ✅ Warm up accounts before first use
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
   cp account1.txt account.txt
   python3 import_cookies.py
   docker-compose restart
   
   # Later, switch to account 2
   cp account2.txt account.txt
   python3 import_cookies.py
   docker-compose restart
   ```

### 7. Monitor Account Health

Run this regularly:
```bash
python3 check_cookies.py
```

Set up monitoring:
```bash
# Run in background
python3 monitor_cookies.py &
```

### 8. If Account Gets Banned

1. **Stop using it immediately**
2. **Don't try to login** - this can trigger more security
3. **Switch to a different account**
4. **Review your usage patterns**
5. **Ensure proxy is working**

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
# 3. Import account cookies
python3 import_cookies.py

# 4. Warm up account
python3 warmup_account.py

# 5. Wait 30+ minutes

# 6. Start bot with new anti-detection features
docker-compose up -d

# 7. Monitor health
python3 monitor_cookies.py
```

## Emergency Checklist

If accounts keep getting banned:

- [ ] Are you using a proxy?
- [ ] Is the proxy from the account's country?
- [ ] Did you warm up the account?
- [ ] Are you waiting between downloads?
- [ ] Are you limiting daily downloads?
- [ ] Is the User-Agent up to date?

Remember: Instagram's detection is sophisticated. Even with all precautions, accounts may still get flagged. Always have backup accounts ready. 