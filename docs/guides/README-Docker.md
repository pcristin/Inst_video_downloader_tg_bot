# Docker Setup for Instagram Video Downloader Bot

This guide explains how to run the Instagram Video Downloader Bot using Docker.

## Prerequisites

- Docker and Docker Compose installed on your system
- A Telegram Bot Token (from @BotFather)
- Instagram account credentials

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/instagram-video-bot.git
   cd instagram-video-bot
   ```

2. **Create environment file:**
   ```bash
   cp env.example .env
   ```
   Edit `.env` and add your credentials:
   - `BOT_TOKEN`: Your Telegram bot token
   - `IG_USERNAME`: Your Instagram username
   - `IG_PASSWORD`: Your Instagram password

3. **Build and run the bot:**
   ```bash
   make build
   make up
   ```

## Two-Factor Authentication Setup

If your Instagram account has 2FA enabled:

1. **Run the setup script:**
   ```bash
   ./docker-setup-2fa.sh
   ```

2. **Scan the QR code** in `2fa_qr.png` with Google Authenticator

3. **Add the secret** to your `.env` file:
   ```env
   TOTP_SECRET=your_generated_secret
   ```

4. **Delete the QR code** for security:
   ```bash
   rm 2fa_qr.png
   ```

5. **Restart the bot:**
   ```bash
   make restart
   ```

## Docker Commands

### Start the bot:
```bash
make up
```

### View logs:
```bash
make logs
```

### Stop the bot:
```bash
make down
```

### Rebuild after code changes:
```bash
make build
make up
```

### Run commands inside container:
```bash
docker-compose exec instagram-video-bot /bin/bash
```

### Run Python commands inside the container:
```bash
docker-compose exec instagram-video-bot uv run --no-sync python -m src.instagram_video_bot.utils.health_check
docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py status
```

## Volume Mounts

The Docker setup uses several volume mounts:

- `./temp:/app/temp` - Temporary video downloads
- `./sessions:/app/sessions` - Persisted Instagram sessions
- `./logs:/app/logs` - Application logs (optional)

## Troubleshooting

### Bot not starting
Check the logs:
```bash
docker-compose logs instagram-video-bot
```

### Instagram authentication issues
1. Verify your `.env` credentials or `accounts.txt` entries.
2. Re-initialize sessions:
   ```bash
   docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py setup
   ```
3. Restart the bot:
   ```bash
   make restart
   ```

### Video download failures
- Ensure FFmpeg is properly installed in the container
- Check if Instagram requires re-authentication
- Verify proxy settings if using a proxy

### Container resource issues
Adjust resource limits in `docker-compose.yml`:
```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
```

## Development

To develop with Docker:

1. Mount your source code:
   ```yaml
   volumes:
     - ./src:/app/src
   ```

2. Enable hot reload by setting:
   ```env
   DEV_MODE=true
   ```

3. Use Docker Compose override:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
   ```

## Security Considerations

- Never commit `.env` file to version control
- Keep session data secure
- Use strong passwords for Instagram account
- Consider using a dedicated Instagram account for the bot
- Regularly update the Docker image for security patches

## Performance Optimization

- The container is configured to run as non-root user for security
- Video processing is optimized for Telegram's requirements
- Resource limits prevent container from consuming excessive resources 
