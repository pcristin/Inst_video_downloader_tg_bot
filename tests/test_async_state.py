import asyncio
import threading

import pytest

from src.instagram_video_bot.services.async_state import call_state


@pytest.mark.asyncio
async def test_state_call_keeps_loop_responsive_and_drains_cancelled_write():
    started = threading.Event()
    release = threading.Event()
    committed = []

    def write():
        started.set()
        release.wait(timeout=2)
        committed.append(True)

    task = asyncio.create_task(call_state(write))
    try:
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not committed
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert committed == [True]
