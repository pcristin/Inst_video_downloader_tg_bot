"""True inline media delivery helpers."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dtm
from typing import Literal

from telegram import InputMediaPhoto, InputMediaVideo

from ..config.settings import settings
from .download_models import VideoInfo
from .telegram_media_retry import (
    build_telegram_timeout_kwargs,
    call_telegram_with_retries,
)

MAX_INLINE_MEDIA_CAPTION_LENGTH = 1024


@dataclass(frozen=True)
class InlineCachedMediaItem:
    media_type: Literal["video", "photo"]
    file_id: str
    caption: str | None = None
    duration: float | int | None = None
    width: int | None = None
    height: int | None = None


async def upload_first_media_to_storage(
    bot, *, storage_chat_id: int, video_info: VideoInfo
) -> InlineCachedMediaItem:
    """Upload the first downloaded media item to storage and return its bot-local file_id."""

    first = video_info.media_items[0]
    caption = _truncate_inline_caption(first.caption or video_info.title)
    timeout_kwargs = build_telegram_timeout_kwargs(
        read_timeout=settings.TELEGRAM_MEDIA_READ_TIMEOUT_SECONDS,
        write_timeout=settings.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS,
        connect_timeout=settings.TELEGRAM_MEDIA_CONNECT_TIMEOUT_SECONDS,
        pool_timeout=settings.TELEGRAM_MEDIA_POOL_TIMEOUT_SECONDS,
    )
    if first.media_type == "photo":

        async def send_photo_with_fresh_file(**telegram_timeout_kwargs):
            with first.file_path.open("rb") as media_file:
                return await bot.send_photo(
                    chat_id=storage_chat_id,
                    photo=media_file,
                    caption=caption,
                    **telegram_timeout_kwargs,
                )

        message = await call_telegram_with_retries(
            send_photo_with_fresh_file,
            attempts=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS,
            backoff_seconds=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS,
            timeout_kwargs=timeout_kwargs,
            context={
                "telegram_method": "send_photo",
                "storage_chat_id": storage_chat_id,
            },
        )
        file_id = message.photo[-1].file_id
        return InlineCachedMediaItem(
            media_type="photo", file_id=file_id, caption=caption
        )

    async def send_video_with_fresh_file(**telegram_timeout_kwargs):
        with first.file_path.open("rb") as media_file:
            return await bot.send_video(
                chat_id=storage_chat_id,
                video=media_file,
                caption=caption,
                **_inline_video_kwargs(
                    duration=first.duration,
                    width=first.width,
                    height=first.height,
                ),
                **telegram_timeout_kwargs,
            )

    message = await call_telegram_with_retries(
        send_video_with_fresh_file,
        attempts=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS,
        backoff_seconds=settings.TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS,
        timeout_kwargs=timeout_kwargs,
        context={"telegram_method": "send_video", "storage_chat_id": storage_chat_id},
    )
    return InlineCachedMediaItem(
        media_type="video",
        file_id=message.video.file_id,
        caption=caption,
        duration=first.duration,
        width=first.width,
        height=first.height,
    )


def _truncate_inline_caption(caption: str | None) -> str | None:
    if not caption:
        return None
    caption = caption.strip()
    if not caption:
        return None
    if len(caption) <= MAX_INLINE_MEDIA_CAPTION_LENGTH:
        return caption
    return caption[: MAX_INLINE_MEDIA_CAPTION_LENGTH - 3].rstrip() + "..."


def build_inline_input_media(
    item: InlineCachedMediaItem,
) -> InputMediaPhoto | InputMediaVideo:
    """Build an InputMedia object usable with editMessageMedia for inline messages."""

    if item.media_type == "photo":
        return InputMediaPhoto(media=item.file_id, caption=item.caption)
    if item.media_type == "video":
        return InputMediaVideo(
            media=item.file_id,
            caption=item.caption,
            **_inline_video_kwargs(
                duration=item.duration,
                width=item.width,
                height=item.height,
            ),
        )
    raise ValueError(f"Unsupported inline media type: {item.media_type}")


def _inline_video_kwargs(
    *,
    duration: float | int | None,
    width: int | None,
    height: int | None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {"supports_streaming": True}
    if width:
        kwargs["width"] = int(width)
    if height:
        kwargs["height"] = int(height)
    if duration is not None:
        kwargs["duration"] = dtm.timedelta(seconds=max(0, round(float(duration))))
    return kwargs
