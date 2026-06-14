"""Helpers for Telegram true-inline session bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from ..config.settings import settings
from .inline_access import parse_inline_result_id


class InlineAccessStateStore(Protocol):
    def record_inline_promo_success(self, user_id: int) -> None:
        """Record a successful promotional inline delivery."""
        ...

    def record_inline_delivery_event(
        self,
        *,
        user_id: int,
        session_token: str,
        access_kind: str,
        status: str,
    ) -> None:
        """Record a subscription inline delivery outcome."""
        ...


def subscription_expires_at(
    payment: Any,
    *,
    now: datetime | None = None,
    period_seconds: int | None = None,
) -> datetime:
    expires_at = getattr(payment, "subscription_expiration_date", None)
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            return expires_at.replace(tzinfo=timezone.utc)
        return expires_at.astimezone(timezone.utc)
    if isinstance(expires_at, (int, float)):
        return datetime.fromtimestamp(expires_at, tz=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    effective_period_seconds = (
        settings.INLINE_SUBSCRIPTION_PERIOD_SECONDS
        if period_seconds is None
        else period_seconds
    )
    return current_time + timedelta(
        seconds=effective_period_seconds
    )


def parse_chosen_inline_session_token(
    result_id: str,
) -> tuple[str | None, str | None]:
    one_time_prefix = "inline_once:"
    if result_id.startswith(one_time_prefix):
        token = result_id.removeprefix(one_time_prefix).strip()
        return ("one_time", token) if token else (None, None)
    session_token = parse_inline_result_id(result_id)
    if session_token:
        return "free", session_token
    for prefix in ("sub:", "once:"):
        if result_id.startswith(prefix):
            token = result_id.removeprefix(prefix).strip()
            return ("paid", token) if token else (None, None)
    return None, None


def inline_session_is_expired(
    session: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    expires_at_raw = session.get("expires_at")
    if not expires_at_raw:
        return True
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= (now or datetime.now(timezone.utc))


def record_successful_inline_access(
    state_store: InlineAccessStateStore,
    session: dict[str, Any],
) -> None:
    access_kind = str(session.get("access_kind") or "free")
    user_id = int(session["user_id"])
    session_token = str(session["session_token"])
    if access_kind == "promo":
        state_store.record_inline_promo_success(user_id)
    if access_kind == "subscription":
        state_store.record_inline_delivery_event(
            user_id=user_id,
            session_token=session_token,
            access_kind=access_kind,
            status="success",
        )


def record_failed_inline_access(
    state_store: InlineAccessStateStore,
    session: dict[str, Any],
) -> None:
    access_kind = str(session.get("access_kind") or "free")
    if access_kind != "subscription":
        return
    state_store.record_inline_delivery_event(
        user_id=int(session["user_id"]),
        session_token=str(session["session_token"]),
        access_kind=access_kind,
        status="failed",
    )
