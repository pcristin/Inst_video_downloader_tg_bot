"""Retryable private-chat staging for reusable Telegram media file IDs."""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field, replace
from typing import Any

from ..config.settings import settings
from .download_models import MediaItem, VideoDownloadError
from .telegram_media_sender import TelegramMediaSender
from .telegram_media_files import (
    effective_upload_limit_bytes,
    media_input,
    validate_media_path,
)
from .telegram_media_retry import (
    build_telegram_timeout_kwargs,
    call_telegram_with_retries,
)


@dataclass
class _UploadState:
    semaphore: asyncio.Semaphore
    chat_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    chat_ready_at: dict[int, float] = field(default_factory=dict)
    chat_waiters: dict[int, int] = field(default_factory=dict)

    def defer(self, chat_id: int, seconds: float) -> None:
        now = asyncio.get_running_loop().time()
        self.chat_ready_at[chat_id] = max(
            self.chat_ready_at.get(chat_id, 0), now + seconds
        )

    async def wait(self, chat_id: int) -> None:
        self.chat_waiters[chat_id] = self.chat_waiters.get(chat_id, 0) + 1
        try:
            async with self.chat_locks.setdefault(chat_id, asyncio.Lock()):
                loop = asyncio.get_running_loop()
                while (delay := self.chat_ready_at.get(chat_id, 0) - loop.time()) > 0:
                    await asyncio.sleep(delay)
                if chat_id in self.chat_ready_at:
                    if self.chat_waiters[chat_id] > 1:
                        # Stagger queued uploads only for this flood-recovery batch.
                        self.chat_ready_at[chat_id] = loop.time() + 1.0
                    else:
                        self.chat_ready_at.pop(chat_id, None)
        finally:
            self.chat_waiters[chat_id] -= 1
            if not self.chat_waiters[chat_id]:
                self.chat_waiters.pop(chat_id)
                self.chat_locks.pop(chat_id, None)


def _upload_state(bot: Any) -> _UploadState:
    # The loop owns its state: bound primitives may point back to it, but no
    # global value keeps that cycle alive after a short-lived runtime closes.
    loop = asyncio.get_running_loop()
    states = getattr(loop, "_telegram_media_upload_states", None)
    if states is None:
        states = {}
        setattr(loop, "_telegram_media_upload_states", states)
    key = id(bot)
    if key not in states:
        try:
            owner = weakref.ref(bot, lambda _ref: states.pop(key, None))
        except TypeError:
            # Some lightweight clients cannot be weak-referenced.
            owner = lambda: bot
        states[key] = (
            owner,
            _UploadState(asyncio.Semaphore(settings.TELEGRAM_MEDIA_STAGE_CONCURRENCY)),
        )
    return states[key][1]


class TelegramMediaStager:
    """Upload local media to a private chat before no-duplicate user delivery."""

    def __init__(self, storage_chat_id: int):
        self.storage_chat_id = storage_chat_id

    async def stage_media(
        self, bot: Any, media_items: list[MediaItem], *, force: bool = False
    ) -> list[MediaItem]:
        """Return media items with durable Telegram IDs, retaining existing IDs."""
        state = _upload_state(bot)

        async def stage(index: int, item: MediaItem) -> None:
            if item.telegram_file_id and not force:
                return
            async with state.semaphore:
                media_items[index] = await self._stage_item(bot, item, force=force)

        tasks = [
            asyncio.create_task(stage(index, item))
            for index, item in enumerate(media_items)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return media_items

    async def _stage_item(
        self, bot: Any, media_item: MediaItem, *, force: bool
    ) -> MediaItem:
        if media_item.telegram_file_id and not force:
            return media_item
        if not media_item.remote_url:
            self._validate_local_media(media_item)

        state = _upload_state(bot)

        async def upload_with_fresh_file(**timeout_kwargs: float):
            await state.wait(self.storage_chat_id)
            if media_item.remote_url:
                if media_item.media_type == "video":
                    return await bot.send_video(
                        chat_id=self.storage_chat_id,
                        video=media_item.remote_url,
                        **self._video_kwargs(media_item),
                        **timeout_kwargs,
                    )
                return await bot.send_photo(
                    chat_id=self.storage_chat_id,
                    photo=media_item.remote_url,
                    **timeout_kwargs,
                )
            with media_input(
                media_item.file_path,
                local_mode=settings.TELEGRAM_LOCAL_MODE,
                max_upload_bytes=effective_upload_limit_bytes(
                    settings.TELEGRAM_LOCAL_MODE,
                    settings.TELEGRAM_MAX_UPLOAD_BYTES,
                ),
                shared_root=settings.TEMP_DIR,
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
            context={
                "storage_chat_id": self.storage_chat_id,
                "media_type": media_item.media_type,
            },
            on_retry_after=lambda seconds: state.defer(self.storage_chat_id, seconds),
        )
        return replace(
            media_item,
            telegram_file_id=self._extract_file_id(message, media_item.media_type),
            remote_url=None,
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
            raise VideoDownloadError(
                "Telegram storage response did not contain a file ID"
            )
        return str(file_id)

    @staticmethod
    def _timeout_kwargs() -> dict[str, float]:
        return build_telegram_timeout_kwargs(
            read_timeout=settings.TELEGRAM_MEDIA_READ_TIMEOUT_SECONDS,
            write_timeout=settings.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS,
            connect_timeout=settings.TELEGRAM_MEDIA_CONNECT_TIMEOUT_SECONDS,
            pool_timeout=settings.TELEGRAM_MEDIA_POOL_TIMEOUT_SECONDS,
        )

    _video_kwargs = staticmethod(TelegramMediaSender.telegram_video_kwargs)
