"""Shared admission limit for actual provider work in the current event loop."""

from __future__ import annotations

import asyncio

from ..config.settings import settings

_GATE_ATTRIBUTE = "_instagram_video_bot_global_execution_semaphore"


def get_global_execution_semaphore() -> asyncio.Semaphore:
    """Return the process worker's gate, shared by jobs and Instagram providers.

    The loop owns the gate so closed loops and their waiters are collectible.
    Configuration is fixed for the loop lifetime; changing it requires restart.
    """
    loop = asyncio.get_running_loop()
    semaphore = getattr(loop, _GATE_ATTRIBUTE, None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, settings.GLOBAL_MAX_CONCURRENT_JOBS))
        setattr(loop, _GATE_ATTRIBUTE, semaphore)
    return semaphore
