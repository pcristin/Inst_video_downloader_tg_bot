"""Wait for and terminate child process groups used by blocking providers."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess


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
    """Kill the entire session group and defer cancellation until leader reaping.

    Callers retain their provider/account capacity while cleanup runs, including
    when a timeout and subsequent race cancellation both cancel the caller.
    """
    try:
        # Descendants may survive their leader, so never gate this on poll().
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    async def reap() -> None:
        while process.poll() is None:
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
