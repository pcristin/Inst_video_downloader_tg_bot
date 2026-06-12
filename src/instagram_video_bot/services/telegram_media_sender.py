"""Telegram media delivery service."""

from __future__ import annotations

import datetime as dtm
import logging
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Protocol

from telegram import InputMediaPhoto, InputMediaVideo, Message
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from .chaos_text import ChaosText
from .download_models import MediaItem, VideoDownloadError, VideoInfo
from .state_store import StateStore

logger = logging.getLogger(__name__)


class MediaRequestContext(Protocol):
    """Request fields required to send and cache Telegram media."""

    chat_id: int
    normalized_url: str
    original_message_id: int


class TelegramMediaSender:
    """Send downloaded media to Telegram and persist reusable file IDs."""

    MAX_MEDIA_CAPTION_LENGTH = 1024
    TELEGRAM_MEDIA_GROUP_LIMIT = 10

    def __init__(self, state_store: StateStore):
        self.state_store = state_store

    async def send_media(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        video_info: VideoInfo,
    ) -> None:
        """Send one media item or a multi-item album based on downloader result."""
        media_items = video_info.media_items
        self.validate_media_files([item.file_path for item in media_items])
        caption_text = self.build_caption_text(video_info.title)

        telegram_file_ids: list[str | None] = []

        if len(media_items) == 1:
            media_item = media_items[0]
            telegram_file_ids.append(
                await self._send_single_media_item(
                    context,
                    request_context,
                    media_item,
                    caption_text,
                )
            )
            self._persist_telegram_file_ids(request_context, telegram_file_ids)
            return

        caption_available = caption_text
        for offset in range(0, len(media_items), self.TELEGRAM_MEDIA_GROUP_LIMIT):
            chunk = media_items[offset : offset + self.TELEGRAM_MEDIA_GROUP_LIMIT]
            chunk_caption = caption_available if offset == 0 else None
            if len(chunk) == 1:
                telegram_file_ids.append(
                    await self._send_single_media_item(
                        context,
                        request_context,
                        chunk[0],
                        chunk_caption,
                    )
                )
            else:
                telegram_file_ids.extend(
                    await self._send_media_group_chunk(
                        context,
                        request_context,
                        chunk,
                        chunk_caption,
                    )
                )
        self._persist_telegram_file_ids(request_context, telegram_file_ids)

    @staticmethod
    def validate_media_files(files: list[Path]) -> None:
        """Validate that all files exist and are non-empty."""
        for file_path in files:
            if not file_path.exists():
                raise VideoDownloadError(f"Media file not found at {file_path}")
            if file_path.stat().st_size == 0:
                raise VideoDownloadError(f"Media file is empty: {file_path}")

    async def _send_single_media_item(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_item: MediaItem,
        caption_text: str | None,
    ) -> str | None:
        if media_item.telegram_file_id:
            try:
                message = await self._send_single_media_value(
                    context,
                    request_context,
                    media_item,
                    caption_text,
                    media_item.telegram_file_id,
                )
                return self.extract_telegram_file_id(message, media_item.media_type)
            except BadRequest as exc:
                if not self.is_rejected_telegram_file_id(exc):
                    raise
                logger.info(
                    "Cached Telegram file_id rejected; retrying local media upload",
                    extra={
                        "chat_id": request_context.chat_id,
                        "media_type": media_item.media_type,
                        "failure_class": "telegram_file_id_rejected",
                    },
                )

        with open(media_item.file_path, "rb") as media_file:
            message = await self._send_single_media_value(
                context,
                request_context,
                media_item,
                caption_text,
                media_file,
            )
        return self.extract_telegram_file_id(message, media_item.media_type)

    async def _send_single_media_value(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_item: MediaItem,
        caption_text: str | None,
        media_value: Any,
    ) -> Message:
        if media_item.media_type == "video":
            return await context.bot.send_video(
                chat_id=request_context.chat_id,
                video=media_value,
                caption=caption_text,
                reply_to_message_id=request_context.original_message_id,
                **self.telegram_video_kwargs(media_item),
            )
        return await context.bot.send_photo(
            chat_id=request_context.chat_id,
            photo=media_value,
            caption=caption_text,
            reply_to_message_id=request_context.original_message_id,
        )

    async def _send_media_group_chunk(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_items: list[MediaItem],
        caption_text: str | None,
    ) -> list[str | None]:
        try:
            messages = await self._send_media_group_values(
                context,
                request_context,
                media_items,
                caption_text,
            )
        except BadRequest as exc:
            if not any(
                item.telegram_file_id for item in media_items
            ) or not self.is_rejected_telegram_file_id(exc):
                raise
            logger.info(
                "Cached Telegram file_id rejected in media group; retrying local media upload",
                extra={
                    "chat_id": request_context.chat_id,
                    "media_count": len(media_items),
                    "failure_class": "telegram_file_id_rejected",
                },
            )
            messages = await self._send_media_group_values(
                context,
                request_context,
                media_items,
                caption_text,
                force_local_upload=True,
            )
        return [
            self.extract_telegram_file_id(message, media_item.media_type)
            for message, media_item in zip(messages or [], media_items)
        ]

    async def _send_media_group_values(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_items: list[MediaItem],
        caption_text: str | None,
        *,
        force_local_upload: bool = False,
    ) -> list[Message]:
        with ExitStack() as stack:
            media_group = []
            for index, media_item in enumerate(media_items):
                media_value = (
                    None if force_local_upload else media_item.telegram_file_id
                )
                if not media_value:
                    media_value = stack.enter_context(open(media_item.file_path, "rb"))
                item_caption = caption_text if index == 0 else None
                if media_item.media_type == "video":
                    media_group.append(
                        InputMediaVideo(
                            media=media_value,
                            caption=item_caption,
                            **self.telegram_video_kwargs(media_item),
                        )
                    )
                else:
                    media_group.append(
                        InputMediaPhoto(media=media_value, caption=item_caption)
                    )
            return await context.bot.send_media_group(
                chat_id=request_context.chat_id,
                media=media_group,
                reply_to_message_id=request_context.original_message_id,
            )

    @staticmethod
    def is_rejected_telegram_file_id(error: TelegramError) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "wrong file identifier",
                "file_id_invalid",
                "file reference expired",
            )
        )

    def _persist_telegram_file_ids(
        self,
        request_context: MediaRequestContext,
        telegram_file_ids: list[str | None],
    ) -> None:
        if not telegram_file_ids or not any(telegram_file_ids):
            return
        self.state_store.update_cached_telegram_file_ids(
            request_context.chat_id,
            request_context.normalized_url,
            telegram_file_ids,
        )

    @staticmethod
    def extract_telegram_file_id(message: Any, media_type: str) -> str | None:
        if media_type == "video":
            video = getattr(message, "video", None)
            return getattr(video, "file_id", None)
        photos = getattr(message, "photo", None)
        if photos:
            return getattr(photos[-1], "file_id", None)
        return None

    @staticmethod
    def cleanup_files(files: list[Path]) -> None:
        """Delete downloaded files safely."""
        for file_path in files:
            try:
                file_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to clean up file %s: %s", file_path, exc)

    @classmethod
    def build_caption_text(cls, title: str) -> str:
        """Build a Telegram-safe media caption."""
        caption = title.strip()
        if not caption:
            return ""
        full_caption = ChaosText.media_caption(caption)
        if len(full_caption) <= cls.MAX_MEDIA_CAPTION_LENGTH:
            return full_caption
        return full_caption[: cls.MAX_MEDIA_CAPTION_LENGTH - 3].rstrip() + "..."

    @staticmethod
    def telegram_video_kwargs(media_item: MediaItem) -> dict[str, object]:
        """Build optional Telegram video metadata from a media item."""
        kwargs: dict[str, object] = {}
        if media_item.width:
            kwargs["width"] = int(media_item.width)
        if media_item.height:
            kwargs["height"] = int(media_item.height)
        if media_item.duration is not None:
            kwargs["duration"] = dtm.timedelta(
                seconds=max(0, round(float(media_item.duration)))
            )
        if media_item.file_path.suffix.lower() in {".mp4", ".mov"}:
            kwargs["supports_streaming"] = True
        return kwargs
