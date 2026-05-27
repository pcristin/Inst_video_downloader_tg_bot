"""True inline media delivery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from telegram import InputMediaPhoto, InputMediaVideo

from .download_models import VideoInfo


@dataclass(frozen=True)
class InlineCachedMediaItem:
    media_type: Literal["video", "photo"]
    file_id: str
    caption: str | None = None


async def upload_first_media_to_storage(bot, *, storage_chat_id: int, video_info: VideoInfo) -> InlineCachedMediaItem:
    """Upload the first downloaded media item to storage and return its bot-local file_id."""

    first = video_info.media_items[0]
    caption = first.caption or video_info.title
    if first.media_type == "photo":
        with first.file_path.open("rb") as media_file:
            message = await bot.send_photo(chat_id=storage_chat_id, photo=media_file, caption=caption)
        file_id = message.photo[-1].file_id
        return InlineCachedMediaItem(media_type="photo", file_id=file_id, caption=caption)

    with first.file_path.open("rb") as media_file:
        message = await bot.send_video(
            chat_id=storage_chat_id,
            video=media_file,
            caption=caption,
            supports_streaming=True,
        )
    return InlineCachedMediaItem(media_type="video", file_id=message.video.file_id, caption=caption)


def build_inline_input_media(item: InlineCachedMediaItem) -> InputMediaPhoto | InputMediaVideo:
    """Build an InputMedia object usable with editMessageMedia for inline messages."""

    if item.media_type == "photo":
        return InputMediaPhoto(media=item.file_id, caption=item.caption)
    if item.media_type == "video":
        return InputMediaVideo(media=item.file_id, caption=item.caption, supports_streaming=True)
    raise ValueError(f"Unsupported inline media type: {item.media_type}")
