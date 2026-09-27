import asyncio
import io
import json
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services import instagram_isolated_worker as worker
from src.instagram_video_bot.services import subprocess_lifecycle
from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    VideoInfo,
)
from src.instagram_video_bot.services.instagram_fast_extractor import (
    InstagramFastExtractorError,
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


def test_fast_extractor_failure_preserves_telemetry(monkeypatch, tmp_path):
    result_path = tmp_path / "worker-result.json"
    failure = InstagramFastExtractorError("fast_path_budget_exhausted")
    failure.endpoint_timings = [{"endpoint": "public", "status": "miss"}]
    failure.budget_exhausted = True

    def fail_fast(_payload):
        raise failure

    monkeypatch.setattr(worker, "execute_payload", fail_fast)
    monkeypatch.setattr(
        worker.sys,
        "stdin",
        SimpleNamespace(
            buffer=io.BytesIO(json.dumps({"_result_file": str(result_path)}).encode())
        ),
    )

    worker.main()

    response = json.loads(result_path.read_text(encoding="utf-8"))
    restored = worker.decode_error(response)
    assert isinstance(restored, InstagramFastExtractorError)
    assert restored.endpoint_timings == failure.endpoint_timings
    assert restored.budget_exhausted is True


def test_worker_reports_malformed_payload_when_result_path_is_available(
    monkeypatch, tmp_path
):
    result_path = tmp_path / "worker-result.json"
    monkeypatch.setenv("IG_WORKER_RESULT_PATH", str(result_path))
    monkeypatch.setattr(worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{")))

    worker.main()

    response = json.loads(result_path.read_text(encoding="utf-8"))
    assert response["ok"] is False
    assert response["error_type"] == "JSONDecodeError"


@pytest.mark.asyncio
async def test_process_group_cleanup_runs_after_leader_exits(monkeypatch):
    calls = []

    class ExitedLeader:
        pid = 12345

        def poll(self):
            return 0

    monkeypatch.setattr(
        subprocess_lifecycle.os, "killpg", lambda pid, sig: calls.append((pid, sig))
    )

    await subprocess_lifecycle.terminate_process_group(ExitedLeader())

    assert calls == [(12345, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_process_group_reaping_does_not_block_event_loop(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowWait:
        pid = 12345

        def poll(self):
            started.set()
            return -signal.SIGKILL if release.is_set() else None

    monkeypatch.setattr(subprocess_lifecycle.os, "killpg", lambda _pid, _sig: None)
    task = asyncio.create_task(subprocess_lifecycle.terminate_process_group(SlowWait()))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not task.done()
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_timed_out_worker_is_terminated(monkeypatch):
    script = "import time; time.sleep(60)"
    processes = []
    real_popen = subprocess.Popen

    def track_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        worker, "worker_command", lambda: [sys.executable, "-c", script]
    )
    monkeypatch.setattr(worker.subprocess, "Popen", track_process)

    with pytest.raises(InstagramProviderTimeoutError):
        await worker.run_isolated_instagram_operation(
            {"action": "fast"}, timeout_seconds=0.1
        )

    assert len(processes) == 1
    assert processes[0].poll() == -signal.SIGKILL


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
            self.failures = []

        def get_available_accounts(self):
            return [account]

        def acquire_account(self, *, excluded_usernames):
            return account

        def release_account(self, value):
            self.releases.append(value)

        def record_account_failure(self, value, reason):
            self.failures.append((value, reason))
            return None

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
    assert manager.failures == [(account, "provider_timeout")]
