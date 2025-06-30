# Complete Setup Guide - Avoiding Instagram Bans

## Prerequisites

1. **Purchased Instagram Account** with cookies
2. **USA Residential Proxy** (critical for USA accounts)
3. **Telegram Bot Token** from @BotFather

## Step-by-Step Setup

### 1. Create .env File

Create a `.env` file with these settings:

```bash
# Telegram Bot Token
BOT_TOKEN=your_telegram_bot_token_here

# Instagram Credentials (from your purchased account)
IG_USERNAME=denise.toledo1166
IG_PASSWORD=a558hqpf2ytcz3938

# Two-Factor Authentication
TOTP_SECRET=6C4O BC44 5WVU FE64 CN4K U3VG QTDT B3JY

# CRITICAL: USA Proxy Settings
PROXY_HOST=your.usa.proxy.com
PROXY_PORT=8080
PROXY_USERNAME=proxyuser
PROXY_PASSWORD=proxypass

# Optional Settings
COOKIES_FILE=cookies/instagram_cookies.txt
LOG_LEVEL=INFO
```

### 2. Import Account Cookies

```bash
# Create account.txt with your full account line
cat > account.txt << 'EOF'
denise.toledo1166;a558hqpf2ytcz3938;email@domain.com;token;2fa_secret;USA;cookie_string;warning
EOF

# Import the cookies
python3 import_cookies.py

# Verify cookies are valid
python3 check_cookies.py
```

### 3. Warm Up the Account (CRITICAL)

```bash
# This simulates human browsing behavior
python3 warmup_account.py

# WAIT AT LEAST 30 MINUTES after warmup!
```

### 4. Start the Bot

```bash
# Build and start with Docker
docker-compose up -d

# Check logs
docker-compose logs -f
```

### 5. Monitor Account Health

```bash
# Run in background to monitor cookies
python3 monitor_cookies.py &

# Check manually anytime
python3 check_cookies.py
```

## Usage Guidelines

### Safe Usage Pattern

1. **First Day**: 5-10 downloads max
2. **First Week**: 20-30 downloads per day
3. **After Week 1**: 50-100 downloads per day max

### Time Between Downloads

- **Minimum**: 10 seconds (enforced by bot)
- **Recommended**: 30-60 seconds
- **Take breaks**: Stop for 1-2 hours every 50 downloads

### Daily Schedule Example

```
Morning (9 AM - 12 PM): 20-30 downloads
Break: 12 PM - 2 PM
Afternoon (2 PM - 5 PM): 20-30 downloads  
Break: 5 PM - 8 PM
Evening (8 PM - 11 PM): 20-30 downloads
Night: Bot stopped
```

## Troubleshooting

### "Cookies Expired" Error

1. Check if account is banned:
   ```bash
   python3 check_cookies.py
   ```

2. If banned, switch to new account:
   ```bash
   # Update account.txt with new account
   python3 import_cookies.py
   docker-compose restart
   ```

### Bot Gets Detected Quickly

Check this list:
- [ ] Using USA proxy? (CRITICAL)
- [ ] Warmed up account before use?
- [ ] Waiting between downloads?
- [ ] Taking regular breaks?
- [ ] Not exceeding daily limits?

### Proxy Not Working

Test proxy:
```bash
curl -x http://user:pass@proxy:port https://api.ipify.org
```

Should return USA IP address.

## Best Practices Summary

**✅ DO:**
- Use residential proxy from account's country
- Warm up accounts for 30+ minutes
- Take regular breaks
- Monitor account health daily
- Have backup accounts ready

**❌ DON'T:**
- Use without proxy
- Download immediately after setup
- Download continuously without breaks
- Exceed 100 downloads per day
- Ignore warning signs

## Emergency Response

If account gets banned:

1. **Stop bot immediately**:
   ```bash
   docker-compose down
   ```

2. **Don't try to login manually** (makes it worse)

3. **Switch to backup account**:
   ```bash
   # Update account.txt
   python3 import_cookies.py
   python3 warmup_account.py
   # Wait 30 minutes
   docker-compose up -d
   ```

4. **Review what went wrong** using the checklist above

Remember: Even with all precautions, some accounts may still get flagged. This is why having multiple accounts is essential. 