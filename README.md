# Instagram Video Downloader Bot

A Telegram bot that automatically downloads and forwards Instagram videos and reels shared in chats. Built with Python, python-telegram-bot, and yt-dlp.

## Features

- 🎥 Downloads videos from Instagram posts and reels
- 🤖 Works in private chats and groups
- 🔄 Automatically processes Instagram links
- 🎬 Optimizes video format for Telegram
- 🔒 Handles Instagram authentication
- ⚡ Fast and efficient downloads

## Prerequisites

- Python 3.11 or higher
- Chrome/Chromium browser (for Instagram authentication)
- FFmpeg (for video processing)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/instagram-video-bot.git
   cd instagram-video-bot
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a `.env` file in the project root with your credentials:
   ```env
   BOT_TOKEN=your_telegram_bot_token
   IG_USERNAME=your_instagram_username
   IG_PASSWORD=your_instagram_password
   ```

## Usage

1. Start the bot:
   ```bash
   python -m src.instagram_video_bot
   ```

2. In Telegram, add the bot to a group or start a private chat with it.

3. Share an Instagram video/reel link with the bot.

4. The bot will automatically:
   - Download the video
   - Process it to meet Telegram's requirements
   - Send it back to you

## Development

### Code Style

The project uses:
- Black for code formatting
- isort for import sorting
- mypy for type checking
- pylint for linting

To format the code:
```bash
black src/
isort src/
```

To run type checking and linting:
```bash
mypy src/
pylint src/
```

## Project Structure

```
instagram-video-bot/
├── src/
│   └── instagram_video_bot/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config/
│       │   ├── __init__.py
│       │   └── settings.py
│       ├── core/
│       │   └── __init__.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── telegram_bot.py
│       │   └── video_downloader.py
│       └── utils/
│           ├── __init__.py
│           └── instagram_auth.py
├── .env
├── .gitignore
├── README.md
└── requirements.txt
```

## Contributing

1. Fork the repository
2. Create a new branch for your feature
3. Make your changes
4. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [Selenium](https://www.selenium.dev/)