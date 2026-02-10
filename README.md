# Instagram Video Downloader Telegram Bot

A professional Telegram bot that automatically downloads Instagram videos and reels with advanced multi-account support, anti-ban protection, and high availability features.

## Badges

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/yourusername/instagram-video-downloader-bot)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/docker-supported-blue)](https://docker.com)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

## Table of Contents

- [Demo](#demo)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Installation](#installation)
- [Usage](#usage)
- [Tests](#tests)
- [CI/CD](#cicd)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [License](#license)
- [Contributing](#contributing)

## Demo

![Bot Demo](docs/demo.gif)

*Example: User sends Instagram URL, bot downloads and returns media*

```
User: https://www.instagram.com/reel/xyz123/
Bot:  Downloading media... Please wait.
Bot:  [Sends downloaded media with caption]
```

## Features

- **Fast Primary Downloader + Legacy Fallback** - Multi-endpoint fast extraction first, authenticated fallback second
- **Automatic Media Downloads** - Supports Instagram posts, reels, TV, stories (fallback path), and share links
- **Photo + Album Support** - Sends single photos and mixed carousel albums to Telegram
- **Multi-Account Rotation** - High availability with account switching
- **Anti-Ban Protection** - Human-like behavior and warmup strategies  
- **Advanced Authentication** - Cookie management and 2FA support
- **Health Monitoring** - Automatic account status tracking
- **Docker Ready** - Easy deployment with Docker Compose
- **Rate Limiting** - Smart delays to avoid Instagram limits
- **Easy Configuration** - Environment-based setup
- **Telegram Integration** - Seamless bot interaction
- **Management Tools** - Account warmup and maintenance utilities

## Tech Stack

### Core Technologies
- **Python 3.11+** - Main programming language
- **python-telegram-bot** - Telegram Bot API wrapper
- **instagrapi** - Instagram private API client
- **yt-dlp** - Video downloading engine
- **asyncio** - Asynchronous programming

### Infrastructure & Tools
- **Docker & Docker Compose** - Containerization
- **FFmpeg** - Video processing
- **PyOTP** - Two-factor authentication
- **Pydantic** - Configuration management
- **JSON** - Data persistence

### Development Tools
- **Make** - Build automation
- **Black** - Code formatting
- **Pytest** - Testing framework

## Installation

### Prerequisites
- Python 3.11 or higher
- Docker and Docker Compose (for containerized deployment)
- FFmpeg (for video processing)

### Option 1: Docker Installation (Recommended)

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/instagram-video-downloader-bot.git
cd instagram-video-downloader-bot

# 2. Create environment file
cp .env.example .env

# 3. Configure your credentials in .env
nano .env  # Add BOT_TOKEN, IG_USERNAME, IG_PASSWORD

# 4. Build and start the bot
make build
make up

# 5. Import Instagram cookies (choose one method)
make import-cookies              # For single account
make accounts-setup             # For multi-account mode
```

### Option 2: Local Installation

```bash
# 1. Clone and setup Python environment
git clone https://github.com/yourusername/instagram-video-downloader-bot.git
cd instagram-video-downloader-bot
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
nano .env  # Add your credentials

# 4. Import cookies and start
python3 import_cookies.py
python3 -m src.instagram_video_bot
```

## Usage

### Basic Commands

```bash
# Start the bot
make up                    # Docker
python3 -m src.instagram_video_bot  # Local

# View logs
make logs                  # Docker
# Check Docker logs or terminal output for local

# Stop the bot  
make down                  # Docker
# Ctrl+C for local
```

### Account Management

```bash
# Check account status
make accounts-status

# Setup multiple accounts
make accounts-setup

# Rotate to next account
make accounts-rotate

# Reset banned accounts
make accounts-reset
```

### Account Warmup (Anti-ban)

```bash
# Warm up specific account
make warmup USERNAME=your_username

# Warm up multiple accounts with delays
make warmup-batch ACCOUNTS="user1 user2" DELAY=3600

# Warm up all available accounts
make warmup-available
```

### Monitoring & Maintenance

```bash
# Check cookie validity
make check-cookies

# Monitor account health
make monitor-cookies

# View system health
make test-health

# Clean temporary files
make clean
```

### Sample Telegram Usage

1. **Start a chat** with your bot in Telegram
2. **Send an Instagram URL**:
   ```
   https://www.instagram.com/p/xyz123/
   https://www.instagram.com/reel/abc456/
   https://www.instagram.com/tv/abc456/
   https://www.instagram.com/share/reel/abc123/
   https://www.instagram.com/stories/someuser/1234567890123456789/
   ```
3. **Bot responds** with downloaded media (video, photo, or album)

## Tests

### Running Tests

```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov=src/instagram_video_bot

# Run specific test file
python -m pytest tests/test_video_downloader.py

# Run in Docker
make test
```

### Test Structure

```
tests/
├── test_video_downloader.py    # Video download functionality
├── test_account_manager.py     # Account management
├── test_telegram_bot.py        # Telegram integration
└── test_config.py              # Configuration validation
```

### Writing Tests

```python
# Example test
import pytest
from src.instagram_video_bot.services.video_downloader import VideoDownloader

def test_video_downloader_initialization():
    downloader = VideoDownloader()
    assert downloader is not None
    assert len(downloader.user_agents) > 0
```

## CI/CD

### GitHub Actions Workflow

Our CI/CD pipeline includes:

1. **Code Quality Checks**
   - Black code formatting
   - isort import sorting  
   - Pylint static analysis
   - Type checking with mypy

2. **Testing**
   - Unit tests with pytest
   - Integration tests
   - Coverage reporting

3. **Security**
   - Dependency vulnerability scanning
   - Docker image security scanning

4. **Deployment**
   - Automated Docker builds
   - Multi-platform support (amd64, arm64)
   - Release automation

### Workflow Configuration

```yaml
# .github/workflows/ci.yml
name: CI/CD Pipeline
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Run tests
        run: |
          pip install -r requirements.txt
          python -m pytest --cov
```

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```bash
# Required
BOT_TOKEN=your_telegram_bot_token
IG_USERNAME=your_instagram_username  
IG_PASSWORD=your_instagram_password

# Optional
TOTP_SECRET=your_2fa_secret
PROXY_HOST=proxy.example.com
PROXY_PORT=8080
PROXY_USERNAME=proxy_user
PROXY_PASSWORD=proxy_pass

# Advanced
IG_FAST_METHOD_ENABLED=true
IG_FAST_TIMEOUT_CONNECT=10
IG_FAST_TIMEOUT_READ=45
LOG_LEVEL=INFO
VIDEO_WIDTH=320
VIDEO_HEIGHT=480
VIDEO_BITRATE=192k
VIDEO_CRF=23
DEV_MODE=false
```

### Account Files

#### Single Account Mode
Use browser cookies (see Installation section)

#### Multi-Account Mode
Create `accounts.txt`:
```
username1|password1|totp_secret1
username2|password2|totp_secret2
```

#### Pre-authenticated Mode
Create `accounts_preauth.txt`:
```
username1
username2
username3
```

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Authentication failed | `make check-cookies` → `make import-cookies` |
| Rate limit reached | `make accounts-rotate` → `make warmup-available` |
| No available accounts | `make accounts-status` → `make accounts-reset` |
| Container won't start | Check `.env` file and `make logs` |
| Video download fails | Verify Instagram URL format |

### Debug Commands

```bash
# Enable debug logging
LOG_LEVEL=DEBUG make up

# Check account health
make accounts-status

# Test 2FA code generation
make test-totp

# Validate configuration
make test-health
```

### Getting Help

1. Check the [troubleshooting section](#troubleshooting)
2. Review logs with `make logs`
3. Search [existing issues](https://github.com/yourusername/repo/issues)
4. Create a [new issue](https://github.com/yourusername/repo/issues/new) with logs

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

### Third-party Licenses
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - LGPLv3
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Unlicense
- [instagrapi](https://github.com/subzeroid/instagrapi) - MIT

## Contributing

We welcome contributions! Please follow these guidelines:

### Quick Start for Contributors

```bash
# 1. Fork and clone
git clone https://github.com/yourusername/instagram-video-downloader-bot.git
cd instagram-video-downloader-bot

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install development dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 4. Create feature branch
git checkout -b feature/your-feature-name

# 5. Make changes and test
python -m pytest
black src/
isort src/

# 6. Commit and push
git commit -m "Add your feature"
git push origin feature/your-feature-name

# 7. Create Pull Request
```

### Development Guidelines

- **Code Style**: Use Black formatter and follow PEP 8
- **Testing**: Add tests for new features
- **Documentation**: Update README and docstrings
- **Commits**: Use conventional commit messages
- **Issues**: Link PRs to relevant issues

### Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

---

## Disclaimer

This tool is for educational and personal use only. Please respect Instagram's Terms of Service and use responsibly. The developers are not responsible for any misuse of this software.

## Acknowledgments

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) team
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) developers  
- [instagrapi](https://github.com/subzeroid/instagrapi) maintainers
- All contributors and users of this project
