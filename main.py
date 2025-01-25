import os
import re
import tempfile
import logging
import time

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)

from yt_dlp import YoutubeDL
from config import BOT_TOKEN, IG_USERNAME, IG_PASSWORD
from instagram_auth import get_instagram_cookies
import json

# Enable logging for convenience
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# NOTE: Make sure group privacy is turned OFF in BotFather if you want to see all messages in the group.

# Define a regex pattern to detect Instagram video or reel links
INSTAGRAM_VIDEO_PATTERN = r"(https?://(?:www\.)?instagram\.com/(?:p|reel)/[^ ]+)"

async def download_instagram_video(url: str, download_path: str) -> str:
    """
    Use yt-dlp to download Instagram video to a temporary folder.
    Returns the path to the downloaded file.
    """
    # Try to use existing cookies or get new ones
    cookies_file = 'instagram_cookies.txt'
    if not os.path.exists(cookies_file) or time.time() - os.path.getmtime(cookies_file) > 86400:  # 24 hours
        logging.info("Getting fresh Instagram cookies...")
        if not get_instagram_cookies():
            raise Exception("Failed to get Instagram cookies")
    
    ydl_opts = {
        'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
        'format': 'best',
        'cookiefile': cookies_file,
        'verbose': True,
        'no_warnings': False,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise Exception("Failed to extract video info")
                
                video_path = ydl.prepare_filename(info)
                if not os.path.exists(video_path):
                    raise Exception(f"Video file not found at {video_path}")
                
                logging.info(f"Video downloaded successfully to {video_path}")
                return video_path

            except Exception as e:
                logging.error(f"Error extracting video info: {str(e)}")
                raise

    except Exception as e:
        logging.error(f"Error in download_instagram_video: {str(e)}")
        raise

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Check the message for an Instagram link, download the video, and send it as a reply.
    """
    if not update.message or not update.message.text:
        return

    message_text = update.message.text
    match = re.search(INSTAGRAM_VIDEO_PATTERN, message_text)

    if match:
        url = match.group(1)
        logging.info(f"Processing Instagram URL: {url}")

        # Ensure the chat context is available
        if update.effective_chat is None:
            logging.error("No chat context available for this update.")
            return

        # Create a temporary directory to hold our download
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                # Inform user that download is in progress
                status_message = await update.message.reply_text("Downloading video...")

                # Download the video
                video_file_path = await download_instagram_video(url, tmp_dir)

                # Send the video, replying to the original message
                with open(video_file_path, 'rb') as video_file:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=video_file,
                        reply_to_message_id=update.message.message_id
                    )

                # Delete the status message
                await status_message.delete()

            except Exception as e:
                error_message = f"Sorry, I couldn't download that video. Error: {str(e)}"
                logging.error(error_message)
                await update.message.reply_text(error_message)
                return

            # The file will be automatically cleaned when tmp_dir is removed

def main():
    """
    Main entry point. Set up the bot handlers and start polling.
    """
    if BOT_TOKEN is None:
        raise ValueError("BOT_TOKEN is not set in environment variables")
        
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # With group privacy off, the following will catch all text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.info("Bot started and ready to process messages")
    application.run_polling()

if __name__ == '__main__':
    main() 