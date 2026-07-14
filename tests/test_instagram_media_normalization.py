from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo
from src.instagram_video_bot.services.video_downloader import VideoDownloader


def _photo_result(tmp_path) -> VideoInfo:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo")
    return VideoInfo(
        file_path=path,
        title="photo",
        media_items=[MediaItem(file_path=path, media_type="photo")],
        primary_media_type="photo",
    )


@pytest.mark.asyncio
async def test_normalize_instagram_result_runs_in_worker_thread(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    info = _photo_result(tmp_path)
    calls = []

    def fake_normalize(value):
        calls.append(("normalize", value))
        return value

    async def fake_to_thread(function, value):
        calls.append(("to_thread", function, value))
        return function(value)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.normalize_instagram_media",
        fake_normalize,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.asyncio.to_thread",
        fake_to_thread,
    )

    result = await downloader._normalize_instagram_result(info)

    assert result is info
    assert calls[0] == ("to_thread", fake_normalize, info)
    assert calls[1] == ("normalize", info)


@pytest.mark.asyncio
@pytest.mark.parametrize("success_path", ["fast", "public", "leased", "single"])
async def test_every_instagram_success_path_is_normalized(
    monkeypatch, tmp_path, success_path
):
    downloader = VideoDownloader()
    downloader.fast_min_delay_between_downloads = 0
    downloader.fast_random_delay_range = (0, 0)
    info = _photo_result(tmp_path)
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    normalized = _photo_result(normalized_dir)
    normalize = AsyncMock(return_value=normalized)
    monkeypatch.setattr(downloader, "_normalize_instagram_result", normalize)

    class Adapter:
        @staticmethod
        def is_story_url(_url):
            return False

        @staticmethod
        def download_with_fast_method(_url, _output_dir):
            if success_path != "fast":
                raise AssertionError("fast path should not run")
            return info

        @staticmethod
        def download_with_public_ytdlp(_url, _output_dir):
            return info if success_path == "public" else None

    downloader.instagram_adapter = Adapter()
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.settings.IG_FAST_METHOD_ENABLED",
        success_path == "fast",
    )
    manager = SimpleNamespace() if success_path == "leased" else None
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.get_account_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        downloader,
        "_download_with_account_leases",
        AsyncMock(return_value=info),
    )
    monkeypatch.setattr(
        downloader,
        "_download_with_single_account",
        AsyncMock(return_value=info),
    )

    result = await downloader._download_instagram_media(
        "https://www.instagram.com/reel/test/", tmp_path
    )

    assert result is normalized
    normalize.assert_awaited_once_with(info)


@pytest.mark.asyncio
async def test_twitter_result_bypasses_instagram_normalization(monkeypatch, tmp_path):
    downloader = VideoDownloader()
    info = _photo_result(tmp_path)
    normalize = AsyncMock(side_effect=AssertionError("must not normalize Twitter"))
    monkeypatch.setattr(downloader, "_normalize_instagram_result", normalize)
    monkeypatch.setattr(
        downloader, "_download_twitter_media", AsyncMock(return_value=info)
    )

    result = await downloader.download_video("https://x.com/example/status/1", tmp_path)

    assert result is info
    normalize.assert_not_awaited()
