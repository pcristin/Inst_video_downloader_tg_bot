"""Retryable private-chat staging for reusable Telegram media file IDs."""

from __future__ import annotations

import datetime as dtm
from dataclasses import replace
from typing import Any

from ..config.settings import settings
from .download_models import MediaItem
from .telegram_media_files import (
    effective_upload_limit_bytes,
    media_input,
    validate_media_path,
)
from .telegram_media_retry import (build_telegram_timeout_kwargs,
                                   call_telegram_with_retries)


class TelegramMediaStager:
    """Upload local media to a private chat before no-duplicate user delivery."""

    def __init__(self, storage_chat_id: int):
        self.storage_chat_id = storage_chat_id

    async def stage_media(
        self, bot: Any, media_items: list[MediaItem], *, force: bool = False
    ) -> list[MediaItem]:
        """Return media items with durable Telegram IDs, retaining existing IDs."""
        return [await self._stage_item(bot, item, force=force) for item in media_items]

    async def _stage_item(self, bot: Any, media_item: MediaItem, *, force: bool) -> MediaItem:
        if media_item.telegram_file_id and not force:
            return media_item
        self._validate_local_media(media_item)

        async def upload_with_fresh_file(**timeout_kwargs: float):
            with media_input(
                media_item.file_path,
                local_mode=settings.TELEGRAM_LOCAL_MODE,
                max_upload_bytes=effective_upload_limit_bytes(
                    settings.TELEGRAM_LOCAL_MODE,
                    settings.TELEGRAM_MAX_UPLOAD_BYTES,
                ),
            ) as media_file:
                if media_item.media_type == "video":
                    return await bot.send_video(
                        chat_id=self.storage_chat_id,
                        video=media_file,
                        **self._video_kwargs(media_item),
                        **timeout_kwargs,
                    )
                return await bot.send_photo(
                    chat_id=self.storage_chat_id,
                    photo=media_file,
                    **timeout_kwargs,
                )

        message = await call_telegram_with_retries(
            upload_with_fresh_file,
            attempts=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS,
            backoff_seconds=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS,
            timeout_kwargs=self._timeout_kwargs(),
            context={"storage_chat_id": self.storage_chat_id, "media_type": media_item.media_type},
        )
        return replace(
            media_item,
            telegram_file_id=self._extract_file_id(message, media_item.media_type),
        )

    @staticmethod
    def _validate_local_media(media_item: MediaItem) -> None:
        validate_media_path(
            media_item.file_path,
            max_upload_bytes=effective_upload_limit_bytes(
                settings.TELEGRAM_LOCAL_MODE,
                settings.TELEGRAM_MAX_UPLOAD_BYTES,
            ),
        )

    @staticmethod
    def _extract_file_id(message: Any, media_type: str) -> str:
        if media_type == "video":
            file_id = getattr(getattr(message, "video", None), "file_id", None)
        else:
            photos = getattr(message, "photo", None)
            file_id = getattr(photos[-1], "file_id", None) if photos else None
        if not file_id:
            raise VideoDownloadError("Telegram storage response did not contain a file ID")
        return str(file_id)

    @staticmethod
    def _timeout_kwargs() -> dict[str, float]:
        return build_telegram_timeout_kwargs(
            read_timeout=settings.TELEGRAM_MEDIA_READ_TIMEOUT_SECONDS,
            write_timeout=settings.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS,
            connect_timeout=settings.TELEGRAM_MEDIA_CONNECT_TIMEOUT_SECONDS,
            pool_timeout=settings.TELEGRAM_MEDIA_POOL_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _video_kwargs(media_item: MediaItem) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        if media_item.width:
            kwargs["width"] = media_item.width
        if media_item.height:
            kwargs["height"] = media_item.height
        if media_item.duration is not None:
            kwargs["duration"] = dtm.timedelta(
                seconds=max(0, round(float(media_item.duration)))
            )
        if media_item.file_path.suffix.lower() in {".mp4", ".mov"}:
            kwargs["supports_streaming"] = True
        return kwargs
