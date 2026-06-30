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

from ..config.settings import settings
from .download_models import MediaItem, VideoDownloadError, VideoInfo
from .rich_text import RichText, media_caption_rich_text
from .state_store import StateStore
from .telegram_media_retry import (build_telegram_timeout_kwargs,
                                   call_telegram_with_retries)

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
        caption = self.build_caption(video_info.title)

        telegram_file_ids: list[str | None] = []

        if len(media_items) == 1:
            media_item = media_items[0]
            telegram_file_ids.append(
                await self._send_single_media_item(
                    context,
                    request_context,
                    media_item,
                    caption,
                )
            )
            self._persist_telegram_file_ids(request_context, telegram_file_ids)
            return

        self.validate_media_files(
            [
                media_item.file_path
                for media_item in media_items
                if not media_item.telegram_file_id
            ]
        )
        caption_available = caption
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
        caption: RichText | None,
    ) -> str | None:
        if media_item.telegram_file_id:
            try:
                message = await self._send_single_media_value(
                    context,
                    request_context,
                    media_item,
                    caption,
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

        self.validate_media_files([media_item.file_path])

        async def upload_local_media(**timeout_kwargs: float) -> Message:
            with open(media_item.file_path, "rb") as media_file:
                return await self._send_single_media_value(
                    context,
                    request_context,
                    media_item,
                    caption,
                    media_file,
                    timeout_kwargs=timeout_kwargs,
                )

        message = await call_telegram_with_retries(
            upload_local_media,
            attempts=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS,
            backoff_seconds=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS,
            timeout_kwargs=self._telegram_media_timeout_kwargs(),
            context={
                "chat_id": request_context.chat_id,
                "media_type": media_item.media_type,
            },
        )
        return self.extract_telegram_file_id(message, media_item.media_type)

    async def _send_single_media_value(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_item: MediaItem,
        caption: RichText | None,
        media_value: Any,
        *,
        timeout_kwargs: dict[str, float] | None = None,
    ) -> Message:
        timeout_kwargs = timeout_kwargs or {}
        caption_entities = caption.entities if caption and caption.entities else None
        caption_text = caption.text if caption is not None else None
        if media_item.media_type == "video":
            return await context.bot.send_video(
                chat_id=request_context.chat_id,
                video=media_value,
                caption=caption_text,
                caption_entities=caption_entities,
                reply_to_message_id=request_context.original_message_id,
                **self.telegram_video_kwargs(media_item),
                **timeout_kwargs,
            )
        return await context.bot.send_photo(
            chat_id=request_context.chat_id,
            photo=media_value,
            caption=caption_text,
            caption_entities=caption_entities,
            reply_to_message_id=request_context.original_message_id,
            **timeout_kwargs,
        )

    @staticmethod
    def _telegram_media_timeout_kwargs() -> dict[str, float]:
        return build_telegram_timeout_kwargs(
            read_timeout=settings.TELEGRAM_MEDIA_READ_TIMEOUT_SECONDS,
            write_timeout=settings.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS,
            connect_timeout=settings.TELEGRAM_MEDIA_CONNECT_TIMEOUT_SECONDS,
            pool_timeout=settings.TELEGRAM_MEDIA_POOL_TIMEOUT_SECONDS,
        )

    async def _send_media_group_chunk(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_items: list[MediaItem],
        caption: RichText | None,
    ) -> list[str | None]:
        try:
            messages = await self._send_media_group_values(
                context,
                request_context,
                media_items,
                caption,
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
                caption,
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
        caption: RichText | None,
        *,
        force_local_upload: bool = False,
    ) -> list[Message]:
        uses_local_upload = force_local_upload or any(
            not media_item.telegram_file_id for media_item in media_items
        )

        async def send_group(**timeout_kwargs: float) -> list[Message]:
            return await self._send_media_group_values_once(
                context,
                request_context,
                media_items,
                caption,
                force_local_upload=force_local_upload,
                timeout_kwargs=timeout_kwargs,
            )

        if not uses_local_upload:
            return await send_group()

        return await call_telegram_with_retries(
            send_group,
            attempts=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS,
            backoff_seconds=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS,
            timeout_kwargs=self._telegram_media_timeout_kwargs(),
            context={
                "chat_id": request_context.chat_id,
                "media_count": len(media_items),
            },
        )

    async def _send_media_group_values_once(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: MediaRequestContext,
        media_items: list[MediaItem],
        caption: RichText | None,
        *,
        force_local_upload: bool = False,
        timeout_kwargs: dict[str, float] | None = None,
    ) -> list[Message]:
        timeout_kwargs = timeout_kwargs or {}
        with ExitStack() as stack:
            media_group = []
            for index, media_item in enumerate(media_items):
                media_value = (
                    None if force_local_upload else media_item.telegram_file_id
                )
                if not media_value:
                    self.validate_media_files([media_item.file_path])
                    media_value = stack.enter_context(open(media_item.file_path, "rb"))
                item_caption = caption if index == 0 else None
                item_caption_text = (
                    item_caption.text if item_caption is not None else None
                )
                item_caption_entities = (
                    item_caption.entities
                    if item_caption and item_caption.entities
                    else None
                )
                if media_item.media_type == "video":
                    media_group.append(
                        InputMediaVideo(
                            media=media_value,
                            caption=item_caption_text,
                            caption_entities=item_caption_entities,
                            **self.telegram_video_kwargs(media_item),
                        )
                    )
                else:
                    media_group.append(
                        InputMediaPhoto(
                            media=media_value,
                            caption=item_caption_text,
                            caption_entities=item_caption_entities,
                        )
                    )
            return await context.bot.send_media_group(
                chat_id=request_context.chat_id,
                media=media_group,
                reply_to_message_id=request_context.original_message_id,
                **timeout_kwargs,
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
        return cls.build_caption(title).text

    @classmethod
    def build_caption(cls, title: str) -> RichText:
        """Build a Telegram-safe media caption with optional entities."""
        caption = title.strip()
        if not caption:
            return RichText("")
        rich_caption = media_caption_rich_text(caption)
        if len(rich_caption.text) <= cls.MAX_MEDIA_CAPTION_LENGTH:
            return rich_caption
        truncated_text = (
            rich_caption.text[: cls.MAX_MEDIA_CAPTION_LENGTH - 3].rstrip() + "..."
        )
        truncated_utf16_length = len(truncated_text.encode("utf-16-le")) // 2
        entities = [
            entity
            for entity in rich_caption.entities
            if entity.offset + entity.length <= truncated_utf16_length
        ]
        return RichText(truncated_text, entities)

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
