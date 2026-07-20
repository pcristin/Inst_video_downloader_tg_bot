"""Lifecycle owner for blocking Instagram provider execution."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SubmittedInstagramOperation(Generic[T]):
    executor: ThreadPoolExecutor
    future: Future[T]


class InstagramProviderRuntime:
    """Own one bounded executor generation at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._max_workers: int | None = None

    def submit(
        self,
        operation: Callable[[], T],
        *,
        max_workers: int,
    ) -> SubmittedInstagramOperation[T]:
        limit = max(1, int(max_workers))
        stale: ThreadPoolExecutor | None = None
        with self._lock:
            if self._executor is None or self._max_workers != limit:
                stale = self._executor
                self._executor = ThreadPoolExecutor(
                    max_workers=limit,
                    thread_name_prefix="instagram-provider",
                )
                self._max_workers = limit
            executor = self._executor
            future = executor.submit(operation)
        if stale is not None:
            stale.shutdown(wait=False, cancel_futures=True)
        return SubmittedInstagramOperation(executor=executor, future=future)

    def retire(self, stale_executor: ThreadPoolExecutor) -> bool:
        with self._lock:
            if self._executor is not stale_executor:
                return False
            self._executor = None
            self._max_workers = None
        stale_executor.shutdown(wait=False, cancel_futures=True)
        return True

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            self._max_workers = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
