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
- [CI](#ci)
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
- **One-Tap Audio** - Tap the audio button on a direct, single-video delivery to receive an MP3 while the recent result is cached
- **Multi-Account Rotation** - High availability with account switching
- **Bounded Instagram Workers** - Blocking Instagram downloads run in killable subprocesses, so timed-out work cannot keep a provider thread occupied
- **Anti-Ban Protection** - Account rotation and cooldown-based recovery
- **Advanced Authentication** - Cookie management and 2FA support
- **Health Monitoring** - Automatic account status tracking
- **Docker Ready** - Easy deployment with Docker Compose
- **Rate Limiting** - Smart delays to avoid Instagram limits
- **Easy Configuration** - Environment-based setup
- **Telegram Integration** - Seamless bot interaction
- **Management Tools** - Account rotation and maintenance utilities

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

# 4. Prepare writable account-state storage (fresh installs and upgrades)
sudo install -d -o 1000 -g 1000 -m 0750 account-state
if [ -f accounts_state.json ] \
  && [ ! -e account-state/accounts_state.json ] \
  && [ ! -L account-state/accounts_state.json ]; then
  sudo cp -p accounts_state.json account-state/accounts_state.json
fi
sudo chown -R 1000:1000 account-state
sudo chmod -R u+rwX account-state

# 5. Build and start the bot
make build
make up

# 6. Optional multi-account setup
# Create accounts.txt if you want rotation support, then initialize sessions
# Each managed account needs password + non-empty totp_secret
make accounts-setup
```

Keep the old file until the bot has started successfully and `make accounts-status`
shows the expected quarantines and failure counters.

### Option 2: Local Installation

```bash
# 1. Clone and install project dependencies
git clone https://github.com/yourusername/instagram-video-downloader-bot.git
cd instagram-video-downloader-bot
uv sync

# 2. Configure environment
cp .env.example .env
nano .env  # Add your credentials

# 3. Optional multi-account setup
# Create accounts.txt with username|password|non-empty totp_secret entries
uv run python manage_accounts.py setup

# 4. Start the bot
uv run python -m src.instagram_video_bot
```

## Usage

### Basic Commands

```bash
# Start the bot
make up                    # Docker
uv run python -m src.instagram_video_bot  # Local

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

# Reset accounts banned longer than 24 hours
make accounts-reset-old HOURS=24
```

### Monitoring & Maintenance

```bash
# Inspect account health
make accounts-status              # Docker
uv run python manage_accounts.py status  # Local

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
uv run pytest -q

# Run specific test file
uv run pytest tests/test_video_downloader.py -q
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

## CI

### GitHub Actions Workflow

The repository now includes a GitHub Actions CI workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

Current CI checks:

1. **Dependency Sync**
   - Installs project and development dependencies with `uv sync --frozen --group dev`

2. **Tests**
   - Runs the stable test suite with `uv run pytest -q tests`

3. **Docker Verification**
   - Builds the Docker image from the existing `Dockerfile`

### Workflow Configuration

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install uv
        uses: astral-sh/setup-uv@v6
      - name: Sync dependencies
        run: |
          uv sync --frozen --group dev
      - name: Run tests
        run: uv run pytest -q tests
  docker-build:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker build -t inst-video-downloader-tg-bot:ci .
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
INSTAGRAM_ISOLATED_WORKERS_ENABLED=true
INSTAGRAM_PROVIDER_TIMEOUT_SECONDS=180
LOG_LEVEL=INFO
VIDEO_WIDTH=320
VIDEO_HEIGHT=480
VIDEO_BITRATE=192k
VIDEO_CRF=23
DEV_MODE=false
```

### Account Files

#### Single Account Mode
Set `IG_USERNAME`, `IG_PASSWORD`, and optional `TOTP_SECRET` in `.env`.

#### Multi-Account Mode
Create `accounts.txt`:
```
username1|password1|totp_secret1
username2|password2|totp_secret2
```

Every managed account needs a password and a non-empty `totp_secret`. Empty third fields stay unavailable and will not be used for rotation.

### Account Auto-Quarantine

The bot tracks sequential failures for each managed Instagram account in `accounts_state.json`.
When an account reaches `ACCOUNT_FAILURE_THRESHOLD` sequential auth/download failures, it is removed from the usable rotation by state, not deleted from `accounts.txt`.
A successful account use resets its sequential failure counter.

If usable accounts drop below `ACCOUNT_LOW_WATERMARK`, the bot sends an English DM alert to `BOT_OWNER_USER_ID`.
Alerts are rate-limited by `ACCOUNT_ALERT_COOLDOWN_SECONDS` to avoid spam during outage bursts.

Initialize sessions after creating `accounts.txt`:
```bash
uv run python manage_accounts.py setup
uv run python manage_accounts.py status
```

## Paid true inline mode

Inline mode sends media into the chat where the inline result is selected. Directly pasting links into a bot chat remains free.

For directly pasted links, a single delivered video has a **🎵 Audio** button when its source stays in the recent-result cache. It converts the video to MP3 and replies in the same chat. The button expires with the cache (four hours by default); send the link again if it has expired. Albums, large videos removed from local cache, and inline deliveries do not show this button.

For users who do not already have inline access, the first inline selection is a Stars invoice or subscription invoice link. After the payment succeeds, they run the same inline query again and select "Send media here"; that second result is the one the bot edits into the requested media. One-time payments grant that retry only for the paid link, and are refunded if delivery for that paid link fails.

Required setup:

1. In BotFather, run `/setinline`.
2. In BotFather, run `/setinlinefeedback` and set feedback to 100%.
3. Create a private storage channel or chat, add the bot, and set `INLINE_STORAGE_CHAT_ID`.
4. Set `INLINE_SUBSCRIPTION_STARS` or use `/inline_price subscription <stars>`.
5. Optionally enable one-time access with `/inline_onetime on <stars>`.
6. Grant free inline access with `/inline_whitelist add <telegram_id>` or forward a visible-origin user message to the bot as owner.

Telegram cannot upload a new file while editing an inline message, so the bot first uploads media to the storage chat, then edits the selected inline message using the resulting `file_id`.

Twitter/X posts with multiple photos or videos are delivered as a browsable inline
gallery with previous/next buttons and a media counter. Recipients can browse the
gallery without another download or payment, including after a bot restart.
In ordinary bot chats, the same posts use Telegram albums. Inline failure messages
retain the original source URL, including when a retry is available.

Twitter photo extraction extends the pinned yt-dlp extractor to retain photo
metadata and request original-resolution images; video extraction still uses
yt-dlp. Twitter inline cache keys are versioned to avoid reusing older entries
that contain only the first item. Keep the Twitter extractor regression tests
passing when upgrading yt-dlp, since this extension uses its extractor API.
`INLINE_ONE_TIME_CLAIM_RECOVERY_SECONDS` controls when a paid one-time link that was claimed but never finished becomes selectable again; active deliveries are not released by this cleanup.

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Authentication failed | Verify `.env` credentials, then run `make accounts-setup` or `uv run python manage_accounts.py setup` |
| Rate limit reached | `make accounts-rotate` or, after cooldown, `make accounts-reset-old HOURS=24` |
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

# Validate configuration
make test-health
```

### Getting Help

1. Check the [troubleshooting section](#troubleshooting)
2. Review logs with `make logs`
3. Search [existing issues](https://github.com/yourusername/repo/issues)
4. Create a [new issue](https://github.com/yourusername/repo/issues/new) with logs

## Delivery latency

Owner performance output reports receipt-to-first-media and receipt-to-all-media
p50/p95, delivery outcomes, storage-upload time/throughput, and provider phase
timings. Receipt starts when the message handler runs; it does not include
Telegram polling delay or client rendering. Acquisition `completed` is still a
separate job state, not confirmation of delivery. Failed/unknown/cancelled
requests are counted separately from successful latency percentiles. Historical
rows without receipt measurements are not backfilled with guessed durations.

Public Instagram posts and reels first try one immediately eligible account
with an existing saved session. This attempt has a 20-second acquisition budget,
including provider-slot waits, throttling and session validation, and never
starts a fresh login. Account leases, assigned proxies and ramp/cooldown rules
still apply. If the attempt fails or no eligible saved session is available,
the fast extractor and public yt-dlp remain available; the request does not then
cycle through the account roster again. Stories and single-account deployments
without an account roster retain their existing flows. A roster containing one
account still uses auth-first ordering.
This policy requires isolated workers; legacy thread mode retains the previous
provider order. Set `INSTAGRAM_AUTH_FIRST_ENABLED=false` to restore the previous
order, or tune `INSTAGRAM_AUTH_FIRST_TIMEOUT_SECONDS` to change the attempt budget.

Authenticated sources may be lower resolution than the highest-resolution
public source. In the live canaries, ready-to-send 720×1280 H.264/AAC avoided
transcoding the public 1080×1920 VP9 source. This default favors delivery latency;
it does not promise the maximum available source resolution. The authenticated
account roster and the fast extractor's cookie/bearer pool are separate.

Public Instagram metadata has a 15-second deadline in isolated-worker mode.
After metadata resolves, the transfer receives its own 180-second budget; the
overall acquisition deadline is 300 seconds, including fallback and account
waits. Normalization has a separate 300-second budget per video, shared by its
probe, conversion and validation commands. Transcoding defaults to the `veryfast`
x264 preset while retaining resolution and CRF 20; encoded size and compression
quality can vary. Set `INSTAGRAM_NORMALIZATION_PRESET=medium` for the prior preset.
Legacy thread mode cannot forcibly stop a running network call; leave isolated
workers enabled for hard deadlines.

The encoder preset changes encoding effort, not the selected codec/profile:
transcodes still use MP4, H.264 High, 8-bit `yuv420p`, AAC audio and `+faststart`.
[Telegram documents MPEG4 video support](https://core.telegram.org/bots/api#sendvideo);
[FFmpeg documents presets and profile restrictions separately](https://ffmpeg.org/ffmpeg-codecs.html#libx264_002c-libx264rgb).
Real benchmark outputs passed full audio/video decoding and Telegram video sends.
These checks do not replace playback testing on physical iOS/Android clients.

The fast extractor opens a circuit after three consecutive failures and allows
one recovery probe after five minutes. Replacing the configured auth file
refreshes the pool and resets the circuit; owner status shows only configured,
available and cooling-down context counts. This cannot repair expired credentials
or prove that a replacement account is healthy. Use the existing account canary
workflow before changing production credentials.

Album storage uploads run with a shared concurrency limit of two, retain input
order and successful file IDs, and coordinate Telegram flood backoff. Final user
sends retain the no-duplicate policy. Public format selection preserves maximum
resolution and prefers H.264/AAC sources, then smaller muxed sources at equal
resolution/frame rate. `IG_PUBLIC_PREFER_COMPATIBLE_FORMATS=false` disables the
codec preference. Compression quality can differ. Set
`IG_PUBLIC_PREFER_SMALLER_FORMATS=false` to
restore larger/higher-bitrate preference at equal resolution/frame rate.
Optional `IG_PUBLIC_MAX_HEIGHT` and `IG_PUBLIC_MAX_SOURCE_BYTES` caps trade
quality/availability for fewer bytes.
An incomplete public carousel triggers fallback instead of partial success.

Hot delivery metrics and cache operations run off the event loop. Cancellation
drains an in-flight state write before applying terminal updates. Some admission
operations remain synchronous; their SQLite busy wait is bounded to 100ms by
default rather than ten seconds. Prolonged external write contention can fail
admission, so monitor database lock errors before increasing this budget.

Tune the settings in `.env.example` after comparing uncached single videos,
carousels and cache hits separately. To roll back the performance policies, set
staging concurrency to one and restore the prior timeout values; code rollback
can leave the additive SQLite metrics table/columns in place. No credential
rotation or live deployment is implied by these source changes.

Uncached Instagram posts and reels received in chats use a bounded preparation
race when two saved-session accounts are immediately available. One candidate
asks Telegram to fetch compatible CDN media; the other downloads and normalizes
locally. The first complete set of Telegram file IDs wins. A single coordinator
then sends the result, while cancelled workers finish cleanup. Account leases
and race capacity remain held until those workers stop. With one available
account or exhausted race capacity, the usual sequential path runs.

Direct candidates undergo whole-album checks before staging: known JPEG/MP4
sizes within Telegram URL-fetch limits, valid dimensions, and H.264/yuv420p video
with AAC audio. Silent videos, oversized files and uncertain sources use the
local candidate. Direct metadata, authentication/session, and transport failures
can trigger public extraction, with shared ownership preventing duplicate public
fallbacks.

Private staging is durable storage: winning media IDs remain available for reuse.
Speculative or ambiguously cancelled uploads can leave additional messages in the
private storage chat, as upload retries can. Cancellation cannot reliably delete
sends whose message IDs were never received. Final user delivery has one owner;
the race bounds and disable flag control speculative overhead. Automatic private
storage message cleanup is not provided.

Telegram upload limits and flood backoff apply to both candidates. Rejected
cached file IDs trigger local reacquisition and one safe retry.

`INSTAGRAM_DELIVERY_RACE_ENABLED=false` disables racing.
`INSTAGRAM_DELIVERY_RACE_MAX_ACTIVE=2` bounds concurrent races, and
`INSTAGRAM_NORMALIZATION_CONCURRENCY=1` limits isolated video processing across
requests. Inline deliveries retain their existing acquisition flow. Direct URL
winners have no local source for the audio button. These source defaults have
not been deployed. Earlier prototype measurements are recorded in
[the race benchmark](docs/racing-delivery-benchmark-2026-10-03.json); integrated
measurements are [recorded separately](docs/integrated-delivery-race-2026-10-03.json)
to avoid mixing different implementations. These historical measurements have
[known harness limitations](docs/experiments/2026-10-03-media-race/README.md#review-correction-and-historical-evidence)
and do not validate the corrected harness.

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

# 2. Install development dependencies
uv sync

# 3. Create feature branch
git checkout -b feature/your-feature-name

# 4. Make changes and test
uv run pytest -q
uv run black src/
uv run isort src/

# 5. Commit and push
git commit -m "Add your feature"
git push origin feature/your-feature-name

# 6. Create Pull Request
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
