import datetime as dtm
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram.error import NetworkError
from telegram import InputMediaVideo

from src.instagram_video_bot.config.settings import settings as app_settings
from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo
from src.instagram_video_bot.services import telegram_media_retry
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
        return SimpleNamespace(
            video=SimpleNamespace(file_id="video-file-id"), photo=None
        )

    async def send_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo-file-id")], video=None
        )


class _ConsumeThenFailOnceBot:
    def __init__(self, *, method_name: str, file_kwarg: str, file_id: str):
        self.method_name = method_name
        self.file_kwarg = file_kwarg
        self.file_id = file_id
        self.calls = []

    async def send_video(self, **kwargs):
        if self.method_name != "send_video":
            raise AssertionError("unexpected send_video call")
        return await self._send(**kwargs)

    async def send_photo(self, **kwargs):
        if self.method_name != "send_photo":
            raise AssertionError("unexpected send_photo call")
        return await self._send(**kwargs)

    async def _send(self, **kwargs):
        media_file = kwargs[self.file_kwarg]
        call = {
            "position_before_read": media_file.tell(),
            "content": media_file.read(),
            "closed_during_call": media_file.closed,
            "kwargs": kwargs,
        }
        self.calls.append(call)
        if len(self.calls) == 1:
            raise NetworkError("httpx.ReadError: ")
        if self.method_name == "send_video":
            return SimpleNamespace(
                video=SimpleNamespace(file_id=self.file_id), photo=None
            )
        return SimpleNamespace(
            photo=[SimpleNamespace(file_id=self.file_id)], video=None
        )


def _configure_fast_retry_timeouts(monkeypatch):
    monkeypatch.setattr(app_settings, "TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(
        app_settings, "TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS", 0.0
    )
    monkeypatch.setattr(app_settings, "TELEGRAM_MEDIA_READ_TIMEOUT_SECONDS", 12.0)
    monkeypatch.setattr(app_settings, "TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS", 34.0)
    monkeypatch.setattr(app_settings, "TELEGRAM_MEDIA_CONNECT_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(app_settings, "TELEGRAM_MEDIA_POOL_TIMEOUT_SECONDS", 6.0)


@pytest.mark.asyncio
async def test_upload_first_video_to_storage_returns_file_id(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[
            MediaItem(file_path=media_file, media_type="video", caption="Caption")
        ],
        primary_media_type="video",
    )

    bot = _FakeBot()

    item = await upload_first_media_to_storage(
        bot, storage_chat_id=-100, video_info=info
    )

    assert item == InlineCachedMediaItem(
        media_type="video", file_id="video-file-id", caption="Caption"
    )
    assert len(bot.video_calls) == 1
    call = bot.video_calls[0]
    assert call["chat_id"] == -100
    assert call["caption"] == "Caption"
    assert call["supports_streaming"] is True
    assert call["video_readable_during_call"] is True
    assert call["video_closed_during_call"] is False
    assert call["video_zero_byte_read"] == b""


@pytest.mark.asyncio
async def test_upload_first_video_to_storage_retries_with_timeouts_and_fresh_file_handles(
    monkeypatch, tmp_path
):
    _configure_fast_retry_timeouts(monkeypatch)
    sleep_durations = []

    async def capture_sleep(duration):
        sleep_durations.append(duration)

    monkeypatch.setattr(telegram_media_retry.asyncio, "sleep", capture_sleep)
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[
            MediaItem(
                file_path=media_file,
                media_type="video",
                caption="Caption",
                duration=9.4,
                width=640,
                height=360,
            )
        ],
        primary_media_type="video",
    )
    bot = _ConsumeThenFailOnceBot(
        method_name="send_video", file_kwarg="video", file_id="retry-video-file-id"
    )

    item = await upload_first_media_to_storage(
        bot, storage_chat_id=-100, video_info=info
    )

    assert item == InlineCachedMediaItem(
        media_type="video",
        file_id="retry-video-file-id",
        caption="Caption",
        duration=9.4,
        width=640,
        height=360,
    )
    assert sleep_durations == [0.0]
    assert [call["position_before_read"] for call in bot.calls] == [0, 0]
    assert [call["content"] for call in bot.calls] == [b"video", b"video"]
    assert [call["closed_during_call"] for call in bot.calls] == [False, False]
    assert bot.calls[1]["kwargs"]["read_timeout"] == 12.0
    assert bot.calls[1]["kwargs"]["write_timeout"] == 34.0
    assert bot.calls[1]["kwargs"]["connect_timeout"] == 5.0
    assert bot.calls[1]["kwargs"]["pool_timeout"] == 6.0
    assert bot.calls[1]["kwargs"]["duration"] == dtm.timedelta(seconds=9)
    assert bot.calls[1]["kwargs"]["width"] == 640
    assert bot.calls[1]["kwargs"]["height"] == 360


@pytest.mark.asyncio
async def test_upload_first_photo_to_storage_retries_with_timeouts_and_fresh_file_handles(
    monkeypatch, tmp_path
):
    _configure_fast_retry_timeouts(monkeypatch)

    async def capture_sleep(_duration):
        return None

    monkeypatch.setattr(telegram_media_retry.asyncio, "sleep", capture_sleep)
    media_file = tmp_path / "photo.jpg"
    media_file.write_bytes(b"photo")
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[
            MediaItem(file_path=media_file, media_type="photo", caption="Photo caption")
        ],
        primary_media_type="photo",
    )
    bot = _ConsumeThenFailOnceBot(
        method_name="send_photo", file_kwarg="photo", file_id="retry-photo-file-id"
    )

    item = await upload_first_media_to_storage(
        bot, storage_chat_id=-100, video_info=info
    )

    assert item == InlineCachedMediaItem(
        media_type="photo", file_id="retry-photo-file-id", caption="Photo caption"
    )
    assert [call["position_before_read"] for call in bot.calls] == [0, 0]
    assert [call["content"] for call in bot.calls] == [b"photo", b"photo"]
    assert bot.calls[1]["kwargs"]["read_timeout"] == 12.0
    assert bot.calls[1]["kwargs"]["write_timeout"] == 34.0
    assert bot.calls[1]["kwargs"]["connect_timeout"] == 5.0
    assert bot.calls[1]["kwargs"]["pool_timeout"] == 6.0


@pytest.mark.asyncio
async def test_upload_first_media_to_storage_truncates_long_caption(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    long_caption = "x" * 1100
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[
            MediaItem(file_path=media_file, media_type="video", caption=long_caption)
        ],
        primary_media_type="video",
    )
    bot = _FakeBot()

    item = await upload_first_media_to_storage(
        bot, storage_chat_id=-100, video_info=info
    )

    assert item.caption is not None
    assert len(item.caption) == 1024
    assert item.caption.endswith("...")
    assert bot.video_calls[0]["caption"] == item.caption


def test_build_inline_input_media_for_video():
    item = InlineCachedMediaItem(
        media_type="video", file_id="video-file-id", caption="Caption"
    )

    media = build_inline_input_media(item)

    assert isinstance(media, InputMediaVideo)
    assert media.media == "video-file-id"
    assert media.caption == "Caption"
    if hasattr(media, "supports_streaming"):
        assert media.supports_streaming is True


@pytest.mark.asyncio
async def test_upload_first_video_to_storage_preserves_portrait_metadata(tmp_path):
    media_file = tmp_path / "portrait.mp4"
    media_file.write_bytes(b"video")
    info = VideoInfo(
        file_path=media_file,
        title="Title",
        media_items=[
            MediaItem(
                file_path=media_file,
                media_type="video",
                caption="Caption",
                duration=11.6,
                width=720,
                height=1280,
            )
        ],
        primary_media_type="video",
    )

    bot = _FakeBot()

    item = await upload_first_media_to_storage(
        bot, storage_chat_id=-100, video_info=info
    )

    assert item == InlineCachedMediaItem(
        media_type="video",
        file_id="video-file-id",
        caption="Caption",
        duration=11.6,
        width=720,
        height=1280,
    )
    assert bot.video_calls[0]["width"] == 720
    assert bot.video_calls[0]["height"] == 1280
    assert bot.video_calls[0]["duration"] == dtm.timedelta(seconds=12)


def test_build_inline_input_media_for_video_preserves_portrait_metadata():
    item = InlineCachedMediaItem(
        media_type="video",
        file_id="video-file-id",
        caption="Caption",
        duration=11.6,
        width=720,
        height=1280,
    )

    media = build_inline_input_media(item)

    assert isinstance(media, InputMediaVideo)
    assert media.width == 720
    assert media.height == 1280
    assert media._duration == dtm.timedelta(seconds=12)


def test_build_inline_input_media_rejects_unknown_media_type():
    item = InlineCachedMediaItem(media_type=cast(Any, "audio"), file_id="audio-file-id")

    with pytest.raises(ValueError, match="Unsupported inline media type: audio"):
        build_inline_input_media(item)
