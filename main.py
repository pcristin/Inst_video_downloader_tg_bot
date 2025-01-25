import os
import re
import tempfile
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)

from yt_dlp import YoutubeDL

from config import BOT_TOKEN

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
    ydl_opts = {
        'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
        'format': 'mp4',
        'usenetrc': True  # Enable .netrc authentication
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True, process=True)
        return ydl.prepare_filename(info)

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

        # Ensure the chat context is available
        if update.effective_chat is None:
            logging.error("No chat context available for this update.")
            return

        # Create a temporary directory to hold our download
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                # Download the video
                video_file_path = await download_instagram_video(url, tmp_dir)

                # Send the video, replying to the original message
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=open(video_file_path, 'rb'),
                    reply_to_message_id=update.message.message_id
                )

            except Exception as e:
                logging.error(f"Error while downloading or sending video: {e}")
                await update.message.reply_text("Sorry, I couldn't download that video.")
                return

            # The file will be automatically cleaned when tmp_dir is removed

def main():
    """
    Main entry point. Set up the bot handlers and start polling.
    """
    application = ApplicationBuilder().token(BOT_TOKEN).build() # type: ignore

    # With group privacy off, the following will catch all text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main() 