import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telegram.error import NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)
T = TypeVar("T")
_RESERVED_LOG_RECORD_KEYS = set(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__,
) | {"message", "asctime"}


def build_telegram_timeout_kwargs(
    *,
    read_timeout: float | None,
    write_timeout: float | None,
    connect_timeout: float | None,
    pool_timeout: float | None,
) -> dict[str, float]:
    kwargs: dict[str, float] = {}
    if read_timeout is not None:
        kwargs["read_timeout"] = read_timeout
    if write_timeout is not None:
        kwargs["write_timeout"] = write_timeout
    if connect_timeout is not None:
        kwargs["connect_timeout"] = connect_timeout
    if pool_timeout is not None:
        kwargs["pool_timeout"] = pool_timeout
    return kwargs


def classify_telegram_delivery_error(error: Exception) -> str:
    if isinstance(error, TimedOut):
        return "telegram_timeout"
    if isinstance(error, RetryAfter):
        return "telegram_retry_after"
    if isinstance(error, NetworkError):
        text = str(error).lower()
        if "readerror" in text:
            return "telegram_network"
        if "writeerror" in text or "writetimeout" in text:
            return "telegram_network"
        return "telegram_network"
    return error.__class__.__name__


def is_retriable_telegram_delivery_error(error: Exception) -> bool:
    return isinstance(error, (NetworkError, TimedOut, RetryAfter))


def _safe_log_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    return {key: value for key, value in context.items() if key not in _RESERVED_LOG_RECORD_KEYS}


def _retry_sleep_seconds(error: Exception, *, backoff_seconds: float, attempt: int) -> float:
    generic_backoff = max(0.0, backoff_seconds) * (attempt + 1)
    if not isinstance(error, RetryAfter):
        return generic_backoff

    retry_after = error.retry_after
    if hasattr(retry_after, "total_seconds"):
        retry_after = retry_after.total_seconds()
    return max(generic_backoff, float(retry_after))


async def call_telegram_with_retries(
    operation: Callable[..., Awaitable[T]],
    *,
    attempts: int,
    backoff_seconds: float,
    timeout_kwargs: dict[str, float],
    context: dict[str, Any] | None = None,
) -> T:
    max_attempts = max(1, attempts)
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await operation(**timeout_kwargs)
        except Exception as error:
            last_error = error
            if not is_retriable_telegram_delivery_error(error) or attempt == max_attempts - 1:
                raise
            logger.warning(
                "Retrying Telegram media operation after transient error",
                extra={
                    "attempt": attempt + 1,
                    "attempts": max_attempts,
                    "failure_class": classify_telegram_delivery_error(error),
                    **_safe_log_context(context),
                },
            )
            await asyncio.sleep(
                _retry_sleep_seconds(error, backoff_seconds=backoff_seconds, attempt=attempt),
            )
    assert last_error is not None
    raise last_error
