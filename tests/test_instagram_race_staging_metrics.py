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
@pytest.mark.parametrize("loser_status", ["failed", "cancelled"])
async def test_race_staging_records_winner_and_loser_after_resources_release(
    tmp_path, monkeypatch, loser_status
):
    clock = [10.0]
    monkeypatch.setattr(race, "perf_counter", lambda: clock[0])
    local_started = asyncio.Event()
    persistence_started = asyncio.Event()
    release_persistence = asyncio.Event()
    released = []
    records = []
    local_path = tmp_path / "local.mp4"
    local_path.write_bytes(b"1234567")
    remote = VideoInfo(
        tmp_path / "remote",
        "remote",
        media_items=[
            MediaItem(
                tmp_path / "remote", "video", remote_url="https://cdn.example/video"
            )
        ],
    )
    local = VideoInfo(local_path, "local", media_items=[MediaItem(local_path, "video")])

    class Direct:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def _download_with_account_leases(self, *args, **kwargs):
            return remote

    class Local:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def download_video(self, *args):
            return local

    class Stager:
        async def stage_media(self, bot, items):
            if items[0].remote_url:
                await local_started.wait()
                clock[0] += 1
                items[0].telegram_file_id = "winner"
                return items
            local_started.set()
            if loser_status == "failed":
                raise DownloadError("upload failed")
            await asyncio.Event().wait()

    async def persist(attempt):
        assert len(released) == 2
        assert not asyncio.get_running_loop()._instagram_race_admission.locked()
        persistence_started.set()
        await release_persistence.wait()
        records.append(attempt)

    monkeypatch.setattr(race.settings, "INSTAGRAM_DELIVERY_RACE_MAX_ACTIVE", 1)
    monkeypatch.setattr(race, "race_available", lambda *args: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (
            SimpleNamespace(release_account=released.append),
            (object(), object()),
        ),
    )
    workers = iter([Direct(), Local()])
    monkeypatch.setattr(race.providers, "VideoDownloader", lambda: next(workers))
    outer = SimpleNamespace()
    try:
        result = await asyncio.wait_for(
            race.prepare_instagram_delivery(
                outer,
                "url",
                tmp_path,
                object(),
                Stager(),
                on_staging_attempt=persist,
            ),
            1,
        )
        assert result is remote
        assert outer.last_race_staging_duration_ms == 1000
        await asyncio.wait_for(persistence_started.wait(), 1)
        assert records == []  # State I/O is blocked, but the winner already returned.
    finally:
        release_persistence.set()
        await race.drain_race_cleanup()
    by_stage = {row["stage"]: row for row in records}
    winner = by_stage["storage_upload"]
    assert winner["status"] == "delivered"
    assert winner["media_bytes"] is None
    assert winner["media_count"] == 1
    loser = by_stage["race_storage_upload_local"]
    assert loser["status"] == loser_status
    assert loser["media_bytes"] == 7
    assert loser["media_count"] == 1
    assert loser["error_class"] == (
        "DownloadError" if loser_status == "failed" else "CancelledError"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["race", "failed_race", "sequential"])
async def test_executor_separates_staging_from_provider_duration(
    tmp_path, monkeypatch, outcome
):
    from src.instagram_video_bot.services import telegram_bot as module

    info = VideoInfo(tmp_path / "media", "title")
    metrics = []
    attempts = []
    fake_downloader = SimpleNamespace(
        last_provider_metrics=ProviderExecutionMetrics(provider="instagram"),
        last_account_health_event=None,
    )

    async def download(*args):
        return info

    fake_downloader.download_video = download

    async def prepare(downloader, *args, on_staging_attempt, **kwargs):
        downloader.last_race_staging_duration_ms = 300
        await on_staging_attempt(
            dict(
                candidate="direct",
                stage="storage_upload",
                status="delivered",
                duration_ms=300,
                media_bytes=None,
                media_count=1,
                error_class=None,
            )
        )
        if outcome == "failed_race":
            downloader.last_race_download_duration_ms = 200
            raise DownloadError("failed")
        return info

    async def notify(*args):
        pass

    monkeypatch.setattr(module.settings, "RESULT_CACHE_ENABLED", False)
    monkeypatch.setattr(module.settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(module, "VideoDownloader", lambda: fake_downloader)
    monkeypatch.setattr(race, "prepare_instagram_delivery", prepare)
    bot = object.__new__(module.TelegramBot)
    bot.state_store = SimpleNamespace(
        record_delivery_attempt=lambda **kw: attempts.append(kw)
    )
    bot._record_provider_metrics = lambda *args, **kwargs: metrics.append(kwargs)
    bot._notify_owner_about_low_account_pool = notify
    bot._elapsed_ms = lambda start: 1000
    link = SimpleNamespace(
        provider="twitter" if outcome == "sequential" else "instagram",
        normalized_url="https://example/media",
        original_url="https://example/media",
    )
    execute = bot._build_job_executor(1, link, SimpleNamespace(bot=object()))
    job = SimpleNamespace(job_id="job", delivery_request_id=None)
    if outcome == "failed_race":
        with pytest.raises(DownloadError):
            await execute(job)
    else:
        assert await execute(job) is info
    assert (
        metrics[0]["download_duration_ms"]
        == {
            "race": 700,
            "failed_race": 200,
            "sequential": 1000,
        }[outcome]
    )
    if outcome != "sequential":
        assert attempts[0]["request_id"] == "job"
        assert attempts[0]["stage"] == "storage_upload"
        assert "candidate" not in attempts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_unsuccessful_race_records_each_started_upload(
    tmp_path, monkeypatch, cancel
):
    clock = [0.0]
    monkeypatch.setattr(race, "perf_counter", lambda: clock[0])
    both_staging = asyncio.Event()
    started = []
    records = []
    released = []
    media = tmp_path / "media"
    media.write_bytes(b"123")

    class Candidate:
        last_provider_metrics = ProviderExecutionMetrics(provider="instagram")
        last_account_health_event = None

        async def download_video(self, *args, **kwargs):
            return VideoInfo(media, "title", media_items=[MediaItem(media, "video")])

        _download_with_account_leases = download_video

    class Stager:
        async def stage_media(self, bot, items):
            started.append(True)
            if len(started) == 2:
                both_staging.set()
            if cancel:
                await asyncio.Event().wait()
            clock[0] += 1
            raise DownloadError("upload failed")

    async def persist(attempt):
        assert len(released) == 2
        records.append(attempt)

    monkeypatch.setattr(race, "race_available", lambda *args: True)
    monkeypatch.setattr(
        race,
        "_reserve_accounts",
        lambda: (
            SimpleNamespace(release_account=released.append),
            (object(), object()),
        ),
    )
    monkeypatch.setattr(race.providers, "VideoDownloader", Candidate)
    outer = SimpleNamespace()
    task = asyncio.create_task(
        race.prepare_instagram_delivery(
            outer,
            "url",
            tmp_path,
            object(),
            Stager(),
            on_staging_attempt=persist,
        )
    )
    if cancel:
        await asyncio.wait_for(both_staging.wait(), 1)
        clock[0] += 1
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else DownloadError):
        await task
    await race.drain_race_cleanup()
    assert {r["stage"] for r in records} == {
        "race_storage_upload_direct",
        "race_storage_upload_local",
    }
    assert {r["status"] for r in records} == {"cancelled" if cancel else "failed"}
    assert outer.last_race_download_duration_ms == 0
    assert all(r["media_bytes"] == 3 and r["media_count"] == 1 for r in records)


def test_throughput_excludes_speculation_and_unknown_remote_bytes(tmp_path):
    from src.instagram_video_bot.services.state_store import StateStore

    store = StateStore(tmp_path / "metrics.db")
    store.start_job_metrics(
        job_id="job", chat_id=1, provider="instagram", normalized_url="url"
    )
    store.finalize_job_metrics("job", status="completed")
    for stage, duration, size in [
        ("storage_upload", 100, 100),
        ("storage_upload", 1000, None),
        ("race_storage_upload_local", 1, 100000),
    ]:
        store.record_delivery_attempt(
            job_id="job",
            request_id="request",
            stage=stage,
            status="delivered",
            duration_ms=duration,
            media_bytes=size,
            media_count=1,
        )
    summary = store.get_performance_summary(1)
    assert summary["storage_upload_bytes_per_second"] == 1000
    assert summary["avg_storage_upload_ms"] == 550
