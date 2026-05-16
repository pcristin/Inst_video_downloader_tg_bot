import asyncio

import pytest

from src.instagram_video_bot.services.job_manager import JobManager, RequestRecord, SharedJob
from src.instagram_video_bot.services.state_store import StateStore


async def _wait_for_started(started: list[tuple[str, int] | str], expected: tuple[str, int] | str) -> None:
    async def _wait() -> None:
        while expected not in started:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=1)


async def _wait_for_start_count(started: list[tuple[str, int] | str], count: int) -> None:
    async def _wait() -> None:
        while len(started) < count:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=1)


@pytest.mark.asyncio
async def test_promote_delivery_request_wakes_waiters_and_rotates_future(tmp_path):
    store = StateStore(tmp_path / "state.db")
    manager = JobManager(store)
    job = SharedJob(
        job_id="job-1",
        chat_id=77,
        submitter_user_id=1001,
        provider="instagram",
        provider_label="Instagram",
        original_url="https://www.instagram.com/reel/a/",
        normalized_url="https://www.instagram.com/reel/a/",
        delivery_request_id="req-1",
        requesters={
            "req-1": RequestRecord(request_id="req-1", chat_id=77, user_id=1001, user_label="alice"),
            "req-2": RequestRecord(request_id="req-2", chat_id=77, user_id=1002, user_label="bob"),
        },
    )
    old_future = asyncio.get_running_loop().create_future()
    job.delivery_future = old_future
    manager._jobs[job.job_id] = job
    store.create_job(job.job_id, job.chat_id, job.normalized_url, job.provider, "running")
    store.create_request("req-1", job.job_id, job.chat_id, 1001, "alice", job.provider, job.normalized_url, "running")
    store.create_request("req-2", job.job_id, job.chat_id, 1002, "bob", job.provider, job.normalized_url, "running")

    waiter = asyncio.create_task(manager.wait_for_delivery(job))
    await asyncio.sleep(0)

    manager.mark_request_failed("req-1", status="cancelled")

    assert await waiter is False
    assert job.delivery_request_id == "req-2"
    assert job.delivery_future is not old_future
    assert job.delivery_future is not None
    assert job.delivery_future.done() is False


@pytest.mark.asyncio
async def test_job_manager_passes_job_to_executor_and_records_metrics(tmp_path):
    store = StateStore(tmp_path / "state.db")
    manager = JobManager(store)
    seen_job_ids = []

    async def execute(job):
        seen_job_ids.append(job.job_id)
        return "ok"

    submission = manager.submit(
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="instagram",
        provider_label="Instagram",
        original_url="https://www.instagram.com/reel/a/",
        normalized_url="https://www.instagram.com/reel/a/",
        execute=execute,
        duplicate_suppression=True,
    )

    assert await submission.job.result_future == "ok"
    await submission.job.task

    summary = store.get_performance_summary(77, limit=50)
    assert seen_job_ids == [submission.job.job_id]
    assert summary["total_jobs"] == 1
    assert summary["providers"]["instagram"]["jobs"] == 1


@pytest.mark.asyncio
async def test_job_manager_supports_zero_arg_executor_for_compatibility(tmp_path):
    store = StateStore(tmp_path / "state.db")
    manager = JobManager(store)
    called = False

    async def execute():
        nonlocal called
        called = True
        return "ok"

    submission = manager.submit(
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="instagram",
        provider_label="Instagram",
        original_url="https://www.instagram.com/reel/a/",
        normalized_url="https://www.instagram.com/reel/a/",
        execute=execute,
        duplicate_suppression=False,
    )

    assert await submission.job.result_future == "ok"
    await submission.job.task
    assert called is True


@pytest.mark.asyncio
async def test_provider_semaphore_limits_same_provider_without_blocking_other_provider(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_group_settings(77, chat_max_concurrent_jobs=3)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.GLOBAL_MAX_CONCURRENT_JOBS", 2)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.TWITTER_MAX_CONCURRENT_JOBS", 1)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.YOUTUBE_SHORTS_MAX_CONCURRENT_JOBS", 2)
    manager = JobManager(store)
    started = []
    release = asyncio.Event()

    async def execute(job):
        started.append(job.provider)
        await release.wait()
        return job.provider

    first = manager.submit(
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/1",
        normalized_url="https://x.com/a/status/1",
        execute=execute,
        duplicate_suppression=False,
    )
    second = manager.submit(
        chat_id=77,
        user_id=1002,
        user_label="bob",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/2",
        normalized_url="https://x.com/a/status/2",
        execute=execute,
        duplicate_suppression=False,
    )
    third = manager.submit(
        chat_id=77,
        user_id=1003,
        user_label="cara",
        provider="youtube_shorts",
        provider_label="YouTube Shorts",
        original_url="https://youtube.com/shorts/abcdefg1234",
        normalized_url="https://youtube.com/shorts/abcdefg1234",
        execute=execute,
        duplicate_suppression=False,
    )

    await _wait_for_started(started, "twitter")
    await _wait_for_started(started, "youtube_shorts")

    assert started.count("twitter") == 1
    assert started.count("youtube_shorts") == 1

    release.set()
    await asyncio.gather(first.job.task, second.job.task, third.job.task)


@pytest.mark.asyncio
async def test_instagram_executor_wait_does_not_hold_global_capacity(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_group_settings(77, chat_max_concurrent_jobs=3)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.GLOBAL_MAX_CONCURRENT_JOBS", 1)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.INSTAGRAM_MAX_CONCURRENT_JOBS", 2)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.TWITTER_MAX_CONCURRENT_JOBS", 1)
    manager = JobManager(store)
    started = []
    instagram_waiting = asyncio.Event()
    release_instagram = asyncio.Event()

    async def execute(job):
        started.append(job.provider)
        if job.provider == "instagram":
            instagram_waiting.set()
            await release_instagram.wait()
        return job.provider

    instagram = manager.submit(
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="instagram",
        provider_label="Instagram",
        original_url="https://www.instagram.com/reel/a/",
        normalized_url="https://www.instagram.com/reel/a/",
        execute=execute,
        duplicate_suppression=False,
    )
    await asyncio.wait_for(instagram_waiting.wait(), timeout=1)

    twitter = manager.submit(
        chat_id=77,
        user_id=1002,
        user_label="bob",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/1",
        normalized_url="https://x.com/a/status/1",
        execute=execute,
        duplicate_suppression=False,
    )

    try:
        await _wait_for_started(started, "twitter")
    finally:
        release_instagram.set()
        await asyncio.gather(instagram.job.task, twitter.job.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_waiters_progress_after_provider_slot_releases(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_group_settings(77, chat_max_concurrent_jobs=3)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.GLOBAL_MAX_CONCURRENT_JOBS", 3)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.TWITTER_MAX_CONCURRENT_JOBS", 1)
    manager = JobManager(store)
    started = []
    release = asyncio.Event()

    async def execute(job):
        started.append(job.job_id)
        await release.wait()
        return job.job_id

    first = manager.submit(
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/1",
        normalized_url="https://x.com/a/status/1",
        execute=execute,
        duplicate_suppression=False,
    )
    second = manager.submit(
        chat_id=77,
        user_id=1002,
        user_label="bob",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/2",
        normalized_url="https://x.com/a/status/2",
        execute=execute,
        duplicate_suppression=False,
    )
    third = manager.submit(
        chat_id=77,
        user_id=1003,
        user_label="cara",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/3",
        normalized_url="https://x.com/a/status/3",
        execute=execute,
        duplicate_suppression=False,
    )

    await _wait_for_start_count(started, 1)
    assert started == [first.job.job_id]

    release.set()
    await _wait_for_start_count(started, 2)
    await asyncio.gather(first.job.task, second.job.task, third.job.task)


@pytest.mark.asyncio
async def test_chat_waiting_job_does_not_hold_provider_capacity(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_group_settings(77, chat_max_concurrent_jobs=1)
    store.update_group_settings(88, chat_max_concurrent_jobs=1)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.GLOBAL_MAX_CONCURRENT_JOBS", 3)
    monkeypatch.setattr("src.instagram_video_bot.services.job_manager.settings.TWITTER_MAX_CONCURRENT_JOBS", 2)
    manager = JobManager(store)
    started = []
    release = asyncio.Event()

    async def execute(job):
        started.append((job.provider, job.chat_id))
        await release.wait()
        return job.provider

    first = manager.submit(
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/1",
        normalized_url="https://x.com/a/status/1",
        execute=execute,
        duplicate_suppression=False,
    )
    second = manager.submit(
        chat_id=77,
        user_id=1002,
        user_label="bob",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/2",
        normalized_url="https://x.com/a/status/2",
        execute=execute,
        duplicate_suppression=False,
    )
    third = manager.submit(
        chat_id=88,
        user_id=1003,
        user_label="cara",
        provider="twitter",
        provider_label="Twitter/X",
        original_url="https://x.com/a/status/3",
        normalized_url="https://x.com/a/status/3",
        execute=execute,
        duplicate_suppression=False,
    )

    await _wait_for_started(started, ("twitter", 77))
    await _wait_for_started(started, ("twitter", 88))

    assert started.count(("twitter", 77)) == 1
    assert started.count(("twitter", 88)) == 1

    release.set()
    await asyncio.gather(first.job.task, second.job.task, third.job.task)
