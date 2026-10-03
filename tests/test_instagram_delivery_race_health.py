"""Account-health events remain owned after a speculative candidate loses."""

import asyncio
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services import instagram_delivery_race as race
from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    ProviderExecutionMetrics,
    VideoInfo,
)


@pytest.mark.asyncio
async def test_late_loser_health_event_notifies_once_after_releasing_capacity(
    monkeypatch, tmp_path
):
    loser_started = asyncio.Event()
    loser_cancelled = asyncio.Event()
    release_loser = asyncio.Event()
    notification_started = asyncio.Event()
    release_notification = asyncio.Event()
    winner_event, loser_event = object(), object()
    notified = []
    released = []
    accounts = (object(), object())

    class Direct:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = winner_event

        async def _download_with_account_leases(self, *args, **kwargs):
            await loser_started.wait()
            return VideoInfo(
                tmp_path / "v",
                "winner",
                media_items=[
                    MediaItem(tmp_path / "v", "video", telegram_file_id="saved")
                ],
            )

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def download_video(self, *args):
            loser_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                loser_cancelled.set()
                await release_loser.wait()
                self.last_account_health_event = loser_event

    class Stager:
        async def stage_media(self, bot, items):
            return items

    async def notify(event):
        assert released == list(accounts)
        assert not asyncio.get_running_loop()._instagram_race_admission.locked()
        notified.append(event)
        notification_started.set()
        await release_notification.wait()

    monkeypatch.setattr(race.settings, "INSTAGRAM_DELIVERY_RACE_MAX_ACTIVE", 1)
    monkeypatch.setattr(race, "race_available", lambda *a: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (SimpleNamespace(release_account=released.append), accounts),
    )
    workers = iter((Direct(), Local()))
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))
    outer = SimpleNamespace(last_account_health_event=None)
    drain = None
    try:
        result = await asyncio.wait_for(
            race.prepare_instagram_delivery(
                outer, "url", tmp_path, object(), Stager(), on_account_health=notify
            ),
            1,
        )
        assert result.title == "winner"
        assert outer.last_account_health_event is None  # No outer duplicate alert.
        await asyncio.wait_for(loser_cancelled.wait(), 1)
        assert not notified
        release_loser.set()
        await asyncio.wait_for(notification_started.wait(), 1)
        drain = asyncio.create_task(race.drain_race_cleanup())
        await asyncio.sleep(0)
        assert not drain.done()  # Shutdown owns pending notification I/O too.
    finally:
        release_loser.set()
        release_notification.set()
        if drain is not None:
            await asyncio.wait_for(drain, 1)
        else:
            await asyncio.wait_for(race.drain_race_cleanup(), 1)
    assert notified == [winner_event, loser_event]
    await race.drain_race_cleanup()
    assert notified == [winner_event, loser_event]


@pytest.mark.asyncio
async def test_failed_race_notifies_both_events_without_outer_duplicate(
    monkeypatch, tmp_path
):
    events = [object(), object()]
    notified = []

    class Direct:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = events[0]

        async def _download_with_account_leases(self, *args, **kwargs):
            raise DownloadError("direct_candidate_ineligible")

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = events[1]

        async def download_video(self, *args):
            raise DownloadError("failed")

    async def notify(event):
        notified.append(event)
        if event is events[0]:
            raise RuntimeError("notification transport failed")

    workers = iter((Direct(), Local()))
    monkeypatch.setattr(race, "race_available", lambda *a: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (SimpleNamespace(release_account=lambda a: None), (object(), object())),
    )
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))
    outer = SimpleNamespace(last_account_health_event=None)
    with pytest.raises(DownloadError):
        await race.prepare_instagram_delivery(
            outer, "url", tmp_path, object(), object(), on_account_health=notify
        )
    assert notified == events
    assert outer.last_account_health_event is None


@pytest.mark.asyncio
async def test_sequential_fallback_retains_outer_account_notification(
    monkeypatch, tmp_path
):
    event = object()

    class Sequential:
        last_account_health_event = None

        async def download_video(self, *args):
            self.last_account_health_event = event
            return "result"

    async def unexpected(event):
        pytest.fail("Sequential path must preserve the existing outer notifier")

    monkeypatch.setattr(race, "race_available", lambda *a: False)
    outer = Sequential()
    assert (
        await race.prepare_instagram_delivery(
            outer, "url", tmp_path, object(), None, on_account_health=unexpected
        )
        == "result"
    )
    assert outer.last_account_health_event is event
