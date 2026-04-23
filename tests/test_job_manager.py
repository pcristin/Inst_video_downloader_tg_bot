import asyncio

import pytest

from src.instagram_video_bot.services.job_manager import JobManager, RequestRecord, SharedJob
from src.instagram_video_bot.services.state_store import StateStore


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
