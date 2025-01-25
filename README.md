# Telegram Instagram Video Bot

This is a simple Telegram bot that:
1. Listens for Instagram video links in messages.
2. Downloads the video using `yt-dlp`.
3. Sends the video back to the chat.
4. Deletes the downloaded file from the server.

## Setup

1. Clone this repository or download the files into a new folder.

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file and add your credentials:
   ```dotenv
   BOT_TOKEN="REPLACE_WITH_YOUR_BOT_TOKEN"
   IG_USERNAME="your_instagram_username"
   IG_PASSWORD="your_instagram_password"
   ```

4. Run the bot:
   ```bash
   python main.py
   ```

5. Now, message the bot with an Instagram link (like https://instagram.com/p/...).

## Notes

- This bot uses the [python-telegram-bot](https://python-telegram-bot.org/) library for interaction with Telegram.
- The video download feature relies on [yt-dlp](https://github.com/yt-dlp/yt-dlp) with Instagram authentication.
- The bot handles temporary files in a secure manner, removing them after sending the video.
- **Security:** Ensure that your `.env` file is kept secure and not exposed publicly, as it contains sensitive credentials. 