import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.video_downloader import VideoDownloader
from src.instagram_video_bot.services.download_models import DownloadError, VideoInfo
from src.instagram_video_bot.services import video_downloader as module


@pytest.fixture
def setup_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INSTAGRAM_AUTH_FIRST_ENABLED", True)
    monkeypatch.setattr(settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", True)
    monkeypatch.setattr(settings, "IG_FAST_METHOD_ENABLED", True)
    session = tmp_path / "session.json"
    session.write_text("{}")
    account = SimpleNamespace(
        username="one",
        password="unused",
        session_file=session,
        proxy=None,
        totp_secret=None,
    )

    class Manager:
        def __init__(self):
            self.releases = []
            self.failures = []
            self.acquires = 0
            self.busy = False

        def get_available_accounts(self):
            return [account]

        def acquire_account(self, excluded_usernames):
            self.acquires += 1
            return (
                None if self.busy or account.username in excluded_usernames else account
            )

        def release_account(self, value):
            self.releases.append(value)

        def record_account_success(self, value):
            pass

        def record_account_failure(self, value, reason):
            self.failures.append(reason)

    manager = Manager()
    monkeypatch.setattr(module, "get_account_manager", lambda: manager)
    downloader = VideoDownloader()

    async def no_throttle(*args, **kwargs):
        pass

    monkeypatch.setattr(downloader, "_apply_instagram_throttle", no_throttle)
    return downloader, manager, account


@pytest.mark.asyncio
async def test_auth_first_success_precedes_public_and_fast(
    setup_policy, monkeypatch, tmp_path
):
    downloader, manager, account = setup_policy
    actions = []

    async def operation(function, *, action, **kwargs):
        actions.append(action)
        return VideoInfo(tmp_path / "clip.mp4", "saved")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    result = await downloader._acquire_instagram_media(
        "https://instagram.com/reel/example/", tmp_path
    )
    assert result.title == "saved"
    assert actions == ["saved_session"]
    assert manager.releases == [account]
    assert downloader.last_provider_metrics.failure_class is None


@pytest.mark.asyncio
async def test_failed_priority_tries_public_paths_without_second_account_round(
    setup_policy, monkeypatch, tmp_path
):
    downloader, manager, account = setup_policy
    actions = []

    async def operation(function, *, action, **kwargs):
        actions.append(action)
        raise DownloadError("failed")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    with pytest.raises(DownloadError):
        await downloader._acquire_instagram_media(
            "https://instagram.com/reel/example/", tmp_path
        )
    assert actions == ["saved_session", "fast", "public"]
    assert manager.acquires == 1
    assert manager.releases == [account]


@pytest.mark.asyncio
async def test_priority_timeout_releases_and_removes_partial_then_falls_back(
    setup_policy, monkeypatch, tmp_path
):
    downloader, manager, account = setup_policy
    monkeypatch.setattr(settings, "INSTAGRAM_AUTH_FIRST_TIMEOUT_SECONDS", 0.05)
    actions = []

    async def operation(function, *, action, output_dir, **kwargs):
        actions.append(action)
        if action == "saved_session":
            (output_dir / "partial.mp4").write_bytes(b"partial")
            await asyncio.sleep(10)
        return VideoInfo(tmp_path / "clip.mp4", "fallback")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    result = await downloader._acquire_instagram_media(
        "https://instagram.com/reel/example/", tmp_path
    )
    assert result.title == "fallback"
    assert actions == ["saved_session", "fast"]
    assert manager.releases == [account]
    assert not manager.failures
    assert not list(tmp_path.glob("auth-first-*"))
    assert downloader.last_provider_metrics.failure_class is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["busy", "no_saved_session"])
async def test_priority_does_not_wait_or_login_when_unavailable(
    setup_policy, monkeypatch, tmp_path, unavailable
):
    downloader, manager, account = setup_policy
    if unavailable == "busy":
        manager.busy = True
    else:
        account.session_file.unlink()
    actions = []

    async def operation(function, *, action, **kwargs):
        actions.append(action)
        return VideoInfo(tmp_path / "clip.mp4", "fallback")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    await downloader._acquire_instagram_media(
        "https://instagram.com/reel/example/", tmp_path
    )
    assert actions == ["fast"]
    assert manager.acquires == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["disabled", "legacy_thread", "story"])
async def test_auth_first_opt_out_and_stories_keep_existing_route(
    setup_policy, monkeypatch, tmp_path, policy
):
    downloader, manager, account = setup_policy
    if policy == "disabled":
        monkeypatch.setattr(settings, "INSTAGRAM_AUTH_FIRST_ENABLED", False)
    if policy == "legacy_thread":
        monkeypatch.setattr(settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", False)
    actions = []

    async def operation(function, *, action, **kwargs):
        actions.append(action)
        return VideoInfo(tmp_path / "clip.mp4", "existing")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    url = (
        "https://instagram.com/stories/person/123/"
        if policy == "story"
        else "https://instagram.com/reel/example/"
    )
    await downloader._acquire_instagram_media(url, tmp_path)
    assert actions == (["leased"] if policy == "story" else ["fast"])


@pytest.mark.asyncio
async def test_auth_first_preserves_single_account_mode(
    setup_policy, monkeypatch, tmp_path
):
    downloader, manager, account = setup_policy
    monkeypatch.setattr(module, "get_account_manager", lambda: None)
    actions = []

    async def operation(function, *, action, **kwargs):
        actions.append(action)
        if action in {"fast", "public"}:
            raise DownloadError("public unavailable")
        return VideoInfo(tmp_path / "clip.mp4", "single")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    result = await downloader._acquire_instagram_media(
        "https://instagram.com/reel/example/", tmp_path
    )
    assert result.title == "single"
    assert actions == ["fast", "public", "single"]


@pytest.mark.parametrize("valid", [True, False])
def test_saved_session_client_never_performs_fresh_login(monkeypatch, valid):
    client = object.__new__(module._SavedSessionInstagramClient)
    monkeypatch.setattr(client, "_load_session_into_client", lambda: {"saved": True})
    monkeypatch.setattr(client, "_is_session_valid", lambda: valid)
    monkeypatch.setattr(
        module.InstagramClient,
        "_perform_login",
        lambda self: pytest.fail("fresh login forbidden"),
    )
    monkeypatch.setattr(
        module.InstagramClient,
        "_relogin",
        lambda self: pytest.fail("relogin forbidden"),
    )
    assert client.login() is valid
    assert client._relogin() is False
    assert client._perform_login() is False


def test_saved_session_worker_dispatches_without_regular_login(monkeypatch, tmp_path):
    from src.instagram_video_bot.services import instagram_isolated_worker as worker

    calls = []

    def download(self, account, url, output_dir, *, saved_session_only=False):
        calls.append(saved_session_only)

    monkeypatch.setattr(VideoDownloader, "_download_with_leased_account_sync", download)
    worker.execute_payload(
        {
            "action": "saved_session",
            "url": "https://instagram.com/reel/example/",
            "output_dir": str(tmp_path),
            "account": {},
        }
    )
    assert calls == [True]


@pytest.mark.asyncio
async def test_priority_cancellation_kills_worker_before_releasing_lease(
    setup_policy, monkeypatch, tmp_path
):
    import signal
    import subprocess
    import sys
    from src.instagram_video_bot.services import instagram_isolated_worker as worker

    downloader, manager, account = setup_policy
    processes = []
    started = asyncio.Event()
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(
        worker,
        "worker_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    task = asyncio.create_task(
        downloader._acquire_instagram_media(
            "https://instagram.com/reel/example/", tmp_path
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].poll() == -signal.SIGKILL
    assert manager.releases == [account]
    assert not manager.failures
    assert not list(tmp_path.glob("auth-first-*"))


@pytest.mark.asyncio
async def test_priority_gate_timeout_does_not_mark_account_failed(
    setup_policy, monkeypatch, tmp_path
):
    downloader, manager, account = setup_policy
    monkeypatch.setattr(settings, "INSTAGRAM_AUTH_FIRST_TIMEOUT_SECONDS", 0.05)
    semaphore = VideoDownloader._get_instagram_provider_semaphore()
    # Occupy all provider slots; the saved session should never reach the worker.
    for _ in range(settings.INSTAGRAM_MAX_CONCURRENT_JOBS):
        await semaphore.acquire()
    monkeypatch.setattr(
        downloader,
        "_run_instagram_operation",
        lambda *args, **kwargs: pytest.fail("worker must not start"),
    )
    try:
        assert (
            await downloader._try_saved_session_first(
                "https://instagram.com/reel/example/", tmp_path
            )
            is None
        )
    finally:
        for _ in range(settings.INSTAGRAM_MAX_CONCURRENT_JOBS):
            semaphore.release()
    assert manager.releases == [account]
    assert not manager.failures


@pytest.mark.asyncio
async def test_auth_failure_records_existing_quarantine_reason(
    setup_policy, monkeypatch, tmp_path
):
    downloader, manager, account = setup_policy
    from src.instagram_video_bot.services.instagram_client import InstagramAuthError

    async def operation(function, *, action, **kwargs):
        if action == "saved_session":
            raise InstagramAuthError("challenge_required")
        return VideoInfo(tmp_path / "clip.mp4", "public")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    await downloader._acquire_instagram_media(
        "https://instagram.com/reel/example/", tmp_path
    )
    assert manager.failures == ["auth_challenge"]
    assert manager.acquires == 1
    assert manager.releases == [account]
    assert downloader.last_provider_metrics.instagram_auth_failures == 1
    assert downloader.last_provider_metrics.failure_class is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected_reason",
    [
        ("client_connection", None),
        ("requests", None),
        ("unexpected", None),
        ("login_required", "auth_challenge"),
        ("rate_limited", "rate_limited"),
    ],
)
async def test_saved_validation_preserves_account_failure_policy_across_worker_boundary(
    setup_policy, monkeypatch, tmp_path, kind, expected_reason
):
    import requests
    from instagrapi.exceptions import (
        ClientConnectionError,
        LoginRequired,
        PleaseWaitFewMinutes,
    )
    from src.instagram_video_bot.services import instagram_isolated_worker as worker

    downloader, manager, account = setup_policy
    errors = {
        "client_connection": ClientConnectionError("proxy unavailable"),
        "requests": requests.RequestException("connection interrupted"),
        "unexpected": RuntimeError("unexpected validation transport failure"),
        "login_required": LoginRequired("login_required"),
        "rate_limited": PleaseWaitFewMinutes("Please wait a few minutes"),
    }

    def validate():
        raise errors[kind]

    def initialize(self, **kwargs):
        self.client = SimpleNamespace(get_timeline_feed=validate)

    monkeypatch.setattr(module._SavedSessionInstagramClient, "__init__", initialize)
    monkeypatch.setattr(
        module._SavedSessionInstagramClient,
        "_load_session_into_client",
        lambda self: {"session": True},
    )

    async def operation(function, *, action, **kwargs):
        if action == "saved_session":
            try:
                downloader._build_leased_client(account, saved_session_only=True)
            except Exception as error:
                raise worker.decode_error(
                    {"error_type": type(error).__name__, "message": str(error)}
                )
        return VideoInfo(tmp_path / "clip.mp4", "public recovery")

    monkeypatch.setattr(downloader, "_run_instagram_operation", operation)
    result = await downloader._acquire_instagram_media(
        "https://instagram.com/reel/example/", tmp_path
    )
    assert result.title == "public recovery"
    assert manager.failures == ([] if expected_reason is None else [expected_reason])
    assert manager.releases == [account]
    assert downloader.last_provider_metrics.failure_class is None


def test_missing_or_corrupt_saved_session_is_not_remote_auth_failure(monkeypatch):
    client = object.__new__(module._SavedSessionInstagramClient)
    monkeypatch.setattr(client, "_load_session_into_client", lambda: None)
    monkeypatch.setattr(
        client,
        "_is_session_valid",
        lambda: pytest.fail("no remote validation without local session"),
    )
    with pytest.raises(DownloadError, match="saved_session_unavailable"):
        client.login()
