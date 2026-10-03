"""Acquisition deadlines must not include normalization or Telegram staging."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services import instagram_delivery_race as race
from src.instagram_video_bot.services.download_models import (
    DownloadError,
    MediaItem,
    ProviderExecutionMetrics,
    VideoInfo,
)


@pytest.fixture
def preparation(monkeypatch, tmp_path):
    entered, release, public_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    behavior = SimpleNamespace(
        phase="stage", public=False, hang_public=False, local=False
    )

    def result():
        path = tmp_path / "clip.mp4"
        return VideoInfo(path, "Clip", media_items=[MediaItem(path, "video")])

    @asynccontextmanager
    async def slot():
        yield

    class Direct:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None
        _instagram_provider_slot = staticmethod(slot)

        async def _download_with_account_leases(self, *args, **kwargs):
            if behavior.local:
                raise DownloadError("direct_candidate_ineligible")
            if behavior.public:
                raise DownloadError("metadata unavailable")
            return result()

        async def _run_instagram_operation(self, *args, **kwargs):
            if behavior.hang_public:
                entered.set()
                try:
                    await release.wait()
                finally:
                    public_stopped.set()
            return result()

        async def _normalize_instagram_result(self, info):
            if behavior.phase == "normalize":
                entered.set()
                await release.wait()
            return info

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def download_video(self, *args):
            if behavior.local:
                # Model successful acquisition followed by local normalization,
                # which is included in download_video's result preparation.
                info = result()
                entered.set()
                await release.wait()
                return info
            raise DownloadError("local candidate unavailable")

    class Stager:
        async def stage_media(self, bot, items):
            if behavior.phase == "stage":
                entered.set()
                await release.wait()
            for item in items:
                item.telegram_file_id = "ready"
            return items

    monkeypatch.setattr(race.settings, "INSTAGRAM_ACQUISITION_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(race, "race_available", lambda *args: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (
            SimpleNamespace(release_account=lambda account: None),
            (object(), object()),
        ),
    )
    workers = iter([Direct(), Local()])
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))

    async def prepare():
        return await race.prepare_instagram_delivery(
            Local(), "url", tmp_path, object(), Stager()
        )

    return SimpleNamespace(
        prepare=prepare,
        behavior=behavior,
        entered=entered,
        release=release,
        public_stopped=public_stopped,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "public,phase", [(False, "stage"), (True, "normalize"), (True, "stage")]
)
async def test_preparation_can_outlast_acquisition_budget(preparation, public, phase):
    env = preparation
    env.behavior.public, env.behavior.phase = public, phase
    task = asyncio.create_task(env.prepare())
    try:
        await asyncio.wait_for(env.entered.wait(), 1)
        await asyncio.sleep(0.15)
        assert not task.done(), "acquisition budget cancelled later preparation"
        env.release.set()
        info = await asyncio.wait_for(task, 1)
        assert info.media_items[0].telegram_file_id == "ready"
    finally:
        env.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await race.drain_race_cleanup()


@pytest.mark.asyncio
async def test_direct_public_acquisition_remains_bounded(preparation):
    env = preparation
    env.behavior.public = env.behavior.hang_public = True
    task = asyncio.create_task(env.prepare())
    try:
        await asyncio.wait_for(env.entered.wait(), 1)
        with pytest.raises(DownloadError):
            await asyncio.wait_for(task, 1)
        assert env.public_stopped.is_set()
    finally:
        env.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await race.drain_race_cleanup()


@pytest.mark.asyncio
async def test_local_normalization_can_outlast_acquisition_budget(preparation):
    env = preparation
    env.behavior.local = True
    env.behavior.phase = "normalize"
    task = asyncio.create_task(env.prepare())
    try:
        await asyncio.wait_for(env.entered.wait(), 1)
        await asyncio.sleep(0.15)
        assert not task.done(), "acquisition budget cancelled local normalization"
        env.release.set()
        info = await asyncio.wait_for(task, 1)
        assert info.media_items[0].telegram_file_id == "ready"
    finally:
        env.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await race.drain_race_cleanup()
