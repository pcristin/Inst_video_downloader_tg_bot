from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import NetworkError

from src.instagram_video_bot.services.download_models import MediaItem
from src.instagram_video_bot.services.telegram_media_stager import TelegramMediaStager


class _FlakyStorageBot:
    def __init__(self):
        self.video_calls = []
        self.stream_ids = []
        self.stream_payloads = []

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        stream = kwargs["video"]
        self.stream_ids.append(id(stream))
        self.stream_payloads.append(stream.read())
        if len(self.video_calls) == 1:
            raise NetworkError("httpx.ReadError")
        return SimpleNamespace(video=SimpleNamespace(file_id="stored-video-id"))


@pytest.mark.asyncio
async def test_stager_retries_storage_video_with_fresh_stream(monkeypatch, tmp_path):
    async def no_sleep(_duration):
        return None

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_media_retry.asyncio.sleep", no_sleep
    )
    video_file = Path(tmp_path / "video.mp4")
    video_file.write_bytes(b"video")
    bot = _FlakyStorageBot()

    staged = await TelegramMediaStager(storage_chat_id=-1001).stage_media(
        bot, [MediaItem(file_path=video_file, media_type="video")]
    )

    assert staged[0].telegram_file_id == "stored-video-id"
    assert bot.stream_payloads == [b"video", b"video"]
    assert bot.stream_ids[0] != bot.stream_ids[1]
    assert all(call["chat_id"] == -1001 for call in bot.video_calls)


@pytest.mark.asyncio
async def test_stager_preserves_existing_file_id_without_upload(tmp_path):
    video_file = Path(tmp_path / "video.mp4")
    item = MediaItem(
        file_path=video_file,
        media_type="video",
        telegram_file_id="cached-video-id",
    )
    bot = _FlakyStorageBot()

    staged = await TelegramMediaStager(storage_chat_id=-1001).stage_media(bot, [item])

    assert staged == [item]
    assert bot.video_calls == []
