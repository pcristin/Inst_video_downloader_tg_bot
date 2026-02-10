"""Telegram bot service for handling Instagram video downloads."""
from contextlib import ExitStack
import re
import logging
from pathlib import Path
from typing import List
from typing import Optional

from telegram import InputMediaPhoto, InputMediaVideo, Message, Update
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)

from ..config.settings import settings
from .video_downloader import VideoDownloadError, VideoDownloader, VideoInfo

logger = logging.getLogger(__name__)

class TelegramBot:
    """Telegram bot for downloading Instagram videos."""

    # Instagram URL pattern (supports posts, reels, tv, stories, share links, and ddinstagram aliases)
    INSTAGRAM_VIDEO_PATTERN = re.compile(
        r"(https?://(?:www\.|d\.|g\.)?(?:instagram\.com|ddinstagram\.com)/[^ ]+)"
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

        status_message: Optional[Message] = None
        downloaded_files: List[Path] = []
        try:
            # Inform user that download is in progress
            status_message = await update.message.reply_text(
                "🔄 Downloading media... Please wait."
            )

            # Download the media
            video_info = await self.video_downloader.download_video(url=url, output_dir=settings.TEMP_DIR)
            media_items = video_info.media_items
            if not media_items:
                raise VideoDownloadError("No media items were downloaded")

            downloaded_files = [item.file_path for item in media_items]
            self._validate_media_files(downloaded_files)
            await self._send_media(context, update, video_info)

            # Clean up
            if status_message:
                await status_message.delete()

        except VideoDownloadError as e:
            error_str = str(e).lower()
            if "authentication failed" in error_str or "cookies have expired" in error_str:
                error_message = (
                    "🔐 Instagram authentication failed. The session has expired.\n"
                    "The bot administrator needs to refresh the cookies.\n"
                    "Please try again later."
                )
            elif "rate-limit" in error_str:
                error_message = (
                    "⏳ Instagram rate limit reached. Please wait a few minutes and try again."
                )
            else:
                error_message = f"❌ Sorry, couldn't download the media: {str(e)}"
            
            logger.error(f"Download error for {url}: {str(e)}")
            await self._handle_error(update.message, status_message, error_message)

        except Exception as e:
            error_message = "❌ An unexpected error occurred. Please try again later."
            logger.exception(f"Unexpected error while processing {url}: {str(e)}")
            await self._handle_error(update.message, status_message, error_message)
        finally:
            self._cleanup_files(downloaded_files)

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

    async def _global_error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle unhandled Telegram polling/runtime exceptions."""
        error = context.error
        if isinstance(error, NetworkError):
            logger.warning(
                "Transient Telegram network error",
                extra={"failure_class": "telegram_network", "error": str(error)},
            )
            return
        if isinstance(error, TelegramError):
            logger.error(
                "Telegram API error",
                extra={"failure_class": "telegram_api", "error": str(error)},
            )
            return

        logger.exception(
            "Unhandled Telegram runtime error",
            extra={"failure_class": "telegram_unhandled", "error": str(error)},
        )

    @staticmethod
    def _validate_media_files(files: List[Path]) -> None:
        """Validate that all files exist and are non-empty."""
        for file_path in files:
            if not file_path.exists():
                raise VideoDownloadError(f"Media file not found at {file_path}")
            if file_path.stat().st_size == 0:
                raise VideoDownloadError(f"Media file is empty: {file_path}")

    async def _send_media(
        self, context: ContextTypes.DEFAULT_TYPE, update: Update, video_info: VideoInfo
    ) -> None:
        """Send one media item or a multi-item album based on downloader result."""
        media_items = video_info.media_items
        caption = video_info.title.strip()
        caption_text = f"📹 {caption}" if caption else ""

        if len(media_items) == 1:
            media_item = media_items[0]
            with open(media_item.file_path, "rb") as media_file:
                if media_item.media_type == "video":
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=media_file,
                        caption=caption_text,
                        reply_to_message_id=update.message.message_id,
                    )
                else:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=media_file,
                        caption=caption_text,
                        reply_to_message_id=update.message.message_id,
                    )
            return

        with ExitStack() as stack:
            media_group = []
            for index, media_item in enumerate(media_items):
                media_file = stack.enter_context(open(media_item.file_path, "rb"))
                item_caption = caption_text if index == 0 else None
                if media_item.media_type == "video":
                    media_group.append(InputMediaVideo(media=media_file, caption=item_caption))
                else:
                    media_group.append(InputMediaPhoto(media=media_file, caption=item_caption))

            await context.bot.send_media_group(
                chat_id=update.effective_chat.id,
                media=media_group,
                reply_to_message_id=update.message.message_id,
            )

    @staticmethod
    def _cleanup_files(files: List[Path]) -> None:
        """Delete downloaded files safely."""
        for file_path in files:
            try:
                file_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to clean up file %s: %s", file_path, exc)

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
        self.application.add_error_handler(self._global_error_handler)

        logger.info("Bot started and ready to process messages")
        self.application.run_polling() 
