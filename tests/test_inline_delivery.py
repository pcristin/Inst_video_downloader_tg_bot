from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import InputMediaVideo

from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo
from src.instagram_video_bot.services.inline_delivery import (
    InlineCachedMediaItem,
    build_inline_input_media,
    upload_first_media_to_storage,
)


class _FakeBot:
    def __init__(self):
        self.video_calls = []
        self.photo_calls = []

    async def send_video(self, **kwargs):
        kwargs["video_readable_during_call"] = kwargs["video"].readable()
        kwargs["video_closed_during_call"] = kwargs["video"].closed
        kwargs["video_zero_byte_read"] = kwargs["video"].read(0)
        self.video_calls.append(kwargs)
        return SimpleNamespace(video=SimpleNamespace(file_id="video-file-id"), photo=None)

    async def send_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return SimpleNamespace(photo=[SimpleNamespace(file_id="photo-file-id")], video=None)


@pytest.mark.asyncio
async def test_upload_first_video_to_storage_returns_file_id(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[MediaItem(file_path=media_file, media_type="video", caption="Caption")],
        primary_media_type="video",
    )

    bot = _FakeBot()

    item = await upload_first_media_to_storage(bot, storage_chat_id=-100, video_info=info)

    assert item == InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")
    assert len(bot.video_calls) == 1
    call = bot.video_calls[0]
    assert call["chat_id"] == -100
    assert call["caption"] == "Caption"
    assert call["supports_streaming"] is True
    assert call["video_readable_during_call"] is True
    assert call["video_closed_during_call"] is False
    assert call["video_zero_byte_read"] == b""


@pytest.mark.asyncio
async def test_upload_first_media_to_storage_truncates_long_caption(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    long_caption = "x" * 1100
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[MediaItem(file_path=media_file, media_type="video", caption=long_caption)],
        primary_media_type="video",
    )
    bot = _FakeBot()

    item = await upload_first_media_to_storage(bot, storage_chat_id=-100, video_info=info)

    assert item.caption is not None
    assert len(item.caption) == 1024
    assert item.caption.endswith("...")
    assert bot.video_calls[0]["caption"] == item.caption


def test_build_inline_input_media_for_video():
    item = InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")

    media = build_inline_input_media(item)

    assert isinstance(media, InputMediaVideo)
    assert media.media == "video-file-id"
    assert media.caption == "Caption"
    if hasattr(media, "supports_streaming"):
        assert media.supports_streaming is True


def test_build_inline_input_media_rejects_unknown_media_type():
    item = InlineCachedMediaItem(media_type=cast(Any, "audio"), file_id="audio-file-id")

    with pytest.raises(ValueError, match="Unsupported inline media type: audio"):
        build_inline_input_media(item)
