import asyncio
from pathlib import Path

import pytest

from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    VideoInfo,
)


@pytest.mark.asyncio
async def test_first_success_ignores_failure_and_returns_before_loser_cleanup():
    from src.instagram_video_bot.services.instagram_delivery_race import (
        first_success,
        drain_race_cleanup,
    )

    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def bad():
        raise DownloadError("unavailable")

    async def good():
        await asyncio.sleep(0.01)
        return "winner"

    async def slow():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release.wait()

    name, value = await first_success({"bad": bad(), "good": good(), "slow": slow()})
    assert (name, value) == ("good", "winner")
    await cancelled.wait()
    release.set()
    await drain_race_cleanup()


@pytest.mark.asyncio
async def test_all_failed_candidates_raise_and_drain():
    from src.instagram_video_bot.services.instagram_delivery_race import (
        first_success,
        drain_race_cleanup,
    )

    async def bad():
        raise DownloadError("failed")

    with pytest.raises(DownloadError):
        await first_success({"a": bad(), "b": bad()})
    await drain_race_cleanup()


@pytest.mark.asyncio
async def test_cancelled_coordinator_drains_children():
    from src.instagram_video_bot.services.instagram_delivery_race import (
        first_success,
        drain_race_cleanup,
    )

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(first_success({"a": slow()}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await drain_race_cleanup()
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_remote_staging_uses_shared_upload_gate_and_removes_url(
    tmp_path, monkeypatch
):
    from src.instagram_video_bot.services.telegram_media_stager import (
        TelegramMediaStager,
        _upload_state,
    )
    from types import SimpleNamespace

    calls = []

    class Bot:
        async def send_video(self, **kw):
            calls.append(kw["video"])
            return SimpleNamespace(video=SimpleNamespace(file_id="durable"))

    bot = Bot()
    stager = TelegramMediaStager(1)
    item = MediaItem(
        tmp_path / "absent.mp4", "video", remote_url="https://cdn.test/video"
    )
    state = _upload_state(bot)
    for _ in range(state.semaphore._value):
        await state.semaphore.acquire()
    task = asyncio.create_task(stager.stage_media(bot, [item]))
    await asyncio.sleep(0)
    assert not calls
    state.semaphore.release()
    result = await task
    assert calls == ["https://cdn.test/video"]
    assert result[0].telegram_file_id == "durable"
    assert result[0].remote_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,expected_public",
    [("direct_candidate_ineligible", 0), ("metadata_failed", 1)],
)
async def test_public_fallback_only_for_metadata_failure(
    tmp_path, monkeypatch, reason, expected_public
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from src.instagram_video_bot.services import instagram_delivery_race as race
    from src.instagram_video_bot.services.download_models import (
        ProviderExecutionMetrics,
    )

    calls = []

    def info():
        return VideoInfo(
            tmp_path / "v", "title", media_items=[MediaItem(tmp_path / "v", "video")]
        )

    @asynccontextmanager
    async def slot():
        yield

    class Direct:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None
        _instagram_provider_slot = staticmethod(slot)

        async def _download_with_account_leases(self, *args, **kwargs):
            raise DownloadError(reason)

        async def _run_instagram_operation(self, *args, **kwargs):
            calls.append("public")
            return info()

        async def _normalize_instagram_result(self, result):
            return result

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def download_video(self, *args):
            await asyncio.sleep(0.02)
            return info()

    class Stager:
        async def stage_media(self, bot, items):
            for item in items:
                item.telegram_file_id = "staged"
            return items

    monkeypatch.setattr(race, "race_available", lambda *a: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (SimpleNamespace(release_account=lambda a: None), (object(), object())),
    )
    workers = iter([Direct(), Local()])
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))
    result = await race.prepare_instagram_delivery(
        Local(), "url", tmp_path, object(), Stager()
    )
    assert result.media_items[0].telegram_file_id == "staged"
    await race.drain_race_cleanup()
    assert len(calls) == expected_public


@pytest.mark.asyncio
async def test_race_capacity_owned_until_loser_stops(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import instagram_delivery_race as race
    from src.instagram_video_bot.services.download_models import (
        ProviderExecutionMetrics,
    )
    from src.instagram_video_bot.config.settings import settings

    release = asyncio.Event()
    cancelled = asyncio.Event()
    calls = []
    info = VideoInfo(
        tmp_path / "v", "title", media_items=[MediaItem(tmp_path / "v", "video")]
    )

    class Direct:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def _download_with_account_leases(self, *args, **kwargs):
            await asyncio.sleep(0.01)
            return info

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def download_video(self, *args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
                await release.wait()

    class Fallback:
        async def download_video(self, *args):
            calls.append("fallback")
            return info

    class Stager:
        async def stage_media(self, bot, items):
            for item in items:
                item.telegram_file_id = "staged"
            return items

    monkeypatch.setattr(settings, "INSTAGRAM_DELIVERY_RACE_MAX_ACTIVE", 1)
    monkeypatch.setattr(race, "race_available", lambda *a: True)
    released = []
    accounts = (object(), object())
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (SimpleNamespace(release_account=released.append), accounts),
    )
    workers = iter([Direct(), Local()])
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))
    await race.prepare_instagram_delivery(Local(), "url", tmp_path, object(), Stager())
    await cancelled.wait()
    await race.prepare_instagram_delivery(
        Fallback(), "url2", tmp_path, object(), Stager()
    )
    assert calls == ["fallback"]
    assert asyncio.get_running_loop()._instagram_race_admission.locked()
    assert released == []
    release.set()
    await race.drain_race_cleanup()
    assert not asyncio.get_running_loop()._instagram_race_admission.locked()
    assert released == list(accounts)


def test_single_saved_account_is_left_for_sequential_path(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import instagram_delivery_race as race

    session = tmp_path / "session"
    session.touch()
    account = SimpleNamespace(username="one", session_file=session)

    def unexpected(**kwargs):
        pytest.fail("Race must not consume the only account")

    manager = SimpleNamespace(
        get_available_accounts=lambda: [account],
        get_leasable_account_count=lambda **kw: 1,
        acquire_account=unexpected,
    )
    monkeypatch.setattr(race.providers, "get_account_manager", lambda: manager)
    assert race._reserve_accounts() is None


def test_failed_second_reservation_returns_first_account(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import instagram_delivery_race as race

    session = tmp_path / "session"
    session.touch()
    account = SimpleNamespace(username="one", session_file=session)
    acquired = iter([account, None])
    released = []
    manager = SimpleNamespace(
        get_available_accounts=lambda: [account],
        get_leasable_account_count=lambda **kw: 2,
        acquire_account=lambda **kw: next(acquired),
        release_account=released.append,
    )
    monkeypatch.setattr(race.providers, "get_account_manager", lambda: manager)
    assert race._reserve_accounts() is None
    assert released == [account]


@pytest.mark.asyncio
async def test_failed_race_preserves_provider_failure_metrics(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import instagram_delivery_race as race
    from src.instagram_video_bot.services.download_models import (
        ProviderExecutionMetrics,
    )

    class Direct:
        async def _download_with_account_leases(self, *args, **kwargs):
            raise DownloadError("direct_candidate_ineligible")

        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(
            provider="instagram", failure_class="download_failed"
        )
        last_account_health_event = object()

        async def download_video(self, *args):
            raise DownloadError("local failed")

    local = Local()
    outer = SimpleNamespace(
        last_provider_metrics=ProviderExecutionMetrics(provider="unknown"),
        last_account_health_event=None,
    )
    workers = iter([Direct(), local])
    monkeypatch.setattr(race, "race_available", lambda *a: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (SimpleNamespace(release_account=lambda a: None), (object(), object())),
    )
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))
    with pytest.raises(DownloadError):
        await race.prepare_instagram_delivery(
            outer, "url", tmp_path, object(), object()
        )
    assert outer.last_provider_metrics is local.last_provider_metrics
    assert outer.last_account_health_event is local.last_account_health_event
