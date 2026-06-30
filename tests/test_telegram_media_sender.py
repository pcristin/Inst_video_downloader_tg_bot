from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import MessageEntity
from telegram.error import NetworkError

from src.instagram_video_bot.services import telegram_media_retry
from src.instagram_video_bot.services.download_models import (
    MediaItem, VideoDownloadError, VideoInfo)
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_media_sender import \
    TelegramMediaSender


class _FakeBot:
    def __init__(self):
        self.video_calls = []
        self.photo_calls = []
        self.media_group_calls = []

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return SimpleNamespace(
            video=SimpleNamespace(file_id="standalone-video-file-id"),
            photo=None,
        )

    async def send_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return SimpleNamespace(
            video=None,
            photo=[SimpleNamespace(file_id="standalone-photo-file-id")],
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


class _FlakyLocalVideoBot(_FakeBot):
    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        if len(self.video_calls) == 1:
            raise NetworkError("httpx.ReadError: ")
        return SimpleNamespace(
            video=SimpleNamespace(file_id="retried-video-file-id"),
            photo=None,
        )


class _ReadingFlakyVideoBot(_FakeBot):
    def __init__(self):
        super().__init__()
        self.payloads = []
        self.stream_ids = []

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        media_file = kwargs["video"]
        self.stream_ids.append(id(media_file))
        self.payloads.append(media_file.read())
        if len(self.video_calls) == 1:
            raise NetworkError("httpx.ReadError: ")
        return SimpleNamespace(
            video=SimpleNamespace(file_id="fresh-stream-video-file-id"),
            photo=None,
        )


class _ReadingFlakyMediaGroupBot(_FakeBot):
    def __init__(self):
        super().__init__()
        self.media_object_ids = []
        self.input_file_ids = []
        self.payloads = []

    async def send_media_group(self, **kwargs):
        self.media_group_calls.append(kwargs)
        media_items = kwargs["media"]
        self.media_object_ids.append([id(item) for item in media_items])
        self.input_file_ids.append([id(item.media) for item in media_items])
        self.payloads.append([item.media.input_file_content for item in media_items])
        if len(self.media_group_calls) == 1:
            raise NetworkError("httpx.ReadError: ")
        return [
            SimpleNamespace(
                video=SimpleNamespace(file_id=f"retried-group-file-id-{index}"),
                photo=None,
            )
            for index, _ in enumerate(media_items)
        ]


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
async def test_media_sender_adds_caption_entities_to_single_video(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=media_file,
            title="Captioned video",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        ),
    )

    call = fake_bot.video_calls[0]
    assert call["caption"] == "Медиа: Captioned video"
    assert [
        (entity.type, entity.offset, entity.length)
        for entity in call["caption_entities"]
    ] == [
        (MessageEntity.BOLD, 0, len("Медиа:")),
    ]
    assert "parse_mode" not in call


@pytest.mark.asyncio
async def test_media_sender_adds_caption_entities_to_single_photo(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()
    media_file = tmp_path / "photo.jpg"
    media_file.write_bytes(b"photo")

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=media_file,
            title="Captioned photo",
            media_items=[MediaItem(file_path=media_file, media_type="photo")],
            primary_media_type="photo",
        ),
    )

    call = fake_bot.photo_calls[0]
    assert call["caption"] == "Медиа: Captioned photo"
    assert [
        (entity.type, entity.offset, entity.length)
        for entity in call["caption_entities"]
    ] == [
        (MessageEntity.BOLD, 0, len("Медиа:")),
    ]
    assert "parse_mode" not in call


@pytest.mark.asyncio
async def test_media_sender_adds_caption_entities_to_media_group_first_item(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"photo")
    second.write_bytes(b"video")

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=first,
            title="Captioned album",
            media_items=[
                MediaItem(file_path=first, media_type="photo"),
                MediaItem(file_path=second, media_type="video"),
            ],
            primary_media_type="photo",
        ),
    )

    sent_media = fake_bot.media_group_calls[0]["media"]
    assert sent_media[0].caption == "Медиа: Captioned album"
    assert [
        (entity.type, entity.offset, entity.length)
        for entity in sent_media[0].caption_entities
    ] == [
        (MessageEntity.BOLD, 0, len("Медиа:")),
    ]
    assert sent_media[1].caption is None
    assert sent_media[1].caption_entities == ()


@pytest.mark.asyncio
async def test_media_sender_retries_flaky_local_video_upload_with_timeout_kwargs(
    monkeypatch, tmp_path
):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FlakyLocalVideoBot()
    request_context = _request_context()
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")

    async def no_sleep(_duration):
        return None

    monkeypatch.setattr(telegram_media_retry.asyncio, "sleep", no_sleep)

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=media_file,
            title="Flaky local sender",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        ),
    )

    assert len(fake_bot.video_calls) == 2
    for call in fake_bot.video_calls:
        assert call["write_timeout"] == 60.0
        assert call["read_timeout"] == 120.0
        assert call["connect_timeout"] == 20.0
        assert call["pool_timeout"] == 30.0


@pytest.mark.asyncio
async def test_media_sender_reopens_local_video_stream_for_each_retry(
    monkeypatch, tmp_path
):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _ReadingFlakyVideoBot()
    request_context = _request_context()
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")

    async def no_sleep(_duration):
        return None

    monkeypatch.setattr(telegram_media_retry.asyncio, "sleep", no_sleep)

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=media_file,
            title="Fresh stream sender",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        ),
    )

    assert fake_bot.payloads == [b"video", b"video"]
    assert fake_bot.stream_ids[0] != fake_bot.stream_ids[1]


@pytest.mark.asyncio
async def test_media_sender_retries_flaky_local_media_group_with_fresh_media(
    monkeypatch, tmp_path
):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _ReadingFlakyMediaGroupBot()
    request_context = _request_context()
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first-video")
    second.write_bytes(b"second-video")

    async def no_sleep(_duration):
        return None

    monkeypatch.setattr(telegram_media_retry.asyncio, "sleep", no_sleep)

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=first,
            title="Flaky album",
            media_items=[
                MediaItem(file_path=first, media_type="video"),
                MediaItem(file_path=second, media_type="video"),
            ],
            primary_media_type="video",
        ),
    )

    assert len(fake_bot.media_group_calls) == 2
    assert fake_bot.payloads == [
        [b"first-video", b"second-video"],
        [b"first-video", b"second-video"],
    ]
    assert fake_bot.media_object_ids[0] != fake_bot.media_object_ids[1]
    assert fake_bot.input_file_ids[0] != fake_bot.input_file_ids[1]


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


@pytest.mark.asyncio
async def test_media_sender_allows_cached_album_file_ids_without_local_files(tmp_path):
    store = StateStore(tmp_path / "state.db")
    sender = TelegramMediaSender(store)
    fake_bot = _FakeBot()
    request_context = _request_context()
    media_file = tmp_path / "present.mp4"
    missing_file = tmp_path / "cached-missing.mp4"
    media_file.write_bytes(b"video")

    await sender.send_media(
        _FakeContext(fake_bot),
        request_context,
        VideoInfo(
            file_path=media_file,
            title="Cached album item",
            media_items=[
                MediaItem(file_path=media_file, media_type="video"),
                MediaItem(
                    file_path=missing_file,
                    media_type="video",
                    telegram_file_id="cached-album-file-id",
                ),
            ],
            primary_media_type="video",
        ),
    )

    assert len(fake_bot.media_group_calls) == 1
    sent_media = fake_bot.media_group_calls[0]["media"]
    assert len(sent_media) == 2
    assert sent_media[0].media.filename == "present.mp4"
    assert sent_media[1].media == "cached-album-file-id"
