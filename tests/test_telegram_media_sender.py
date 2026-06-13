from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.download_models import (
    MediaItem,
    VideoDownloadError,
    VideoInfo,
)
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_media_sender import \
    TelegramMediaSender


class _FakeBot:
    def __init__(self):
        self.video_calls = []
        self.media_group_calls = []

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return SimpleNamespace(
            video=SimpleNamespace(file_id="standalone-video-file-id"),
            photo=None,
        )

    async def send_media_group(self, **kwargs):
        self.media_group_calls.append(kwargs)
        return [
            SimpleNamespace(video=SimpleNamespace(file_id=f"group-file-id-{index}"))
            for index, _ in enumerate(kwargs["media"])
        ]


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot


def _request_context() -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=77,
        normalized_url="https://www.instagram.com/reel/a/",
        original_message_id=10,
    )


@pytest.mark.asyncio
async def test_media_sender_sends_video_and_persists_telegram_file_id(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()

    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    store.save_cached_result(
        chat_id=request_context.chat_id,
        normalized_url=request_context.normalized_url,
        provider="instagram",
        title="Standalone sender",
        media_items=[
            {
                "file_path": str(media_file),
                "media_type": "video",
                "caption": None,
                "duration": None,
                "width": None,
                "height": None,
            }
        ],
        ttl_seconds=3600,
    )

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=media_file,
            title="Standalone sender",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        ),
    )

    assert fake_bot.video_calls[0]["chat_id"] == request_context.chat_id
    cached = store.get_cached_result(
        request_context.chat_id, request_context.normalized_url
    )
    assert cached is not None
    assert cached.media_items[0]["telegram_file_id"] == "standalone-video-file-id"


@pytest.mark.asyncio
async def test_media_sender_uses_cached_file_id_when_local_file_is_missing(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()
    missing_file = tmp_path / "missing.mp4"

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=missing_file,
            title="Cached sender",
            media_items=[
                MediaItem(
                    file_path=missing_file,
                    media_type="video",
                    telegram_file_id="cached-video-file-id",
                )
            ],
            primary_media_type="video",
        ),
    )

    assert fake_bot.video_calls[0]["video"] == "cached-video-file-id"


@pytest.mark.asyncio
async def test_media_sender_preflights_uncached_album_files_before_chunks(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()
    media_items = []

    for index in range(sender.TELEGRAM_MEDIA_GROUP_LIMIT):
        media_file = tmp_path / f"video-{index}.mp4"
        media_file.write_bytes(b"video")
        media_items.append(MediaItem(file_path=media_file, media_type="video"))

    media_items.append(
        MediaItem(file_path=tmp_path / "missing-later.mp4", media_type="video")
    )

    with pytest.raises(VideoDownloadError, match="Media file not found"):
        await sender.send_media(
            _FakeContext(fake_bot),
            request_context,
            VideoInfo(
                file_path=media_items[0].file_path,
                title="Chunked album",
                media_items=media_items,
                primary_media_type="video",
            ),
        )

    assert fake_bot.media_group_calls == []
