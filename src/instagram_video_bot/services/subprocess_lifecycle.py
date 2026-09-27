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
    """Kill the process group of a child started with start_new_session=True."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1
    while process.poll() is None and loop.time() < deadline:
        await asyncio.sleep(0.05)
