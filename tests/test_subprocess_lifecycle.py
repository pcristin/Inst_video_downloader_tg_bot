"""Capacity and cancellation guarantees for process-group cleanup."""

import asyncio
import signal

import pytest

from src.instagram_video_bot.services import subprocess_lifecycle


@pytest.mark.asyncio
async def test_repeated_cancellation_holds_capacity_until_process_is_reaped(
    monkeypatch,
):
    started = asyncio.Event()
    release = asyncio.Event()
    entered_next = asyncio.Event()
    capacity = asyncio.Semaphore(1)
    signals = []

    class DelayedReap:
        pid = 12345

        def poll(self):
            started.set()
            return -signal.SIGKILL if release.is_set() else None

    monkeypatch.setattr(
        subprocess_lifecycle.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )

    async def operation():
        async with capacity:
            await subprocess_lifecycle.terminate_process_group(DelayedReap())

    async def next_operation():
        async with capacity:
            entered_next.set()

    task = asyncio.create_task(operation())
    next_task = None
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        next_task = asyncio.create_task(next_operation())
        task.cancel()
        await asyncio.sleep(0.08)
        assert not task.done()
        assert not entered_next.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(next_task, timeout=1)
        assert signals == [(12345, signal.SIGKILL)]
    finally:
        release.set()
        await asyncio.gather(
            *(t for t in (task, next_task) if t), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_cleanup_waits_beyond_previous_grace_period_for_actual_reap(monkeypatch):
    release = asyncio.Event()

    class DelayedReap:
        pid = 12345

        def poll(self):
            return -signal.SIGKILL if release.is_set() else None

    monkeypatch.setattr(subprocess_lifecycle.os, "killpg", lambda pid, sig: None)
    task = asyncio.create_task(
        subprocess_lifecycle.terminate_process_group(DelayedReap())
    )
    try:
        await asyncio.sleep(1.1)
        assert not task.done()
    finally:
        release.set()
        await task
