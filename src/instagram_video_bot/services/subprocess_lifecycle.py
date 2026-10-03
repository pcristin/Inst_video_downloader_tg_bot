"""Wait for and terminate child process groups used by blocking providers."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import signal
import subprocess

logger = logging.getLogger(__name__)
_PROC_ROOT = Path("/proc")
_REAP_WARNING_SECONDS = 2.0


def _group_has_live_members(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    # Linux retains orphaned zombies until their new parent reaps them. They
    # cannot execute or write files, so they need not retain worker capacity.
    # Without procfs, conservatively wait until the group disappears.
    if not _PROC_ROOT.is_dir():
        return True
    for entry in _PROC_ROOT.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (
                (entry / "stat")
                .read_text(encoding="utf-8", errors="replace")
                .rsplit(")", 1)[1]
                .split()
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            # An unreadable status is not proof that workers stopped.
            return True
        if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
            return True
    return False


async def wait_for_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    timeout_error: Exception,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout_seconds)
    while process.poll() is None:
        if loop.time() >= deadline:
            raise timeout_error
        await asyncio.sleep(min(0.05, deadline - loop.time()))


async def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the entire session group and defer cancellation until all workers stop.

    Callers retain their provider/account capacity while cleanup runs, including
    when a timeout and subsequent race cancellation both cancel the caller.
    """
    try:
        # Descendants may survive their leader, so never gate this on poll().
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    async def reap() -> None:
        loop = asyncio.get_running_loop()
        warning_at = loop.time() + _REAP_WARNING_SECONDS
        warned = False
        while process.poll() is None or _group_has_live_members(process.pid):
            if not warned and loop.time() >= warning_at:
                logger.warning(
                    "Killed process group is still active; retaining worker capacity",
                    extra={"process_group": process.pid},
                )
                warned = True
            # A time limit here would release capacity while a killed worker
            # remains alive (for example in uninterruptible kernel I/O).
            await asyncio.sleep(0.05)

    cleanup = asyncio.create_task(reap())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError
