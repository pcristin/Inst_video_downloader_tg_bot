"""Telegram bot service for handling Instagram video downloads."""
import re
import logging
from pathlib import Path
from typing import Optional

from telegram import Update, Message
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)

from ..config.settings import settings
from .video_downloader import VideoDownloader, VideoDownloadError

logger = logging.getLogger(__name__)

class TelegramBot:
    """Telegram bot for downloading Instagram videos."""

    # Instagram video/reel URL pattern
    INSTAGRAM_VIDEO_PATTERN = re.compile(
        r"(https?://(?:www\.)?instagram\.com/(?:p|reel)/[^ ]+)"
    )

    def __init__(self):
        """Initialize the Telegram bot with required services."""
        self.video_downloader = VideoDownloader()
        self.application: Optional[Application] = None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle incoming messages and process Instagram video links.
        
        Args:
            update: Telegram update object
            context: Telegram context object
        """
        if not update.message or not update.message.text or not update.effective_chat:
            return

        message_text = update.message.text
        match = self.INSTAGRAM_VIDEO_PATTERN.search(message_text)

        if not match:
            return

        url = match.group(1)
        logger.info(f"Processing Instagram URL: {url}")

        try:
            # Inform user that download is in progress
            status_message = await update.message.reply_text(
                "🔄 Downloading video... Please wait."
            )

            # Download the video
            video_info = await self.video_downloader.download_video(
                url=url,
                output_dir=settings.TEMP_DIR
            )

            # Send the video
            with open(video_info.file_path, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=video_file,
                    caption=f"📹 {video_info.title}" if video_info.title else "",
                    reply_to_message_id=update.message.message_id
                )

            # Clean up
            video_info.file_path.unlink(missing_ok=True)
            if status_message:
                await status_message.delete()

        except VideoDownloadError as e:
            error_message = f"❌ Sorry, couldn't download the video: {str(e)}"
            logger.error(error_message)
            await self._handle_error(update.message, status_message, error_message)

        except Exception as e:
            error_message = "❌ An unexpected error occurred. Please try again later."
            logger.exception(f"Unexpected error while processing {url}: {str(e)}")
            await self._handle_error(update.message, status_message, error_message)

    async def _handle_error(
        self,
        original_message: Message,
        status_message: Optional[Message],
        error_message: str
    ) -> None:
        """
        Handle errors by sending appropriate messages to the user.
        
        Args:
            original_message: Original message that triggered the error
            status_message: Status message to be updated/deleted
            error_message: Error message to send to the user
        """
        if status_message:
            try:
                await status_message.delete()
            except Exception:
                pass

        if original_message:
            try:
                await original_message.reply_text(error_message)
            except Exception as e:
                logger.error(f"Failed to send error message: {str(e)}")

    def run(self) -> None:
        """Start the Telegram bot."""
        if not settings.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is not set in environment variables")

        self.application = (
            ApplicationBuilder()
            .token(settings.BOT_TOKEN)
            .build()
        )

        # Add message handler
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_message
            )
        )

        logger.info("Bot started and ready to process messages")
        self.application.run_polling() 