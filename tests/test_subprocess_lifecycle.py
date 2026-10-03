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
        assert [entry for entry in signals if entry[1]] == [(12345, signal.SIGKILL)]
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


@pytest.mark.asyncio
async def test_cleanup_waits_for_live_descendants_after_leader_reaped(monkeypatch, tmp_path, caplog):
    """A reaped leader must not release capacity while its group is still active."""
    proc = tmp_path / 'proc'
    member = proc / '456'
    member.mkdir(parents=True)
    stat = member / 'stat'
    # comm can contain spaces and parentheses; pgrp follows state and ppid.
    stat.write_text('456 (worker (child)) D 1 12345 12345 0')

    class ReapedLeader:
        pid = 12345

        def poll(self):
            return -signal.SIGKILL

    monkeypatch.setattr(subprocess_lifecycle.os, 'killpg', lambda pid, sig: None)
    monkeypatch.setattr(subprocess_lifecycle, '_PROC_ROOT', proc, raising=False)
    monkeypatch.setattr(subprocess_lifecycle, "_REAP_WARNING_SECONDS", 0.01, raising=False)
    task = asyncio.create_task(subprocess_lifecycle.terminate_process_group(ReapedLeader()))
    try:
        await asyncio.sleep(0.08)
        assert not task.done()
        assert "retaining worker capacity" in caplog.text
        # A zombie no longer executes; its reaping belongs to its new parent.
        stat.write_text('456 (worker (child)) Z 1 12345 12345 0')
        await asyncio.wait_for(task, 1)
    finally:
        stat.unlink(missing_ok=True)
        await asyncio.wait_for(task, 1)


def test_group_member_disappearing_during_scan_is_stopped(monkeypatch, tmp_path):
    (tmp_path / '456').mkdir()
    monkeypatch.setattr(subprocess_lifecycle, '_PROC_ROOT', tmp_path)
    monkeypatch.setattr(subprocess_lifecycle.os, 'killpg', lambda pid, sig: None)
    assert not subprocess_lifecycle._group_has_live_members(12345)


def test_unreadable_group_status_retains_capacity(monkeypatch, tmp_path):
    from pathlib import Path

    (tmp_path / '456').mkdir()
    monkeypatch.setattr(subprocess_lifecycle, '_PROC_ROOT', tmp_path)
    monkeypatch.setattr(subprocess_lifecycle.os, 'killpg', lambda pid, sig: None)

    def unreadable(path):
        raise PermissionError('procfs unavailable')

    monkeypatch.setattr(Path, 'read_text', unreadable)
    assert subprocess_lifecycle._group_has_live_members(12345)
