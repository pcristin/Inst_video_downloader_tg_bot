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

# Setup all accounts and initialize sessions
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

## Session and Health Commands

```bash
# Health check inside the running container
make test-health

# Inspect current account/session state
make accounts-status

# Open a shell if you need manual inspection
make shell
```

## Development & Testing

```bash
make dev            # Start in development mode
make dev-build      # Build for development
make test-health    # Test health check
make setup-2fa      # Setup 2FA
```

## Typical Workflows

### Setup with InstAccountsManager Source Data

```bash
# 1. Convert provider data into accounts.txt
# Format: username|password|totp_secret

echo "username|password|totp_secret" > accounts.txt

# 2. Build Docker image
make build

# 3. Initialize account sessions
make accounts-setup

# 4. Start the bot
make up

# 5. Check status
make accounts-status

# 6. Monitor logs
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

# Need to inspect current account state?
make accounts-status

# Need to run the health check?
make test-health

# Need shell access?
make shell
```

## Notes

- All commands run inside Docker container
- The image uses `uv` for Python execution
- Account data persists in `accounts_state.json`
- Session state is managed through the supported account workflow
- Logs available with `make logs` 
