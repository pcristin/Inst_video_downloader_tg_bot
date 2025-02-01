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

# Configure root logger
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Set HTTPX logger to WARNING level to suppress INFO messages
logging.getLogger('httpx').setLevel(logging.WARNING)

# NOTE: Make sure group privacy is turned OFF in BotFather if you want to see all messages in the group.

# Define a regex pattern to detect Instagram video or reel links
INSTAGRAM_VIDEO_PATTERN = r"(https?://(?:www\.)?instagram\.com/(?:p|reel)/[^ ]+)"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles incoming messages and processes Instagram video links.
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

async def download_instagram_video(url: str, download_path: str) -> str:
    """
    Use yt-dlp to download Instagram video to a temporary folder.
    Returns the path to the downloaded file.
    """
    async def try_download(retry=False):
        if retry:
            logging.info("Retrying with fresh cookies...")
            if not get_instagram_cookies():
                raise Exception("Failed to get fresh Instagram cookies")
        
        ydl_opts = {
            'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
            'format': 'best',
            'cookiefile': 'instagram_cookies.txt',
            'verbose': True,
            'no_warnings': False,
            # Format and encoding options
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }],
            # FFmpeg options specifically for vertical videos
            'ffmpeg_args': [
                # This will maintain aspect ratio for both horizontal and vertical videos
                '-vf', 'scale=w=720:h=1280:force_original_aspect_ratio=preserve,pad=720:1280:(ow-iw)/2:(oh-ih)/2',
                '-c:v', 'libx264',
                '-crf', '23',
                '-preset', 'medium',
                '-c:a', 'aac',
                '-b:a', '128k'
            ],
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise Exception("Failed to extract video info")
            
            video_path = ydl.prepare_filename(info)
            if not os.path.exists(video_path):
                raise Exception(f"Video file not found at {video_path}")
            
            logging.info(f"Video downloaded successfully to {video_path}")
            return video_path

    try:
        # First attempt
        return await try_download(retry=False)
    except Exception as e:
        error_str = str(e)
        # Check if the error is related to authentication/cookies
        if "login required" in error_str or "rate-limit reached" in error_str:
            logging.info("Cookie expired or authentication failed, getting fresh cookies...")
            # Second attempt with fresh cookies
            return await try_download(retry=True)
        else:
            # If it's a different error, raise it
            raise

def main():
    """
    Main entry point. Set up the bot handlers and start polling.
    """
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set in environment variables")
        
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # With group privacy off, the following will catch all text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.info("Bot started and ready to process messages")
    application.run_polling()

if __name__ == '__main__':
    main() 