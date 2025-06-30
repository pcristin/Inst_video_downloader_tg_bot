# Makefile Commands Reference

## Basic Bot Operations

```bash
make build          # Build Docker image
make up             # Start bot in background
make down           # Stop bot
make restart        # Restart bot
make logs           # View bot logs (live)
make shell          # Open shell in container
make clean          # Clean up temp files
```

## Account Management (Multi-Account Mode)

```bash
# View all accounts status
make accounts-status

# Setup all accounts (login & generate cookies)
make accounts-setup

# Rotate to next available account
make accounts-rotate

# Reset banned status
make accounts-reset                              # Reset all accounts
make accounts-reset-one USERNAME=samosirarlene   # Reset specific account

# Warm up accounts
make accounts-warmup                             # Warm up current account
make accounts-warmup-one USERNAME=samosirarlene  # Warm up specific account
```

## Cookie Management

```bash
# Check if cookies are valid
make check-cookies

# Import cookies (single account mode)
make import-cookies

# Import InstAccountsManager format accounts
make import-instmanager

# Create pre-auth accounts file from imported cookies
make create-preauth

# Monitor cookie health
make monitor-cookies
```

## Development & Testing

```bash
make dev            # Start in development mode
make dev-build      # Build for development
make test-health    # Test health check
make test-totp      # Test TOTP generation
make setup-2fa      # Setup 2FA
```

## Typical Workflows

### Setup with InstAccountsManager Format (NEW!)

```bash
# 1. Create raw_accounts.txt with your full account data
# (copy-paste from your provider)

# 2. Format accounts to correct format
make format-instmanager

# 3. Build Docker image
make build

# 4. Import all accounts and extract cookies
make import-instmanager

# 5. Create pre-auth accounts file
make create-preauth

# 6. Start the bot (will auto-detect accounts_preauth.txt)
make up

# 7. Check status
make accounts-status

# 8. Monitor logs
make logs
```

### Initial Setup with Multiple Accounts (Traditional)

```bash
# 1. Create accounts.txt file
echo "username|password|totp_secret" > accounts.txt

# 2. Build the Docker image
make build

# 3. Setup all accounts
make accounts-setup

# 4. Check status
make accounts-status

# 5. Start the bot
make up

# 6. Monitor logs
make logs
```

### Daily Maintenance

```bash
# Check account health
make accounts-status

# Check logs for issues
make logs

# Rotate account if needed
make accounts-rotate

# Reset banned accounts (if any)
make accounts-reset
```

### Troubleshooting

```bash
# Account banned?
make accounts-reset-one USERNAME=problemaccount

# All accounts failing?
make accounts-reset
make accounts-setup

# Need to check cookies?
make check-cookies

# Need shell access?
make shell
```

## Notes

- All commands run inside Docker container
- No need to install Python/pip on host
- Account data persists in `accounts_state.json`
- Cookies stored in `cookies/` directory
- Logs available with `make logs` 