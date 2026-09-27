import asyncio
import sys
import time
from pathlib import Path

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services import instagram_isolated_worker as worker
from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    VideoInfo,
)
from src.instagram_video_bot.services.video_downloader import (
    InstagramProviderTimeoutError,
    VideoDownloader,
)


def test_worker_result_round_trips_media_paths(tmp_path):
    item = MediaItem(file_path=tmp_path / "clip.mp4", media_type="video", duration=3.0)
    info = VideoInfo(file_path=item.file_path, title="Clip", media_items=[item])

    decoded = worker.decode_result(worker.encode_result(info))

    assert decoded == info
    assert isinstance(decoded.media_items[0].file_path, Path)


@pytest.mark.asyncio
async def test_timed_out_worker_is_terminated(monkeypatch, tmp_path):
    marker = tmp_path / "finished"
    script = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('done')"
    monkeypatch.setattr(
        worker, "worker_command", lambda: [sys.executable, "-c", script]
    )

    started = time.monotonic()
    with pytest.raises(InstagramProviderTimeoutError):
        await worker.run_isolated_instagram_operation(
            {"action": "fast"}, timeout_seconds=0.1
        )

    assert time.monotonic() - started < 1.5
    await asyncio.sleep(2.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_worker_process_reports_invalid_action(tmp_path):
    with pytest.raises(DownloadError, match="Unsupported Instagram worker action"):
        await worker.run_isolated_instagram_operation(
            {
                "action": "invalid",
                "url": "https://www.instagram.com/reel/a/",
                "output_dir": str(tmp_path),
            },
            timeout_seconds=10,
        )


@pytest.mark.asyncio
async def test_instagram_fast_path_uses_isolated_worker(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    info = VideoInfo(
        file_path=source,
        title="Clip",
        media_items=[MediaItem(file_path=source, media_type="video")],
    )
    calls = []

    async def fake_worker(payload, *, timeout_seconds):
        calls.append(payload)
        return info

    async def no_normalization(self, value):
        return value

    monkeypatch.setattr(
        settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", True, raising=False
    )
    monkeypatch.setattr(settings, "IG_FAST_METHOD_ENABLED", True)
    monkeypatch.setattr(worker, "run_isolated_instagram_operation", fake_worker)
    monkeypatch.setattr(
        VideoDownloader, "_normalize_instagram_result", no_normalization
    )
    downloader = VideoDownloader()
    downloader.instagram_adapter.download_with_fast_method = (
        lambda url, output_dir: info
    )
    downloader.fast_min_delay_between_downloads = 0
    downloader.fast_random_delay_range = (0, 0)

    result = await downloader.download_video(
        "https://www.instagram.com/reel/a/", tmp_path
    )

    assert result == info
    assert calls == [
        {
            "action": "fast",
            "url": "https://www.instagram.com/reel/a/",
            "output_dir": str(tmp_path),
        }
    ]


@pytest.mark.asyncio
async def test_killed_account_worker_releases_lease(monkeypatch, tmp_path):
    account = type(
        "Account",
        (),
        {
            "username": "alice",
            "password": "password",
            "session_file": None,
            "proxy": None,
            "totp_secret": None,
        },
    )()

    class Manager:
        def __init__(self):
            self.releases = []

        def get_available_accounts(self):
            return [account]

        def acquire_account(self, *, excluded_usernames):
            return account

        def release_account(self, value):
            self.releases.append(value)

    manager = Manager()

    async def timeout_worker(payload, *, timeout_seconds):
        raise InstagramProviderTimeoutError("timed out")

    monkeypatch.setattr(settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", True)
    monkeypatch.setattr(worker, "run_isolated_instagram_operation", timeout_worker)
    monkeypatch.setattr(
        "src.instagram_video_bot.services.video_downloader.get_account_manager",
        lambda: manager,
    )
    downloader = VideoDownloader()
    downloader.min_delay_between_downloads = 0
    downloader.random_delay_range = (0, 0)

    with pytest.raises(InstagramProviderTimeoutError):
        await downloader._download_with_account_leases(
            "https://www.instagram.com/reel/a/", tmp_path, None
        )

    assert manager.releases == [account]
