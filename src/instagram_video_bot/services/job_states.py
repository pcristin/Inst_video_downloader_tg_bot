"""Typed job states and stable failure classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from telegram.error import NetworkError, RetryAfter, TimedOut

from .download_models import AuthenticationError


class JobState(str, Enum):
    """Storage-compatible lifecycle states for one provider job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureReason(str, Enum):
    """Stable reasons used by persistence and user-facing recovery actions."""

    UNSUPPORTED_URL = "unsupported_url"
    AUTHENTICATION_REQUIRED = "authentication_required"
    MEDIA_UNAVAILABLE = "media_unavailable"
    FILE_TOO_LARGE = "file_too_large"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TELEGRAM_DELIVERY = "telegram_delivery"
    DELIVERY_AMBIGUOUS = "delivery_ambiguous"
    UNKNOWN = "unknown"


class FailureStage(str, Enum):
    """High-level boundary at which a request failed."""

    ACQUISITION = "acquisition"
    DELIVERY = "delivery"


@dataclass(frozen=True)
class FailureDetails:
    """A stable failure reason and whether an explicit user retry is safe."""

    reason: FailureReason
    retryable: bool


def classify_failure(
    error: Exception,
    *,
    stage: FailureStage,
    ambiguous_delivery: bool = False,
) -> FailureDetails:
    """Classify provider and Telegram errors without leaking implementation text."""

    if stage is FailureStage.DELIVERY and ambiguous_delivery:
        return FailureDetails(FailureReason.DELIVERY_AMBIGUOUS, retryable=False)

    text = str(error).lower()
    class_name = error.__class__.__name__.lower()

    if isinstance(error, AuthenticationError) or _contains_any(
        text,
        "authentication failed",
        "cookies have expired",
        "login required",
        "authorization failed",
        "auth challenge",
    ):
        return FailureDetails(FailureReason.AUTHENTICATION_REQUIRED, retryable=False)

    if _contains_any(text, "unsupported", "not supported"):
        return FailureDetails(FailureReason.UNSUPPORTED_URL, retryable=False)

    if _contains_any(text, "file is too large", "file too large", "entity too large"):
        return FailureDetails(FailureReason.FILE_TOO_LARGE, retryable=False)

    if isinstance(error, RetryAfter) or _contains_any(
        text, "rate limit", "rate-limit", "too many requests"
    ):
        return FailureDetails(FailureReason.PROVIDER_RATE_LIMITED, retryable=True)

    if (
        isinstance(error, (TimeoutError, TimedOut))
        or "timeout" in class_name
        or _contains_any(text, "timed out", "timeout")
    ):
        reason = (
            FailureReason.TELEGRAM_DELIVERY
            if stage is FailureStage.DELIVERY
            else FailureReason.PROVIDER_TIMEOUT
        )
        return FailureDetails(reason, retryable=True)

    if _contains_any(
        text,
        "private account",
        "private media",
        "media unavailable",
        "not found",
        "has been removed",
        "deleted",
    ):
        return FailureDetails(FailureReason.MEDIA_UNAVAILABLE, retryable=False)

    if stage is FailureStage.DELIVERY:
        return FailureDetails(
            FailureReason.TELEGRAM_DELIVERY,
            retryable=True,
        )

    if isinstance(error, NetworkError) or _contains_any(
        text,
        "no instagram accounts",
        "no accounts available",
        "temporarily unavailable",
        "connection",
        "network",
    ):
        return FailureDetails(FailureReason.PROVIDER_UNAVAILABLE, retryable=True)

    return FailureDetails(FailureReason.UNKNOWN, retryable=True)


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)
