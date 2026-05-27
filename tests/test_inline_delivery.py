from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.inline_delivery import (
    InlineCachedMediaItem,
    build_inline_input_media,
    upload_first_media_to_storage,
)
from src.instagram_video_bot.services.video_downloader import MediaItem, VideoInfo


class _FakeBot:
    def __init__(self):
        self.video_calls = []
        self.photo_calls = []

    async def send_video(self, **kwargs):
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

    item = await upload_first_media_to_storage(_FakeBot(), storage_chat_id=-100, video_info=info)

    assert item == InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")


def test_build_inline_input_media_for_video():
    item = InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")

    media = build_inline_input_media(item)

    assert media.media == "video-file-id"
    assert media.caption == "Caption"
