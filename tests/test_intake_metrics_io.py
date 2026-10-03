import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.instagram_video_bot.services.telegram.request_intake import (
    TelegramRequestIntake,
)
from src.instagram_video_bot.services.job_states import JobState


def make_intake(record):
    job = SimpleNamespace(job_id="j", state=JobState.QUEUED)
    submission = SimpleNamespace(
        request_id="r", job=job, queue_position=0, is_new_job=True
    )
    cancelled = []
    bot = SimpleNamespace(
        _request_user_id=lambda update: 1,
        _request_user_label=lambda update: "test",
        _build_job_executor=lambda *args: None,
        _build_submission_message=lambda *args, **kw: "queued",
        _await_request=AsyncMock(),
        _cleanup_request_task=lambda rid: None,
        request_contexts={},
        active_request_tasks={},
        job_manager=SimpleNamespace(
            submit=lambda **kw: submission, cancel_request=cancelled.append
        ),
        state_store=SimpleNamespace(
            record_request_received=record,
            record_request_outcome=lambda *a, **kw: record(),
        ),
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=1),
        effective_message=SimpleNamespace(message_id=2, reply_text=AsyncMock()),
    )
    link = SimpleNamespace(
        provider="instagram",
        provider_label="Instagram",
        original_url="https://instagram.com/p/test/",
        normalized_url="https://instagram.com/p/test/",
    )
    args = dict(
        group_settings={
            "duplicate_suppression": True,
            "chaos_mode_enabled": False,
            "quiet_mode": False,
        },
        language_code="en",
    )
    return TelegramRequestIntake(bot), bot, update, link, args, cancelled


@pytest.mark.asyncio
async def test_intake_metrics_write_runs_off_event_loop():
    main_thread = threading.get_ident()
    calls = []

    def record(*args, **kwargs):
        calls.append(threading.get_ident())
        assert threading.get_ident() != main_thread

    intake, bot, update, link, args, cancelled = make_intake(record)
    await intake.submit_parsed_link(update, object(), link, **args)
    await asyncio.gather(*bot.active_request_tasks.values())
    assert len(calls) == 1
    assert not cancelled


@pytest.mark.asyncio
async def test_cancel_during_receipt_write_drains_then_cancels_submitted_request():
    entered = threading.Event()
    release = threading.Event()
    events = []

    def record(*args, **kwargs):
        if kwargs.get("request_id"):
            entered.set()
            release.wait()
            events.append("received")
        else:
            events.append("cancelled")

    intake, bot, update, link, args, cancelled = make_intake(record)
    task = asyncio.create_task(
        intake.submit_parsed_link(update, object(), link, **args)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert events == ["received", "cancelled"]
    assert cancelled == ["r"]
    assert not bot.active_request_tasks
    update.effective_message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_during_running_status_does_not_leave_unowned_request():
    events = []

    def record(*args, **kwargs):
        events.append("received" if kwargs.get("request_id") else "cancelled")

    intake, bot, update, link, args, cancelled = make_intake(record)
    bot.job_manager.submit().job.state = JobState.RUNNING
    bot._on_job_state_change = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await intake.submit_parsed_link(update, object(), link, **args)
    assert cancelled == ["r"]
    assert events == ["received", "cancelled"]
    assert not bot.request_contexts
    assert not bot.active_request_tasks
