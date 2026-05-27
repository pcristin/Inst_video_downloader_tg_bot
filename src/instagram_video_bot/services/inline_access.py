"""Helpers for paid true-inline delivery."""

from __future__ import annotations

from dataclasses import dataclass
import secrets

SUBSCRIPTION_PREFIX = "inline_sub:v1"
ONE_TIME_PREFIX = "inline_once:v1"
INLINE_RESULT_PREFIX = "inline"


@dataclass(frozen=True)
class InlinePaymentPayload:
    kind: str
    user_id: int
    session_token: str


def generate_session_token() -> str:
    return secrets.token_urlsafe(16)


def build_inline_result_id(session_token: str) -> str:
    return f"{INLINE_RESULT_PREFIX}:{session_token}"


def parse_inline_result_id(result_id: str) -> str | None:
    prefix = f"{INLINE_RESULT_PREFIX}:"
    if not result_id.startswith(prefix):
        return None
    token = result_id[len(prefix):].strip()
    return token or None


def build_subscription_payload(user_id: int, session_token: str) -> str:
    return f"{SUBSCRIPTION_PREFIX}:{user_id}:{session_token}"


def build_one_time_payload(user_id: int, session_token: str) -> str:
    return f"{ONE_TIME_PREFIX}:{user_id}:{session_token}"


def parse_inline_payment_payload(payload: str) -> InlinePaymentPayload | None:
    parts = payload.split(":")
    if len(parts) != 4:
        return None
    prefix = ":".join(parts[:2])
    if prefix == SUBSCRIPTION_PREFIX:
        kind = "subscription"
    elif prefix == ONE_TIME_PREFIX:
        kind = "one_time"
    else:
        return None
    try:
        user_id = int(parts[2])
    except ValueError:
        return None
    session_token = parts[3].strip()
    if not session_token:
        return None
    return InlinePaymentPayload(kind=kind, user_id=user_id, session_token=session_token)


def validate_star_amount(raw_value: str) -> int | None:
    try:
        amount = int(raw_value)
    except ValueError:
        return None
    if amount < 1 or amount > 10000:
        return None
    return amount
