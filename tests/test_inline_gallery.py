from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, NetworkError

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    VideoInfo,
)
from src.instagram_video_bot.services.inline_delivery import InlineCachedMediaItem
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import (
    TelegramBot,
    _inline_media_cache_key,
)

URL = "https://x.com/example/status/123?s=20&ref=original"
NORMALIZED = "https://x.com/example/status/123"


@pytest.fixture
def inline_case(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url=URL,
        normalized_url=NORMALIZED,
        provider="twitter",
        provider_label="Twitter/X",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.claim_inline_delivery("s1", user_id=1001, inline_message_id="inline-msg")
    api = SimpleNamespace(edit_message_text=AsyncMock(), edit_message_media=AsyncMock())
    return TelegramBot(state_store=store), store, SimpleNamespace(bot=api)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["download", "storage", "unknown", "missing"])
async def test_inline_failure_retains_original_source(
    inline_case, monkeypatch, tmp_path, failure
):
    bot, store, context = inline_case
    info = VideoInfo(
        file_path=tmp_path / "photo.jpg",
        title="Title",
        media_items=[MediaItem(file_path=tmp_path / "photo.jpg", media_type="photo")],
    )
    download = AsyncMock(return_value=info)
    upload = AsyncMock(return_value=InlineCachedMediaItem("photo", "photo-id"))
    monkeypatch.setattr(bot, "_download_inline_video_with_retries", download)
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage",
        upload,
    )
    if failure == "download":
        download.side_effect = DownloadError("No media available")
    elif failure == "storage":
        upload.side_effect = NetworkError("temporary upload failure")
    elif failure == "unknown":
        context.bot.edit_message_media.side_effect = NetworkError("connection lost")
    else:
        monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
    await bot._deliver_inline_session(
        context, session_token="s1", one_time_payment_id=None
    )
    assert URL in context.bot.edit_message_text.call_args.kwargs["text"]
    if failure == "storage":
        markup = context.bot.edit_message_text.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "inline-action:retry:s1"


@pytest.mark.asyncio
async def test_inline_gallery_uploads_caches_and_navigates_all_photos(
    inline_case, monkeypatch, tmp_path
):
    bot, store, context = inline_case
    info = VideoInfo(
        file_path=tmp_path / "0.jpg",
        title="Title",
        media_items=[
            MediaItem(file_path=tmp_path / f"{i}.jpg", media_type="photo")
            for i in range(4)
        ],
    )
    monkeypatch.setattr(
        bot, "_download_inline_video_with_retries", AsyncMock(return_value=info)
    )
    upload = AsyncMock(
        side_effect=[InlineCachedMediaItem("photo", f"photo-{i}") for i in range(4)]
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage",
        upload,
    )
    await bot._deliver_inline_session(
        context, session_token="s1", one_time_payment_id=None
    )
    cached = store.get_inline_cached_media(
        _inline_media_cache_key("twitter", NORMALIZED)
    )
    assert [m["file_id"] for m in cached["media_items"]] == [
        f"photo-{i}" for i in range(4)
    ]
    assert upload.await_count == 4
    kwargs = context.bot.edit_message_media.call_args.kwargs
    assert kwargs["media"].media == "photo-0"
    next_button = kwargs["reply_markup"].inline_keyboard[0][-1]
    query = SimpleNamespace(
        data=next_button.callback_data,
        inline_message_id="inline-msg",
        from_user=SimpleNamespace(id=2002),
        answer=AsyncMock(),
    )
    # Any recipient can browse an already delivered gallery, including after restart.
    restarted = TelegramBot(state_store=StateStore(store.db_path))
    await restarted.inline_gallery_callback_handler(
        SimpleNamespace(callback_query=query), context
    )
    assert context.bot.edit_message_media.call_args.kwargs["media"].media == "photo-1"
    assert store.get_inline_session("s1")["status"] == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,message_id",
    [
        ("inline-gallery:s1:99", "inline-msg"),
        ("inline-gallery:s1:1", "different-message"),
        ("inline-gallery:s1:-1", "inline-msg"),
    ],
)
async def test_gallery_rejects_invalid_or_mismatched_callbacks(
    inline_case, data, message_id
):
    bot, store, context = inline_case
    store.finish_inline_delivery("s1", status="delivered")
    store.save_inline_cached_media(
        cache_key=_inline_media_cache_key("twitter", NORMALIZED),
        provider="twitter",
        normalized_url=NORMALIZED,
        media_items=[
            {"media_type": "photo", "file_id": "one"},
            {"media_type": "photo", "file_id": "two"},
        ],
    )
    query = SimpleNamespace(
        data=data,
        inline_message_id=message_id,
        from_user=SimpleNamespace(id=1001),
        answer=AsyncMock(),
    )
    await bot.inline_gallery_callback_handler(
        SimpleNamespace(callback_query=query), context
    )
    context.bot.edit_message_media.assert_not_called()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cached_gallery_skips_download_and_upload(inline_case, monkeypatch):
    bot, store, context = inline_case
    store.save_inline_cached_media(
        cache_key=_inline_media_cache_key("twitter", NORMALIZED),
        provider="twitter",
        normalized_url=NORMALIZED,
        media_items=[
            {"media_type": "photo", "file_id": "one"},
            {"media_type": "photo", "file_id": "two"},
        ],
    )
    download = AsyncMock(side_effect=AssertionError("must reuse cache"))
    monkeypatch.setattr(bot, "_download_inline_video_with_retries", download)
    await bot._deliver_inline_session(
        context, session_token="s1", one_time_payment_id=None
    )
    download.assert_not_called()
    assert store.get_inline_session("s1")["status"] == "delivered"
    assert (
        context.bot.edit_message_media.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][1]
        .text
        == "1/2"
    )


@pytest.mark.asyncio
async def test_partial_gallery_upload_does_not_cache_or_mark_delivered(
    inline_case, monkeypatch, tmp_path
):
    bot, store, context = inline_case
    info = VideoInfo(
        file_path=tmp_path / "0.jpg",
        title="Title",
        media_items=[
            MediaItem(file_path=tmp_path / f"{i}.jpg", media_type="photo")
            for i in range(2)
        ],
    )
    monkeypatch.setattr(
        bot, "_download_inline_video_with_retries", AsyncMock(return_value=info)
    )
    upload = AsyncMock(
        side_effect=[
            InlineCachedMediaItem("photo", "one"),
            NetworkError("upload failed"),
        ]
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage",
        upload,
    )
    await bot._deliver_inline_session(
        context, session_token="s1", one_time_payment_id=None
    )
    assert store.get_inline_session("s1")["status"] == "failed"
    assert (
        store.get_inline_cached_media(_inline_media_cache_key("twitter", NORMALIZED))
        is None
    )
    context.bot.edit_message_media.assert_not_called()
    assert URL in context.bot.edit_message_text.call_args.kwargs["text"]
