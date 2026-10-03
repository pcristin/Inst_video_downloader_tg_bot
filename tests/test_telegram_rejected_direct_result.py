"""Expired direct-result IDs must recover from the provider without duplicate sends."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    VideoInfo,
)
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import TelegramBot
from src.instagram_video_bot.services.telegram_media_sender import (
    RejectedTelegramFileIdError,
)
from src.instagram_video_bot.services import telegram_bot as module


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(settings, "RESULT_CACHE_ENABLED", True)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    request = SimpleNamespace(
        chat_id=77,
        normalized_url="https://www.instagram.com/p/ABC/",
        original_url="https://www.instagram.com/p/ABC/?utm_source=test",
    )
    placeholder = tmp_path / ".direct_sources" / "0"
    info = VideoInfo(
        file_path=placeholder,
        title="old",
        from_cache=True,
        media_items=[
            MediaItem(
                file_path=placeholder, media_type="video", telegram_file_id="invalid-id"
            )
        ],
    )
    for chat_id in (77, 88):
        store.save_cached_result(
            chat_id,
            request.normalized_url,
            "instagram",
            "old",
            [
                {
                    "file_path": str(placeholder),
                    "media_type": "video",
                    "telegram_file_id": "invalid-id",
                }
            ],
            3600,
        )
    sends = []

    async def send(context, request, candidate, **kwargs):
        sends.append([item.telegram_file_id for item in candidate.media_items])
        if len(sends) == 1:
            raise RejectedTelegramFileIdError("wrong file identifier")
        assert all(item.file_path.is_file() for item in candidate.media_items)

    async def stage(context, items, **kwargs):
        assert kwargs["force"] is True
        for index, item in enumerate(items):
            assert item.file_path.is_file()
            item.telegram_file_id = f"new-id-{index}"
        return items

    async def download(url, output_dir):
        assert url == request.original_url
        assert output_dir.parent == tmp_path
        assert output_dir != tmp_path
        assert store.get_cached_result(77, request.normalized_url) is None
        paths = [output_dir / "1.jpg", output_dir / "2.jpg"]
        for path in paths:
            path.write_bytes(b"photo")
        return VideoInfo(
            file_path=paths[0],
            title="new album",
            primary_media_type="photo",
            media_items=[
                MediaItem(file_path=path, media_type="photo") for path in paths
            ],
        )

    downloader = SimpleNamespace(download_video=AsyncMock(side_effect=download))
    monkeypatch.setattr(module, "VideoDownloader", lambda: downloader)
    bot.media_sender = SimpleNamespace(send_media=AsyncMock(side_effect=send))
    bot.media_stager = SimpleNamespace(stage_media=AsyncMock(side_effect=stage))
    return SimpleNamespace(
        bot=bot,
        store=store,
        request=request,
        info=info,
        sends=sends,
        downloader=downloader,
        context=SimpleNamespace(bot=object()),
    )


@pytest.mark.asyncio
async def test_rejected_direct_id_reacquires_full_manifest_and_updates_shared_result(
    recovery,
):
    env = recovery
    await env.bot._send_staged_media(env.context, env.request, env.info)
    assert env.sends == [["invalid-id"], ["new-id-0", "new-id-1"]]
    assert env.info.title == "new album"
    assert env.info.primary_media_type == "photo"
    assert len(env.info.media_items) == 2
    assert env.info.from_cache is False
    assert all(item.file_path.is_file() for item in env.info.media_items)
    cached = env.store.get_cached_result(77, env.request.normalized_url)
    assert cached.title == "new album"
    assert len(cached.media_items) == 2
    assert [item["telegram_file_id"] for item in cached.media_items] == [
        "new-id-0",
        "new-id-1",
    ]
    assert env.store.get_cached_result(88, env.request.normalized_url).title == "old"
    # Another delivery owner receives the updated shared result.
    await env.bot._send_staged_media(env.context, env.request, env.info)
    assert env.sends[-1] == ["new-id-0", "new-id-1"]
    assert env.downloader.download_video.await_count == 1


@pytest.mark.asyncio
async def test_failed_reacquisition_invalidates_bad_cache_without_user_retry(recovery):
    env = recovery
    env.downloader.download_video.side_effect = DownloadError("provider unavailable")
    with pytest.raises(DownloadError, match="provider unavailable"):
        await env.bot._send_staged_media(env.context, env.request, env.info)
    assert env.store.get_cached_result(77, env.request.normalized_url) is None
    assert env.sends == [["invalid-id"]]
    assert not list(settings.TEMP_DIR.glob("telegram-restage-*"))
    env.bot.media_stager.stage_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_rejection_does_not_reacquire_or_invalidate(recovery):
    env = recovery
    error = RejectedTelegramFileIdError("later album chunk")
    error.telegram_user_send_ambiguous = True
    env.bot.media_sender.send_media.side_effect = error
    with pytest.raises(RejectedTelegramFileIdError):
        await env.bot._send_staged_media(env.context, env.request, env.info)
    env.downloader.download_video.assert_not_awaited()
    env.bot.media_stager.stage_media.assert_not_awaited()
    assert env.store.get_cached_result(77, env.request.normalized_url) is not None


def test_invalidate_result_targets_chat_and_url_without_deleting_files(tmp_path):
    store = StateStore(tmp_path / "state.db")
    path = tmp_path / "retained.mp4"
    path.write_bytes(b"video")
    for chat_id, url in ((1, "one"), (2, "one"), (1, "two")):
        store.save_cached_result(
            chat_id,
            url,
            "instagram",
            "title",
            [{"file_path": str(path), "media_type": "video"}],
            3600,
        )
    store.invalidate_cached_result(1, "one")
    assert store.get_cached_result(1, "one") is None
    assert store.get_cached_result(2, "one") is not None
    assert store.get_cached_result(1, "two") is not None
    assert path.is_file()


@pytest.mark.asyncio
async def test_rejected_reacquired_id_has_only_one_user_retry(recovery):
    env = recovery
    env.bot.media_sender.send_media.side_effect = RejectedTelegramFileIdError(
        "wrong file identifier"
    )
    with pytest.raises(RejectedTelegramFileIdError):
        await env.bot._send_staged_media(env.context, env.request, env.info)
    assert env.downloader.download_video.await_count == 1
    assert env.bot.media_stager.stage_media.await_count == 1
    assert env.bot.media_sender.send_media.await_count == 2
    assert not any(item.file_path.is_file() for item in env.info.media_items)
    assert env.store.get_cached_result(77, env.request.normalized_url) is None


@pytest.mark.asyncio
async def test_failed_private_restage_keeps_rejected_cache_invalid(recovery):
    env = recovery
    env.bot.media_stager.stage_media.side_effect = DownloadError("storage unavailable")
    with pytest.raises(DownloadError, match="storage unavailable"):
        await env.bot._send_staged_media(env.context, env.request, env.info)
    assert env.store.get_cached_result(77, env.request.normalized_url) is None
    assert env.sends == [["invalid-id"]]


@pytest.mark.asyncio
async def test_failed_restage_removes_partial_recovery_directory(recovery):
    env = recovery
    env.bot.media_stager.stage_media.side_effect = DownloadError("storage unavailable")
    with pytest.raises(DownloadError):
        await env.bot._send_staged_media(env.context, env.request, env.info)
    assert not list(settings.TEMP_DIR.glob("telegram-restage-*"))


@pytest.mark.asyncio
async def test_successful_uncached_recovery_releases_files(recovery, monkeypatch):
    monkeypatch.setattr(settings, "RESULT_CACHE_ENABLED", False)
    env = recovery
    await env.bot._send_staged_media(env.context, env.request, env.info)
    assert len(env.sends) == 2
    assert not list(settings.TEMP_DIR.glob("telegram-restage-*"))
    assert env.store.get_cached_result(77, env.request.normalized_url) is None


@pytest.mark.asyncio
async def test_cancelled_recovery_removes_partial_download(recovery):
    import asyncio

    env = recovery

    async def cancelled_download(url, output_dir):
        (output_dir / "partial.mp4").write_bytes(b"partial")
        raise asyncio.CancelledError

    env.downloader.download_video.side_effect = cancelled_download
    with pytest.raises(asyncio.CancelledError):
        await env.bot._send_staged_media(env.context, env.request, env.info)
    assert not list(settings.TEMP_DIR.glob("telegram-restage-*"))
    assert env.store.get_cached_result(77, env.request.normalized_url) is None
