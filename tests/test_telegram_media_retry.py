from types import SimpleNamespace

import pytest
from telegram.error import NetworkError, RetryAfter, TimedOut

from src.instagram_video_bot.config.settings import Settings
from src.instagram_video_bot.services import telegram_media_retry
from src.instagram_video_bot.services.telegram_media_retry import (
    build_telegram_timeout_kwargs,
    call_telegram_with_retries,
    classify_telegram_delivery_error,
)


class _FlakyCall:
    def __init__(self):
        self.calls = 0
        self.kwargs_seen = []

    async def __call__(self, **kwargs):
        self.calls += 1
        self.kwargs_seen.append(kwargs)
        if self.calls == 1:
            raise NetworkError("httpx.ReadError: ")
        return SimpleNamespace(ok=True)


class _RetryAfterCall:
    def __init__(self):
        self.calls = 0

    async def __call__(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RetryAfter(3)
        return SimpleNamespace(ok=True)


def test_classify_telegram_delivery_error_marks_network_read_error_transient():
    error = NetworkError("httpx.ReadError: ")

    assert classify_telegram_delivery_error(error) == "telegram_network"


def test_classify_telegram_delivery_error_marks_timeout_transient():
    assert classify_telegram_delivery_error(TimedOut("timed out")) == "telegram_timeout"


def test_build_telegram_timeout_kwargs_uses_configured_values():
    kwargs = build_telegram_timeout_kwargs(
        read_timeout=120.0,
        write_timeout=180.0,
        connect_timeout=20.0,
        pool_timeout=30.0,
    )

    assert kwargs == {
        "read_timeout": 120.0,
        "write_timeout": 180.0,
        "connect_timeout": 20.0,
        "pool_timeout": 30.0,
    }


@pytest.mark.asyncio
async def test_call_telegram_with_retries_retries_transient_network_errors():
    flaky = _FlakyCall()

    result = await call_telegram_with_retries(
        flaky,
        attempts=2,
        backoff_seconds=0.0,
        timeout_kwargs={"write_timeout": 180.0},
    )

    assert result.ok is True
    assert flaky.calls == 2
    assert flaky.kwargs_seen == [
        {"write_timeout": 180.0},
        {"write_timeout": 180.0},
    ]


@pytest.mark.asyncio
async def test_call_telegram_with_retries_honors_retry_after_sleep(monkeypatch):
    retry_after = _RetryAfterCall()
    sleep_durations = []

    async def capture_sleep(duration):
        sleep_durations.append(duration)

    monkeypatch.setattr(telegram_media_retry.asyncio, "sleep", capture_sleep)

    result = await call_telegram_with_retries(
        retry_after,
        attempts=2,
        backoff_seconds=1.0,
        timeout_kwargs={},
    )

    assert result.ok is True
    assert retry_after.calls == 2
    assert sleep_durations == [3]


@pytest.mark.asyncio
async def test_call_telegram_with_retries_ignores_reserved_logging_context_keys():
    flaky = _FlakyCall()

    result = await call_telegram_with_retries(
        flaky,
        attempts=2,
        backoff_seconds=0.0,
        timeout_kwargs={},
        context={"message": "bad", "name": "bad", "chat_id": 123},
    )

    assert result.ok is True
    assert flaky.calls == 2


def test_telegram_media_retry_settings_have_safe_defaults():
    assert Settings.model_fields["TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS"].default == 2
    assert (
        Settings.model_fields["TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS"].default
        == 1.0
    )
    assert Settings.model_fields["TELEGRAM_MEDIA_READ_TIMEOUT_SECONDS"].default == 120.0
    assert (
        Settings.model_fields["TELEGRAM_MEDIA_CONNECT_TIMEOUT_SECONDS"].default
        == 20.0
    )
    assert Settings.model_fields["TELEGRAM_MEDIA_POOL_TIMEOUT_SECONDS"].default == 30.0
