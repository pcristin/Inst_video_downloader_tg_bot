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

3. Create a `.env` file and add your bot token and Instagram credentials:
   ```dotenv
   BOT_TOKEN="REPLACE_WITH_YOUR_TELEGRAM_BOT_TOKEN"
   IG_USERNAME="your_instagram_username"
   IG_PASSWORD="your_instagram_password"
   ```

4. Set up netrc authentication for Instagram:
   ```bash
   # Create .netrc file in your home directory
   touch ~/.netrc
   
   # Set correct permissions (important for security)
   chmod 600 ~/.netrc
   ```
   
   Then add your Instagram credentials to `~/.netrc`:
   ```
   machine instagram.com
   login your_instagram_username
   password your_instagram_password
   ```

5. Run the bot:
   ```bash
   python main.py
   ```

6. Now, message the bot with an Instagram link (like https://instagram.com/reel/...).

## Notes

- This bot uses the [python-telegram-bot](https://python-telegram-bot.org/) library for interaction with Telegram.
- The video download feature relies on [yt-dlp](https://github.com/yt-dlp/yt-dlp) with Netscape format cookies.
- The bot handles temporary files in a secure manner, removing them after sending the video.
- **Security:** 
  - Ensure that your `.env` file is kept secure and not exposed publicly.
  - The `.netrc` file should have permissions set to 600 (readable only by owner).
  - Never commit `.netrc` or `.env` files to version control.