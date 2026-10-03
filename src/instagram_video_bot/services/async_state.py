"""Move disk operations off the event loop without reordering cancelled writes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def call_state(operation: Callable[..., T], *args, **kwargs) -> T:
    """Finish an in-flight state operation before propagating cancellation.

    A SQLite transaction cannot be cancelled by cancelling its Python waiter.
    Draining it prevents terminal request updates overtaking earlier writes.
    """
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Retrieve failures while preserving cancellation.
        raise
