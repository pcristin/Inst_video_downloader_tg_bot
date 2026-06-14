from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.instagram_video_bot.services.telegram_inline_sessions import (
    inline_session_is_expired, parse_chosen_inline_session_token,
    record_failed_inline_access, record_successful_inline_access,
    subscription_expires_at)


class _FakeStateStore:
    def __init__(self):
        self.promo_successes = []
        self.delivery_events = []

    def record_inline_promo_success(self, user_id: int) -> None:
        self.promo_successes.append(user_id)

    def record_inline_delivery_event(
        self,
        *,
        user_id: int,
        session_token: str,
        access_kind: str,
        status: str,
    ) -> None:
        self.delivery_events.append(
            {
                "user_id": user_id,
                "session_token": session_token,
                "access_kind": access_kind,
                "status": status,
            }
        )


def test_subscription_expires_at_normalizes_supported_payment_values():
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=3)))

    assert subscription_expires_at(
        SimpleNamespace(subscription_expiration_date=naive)
    ) == naive.replace(tzinfo=timezone.utc)
    assert subscription_expires_at(
        SimpleNamespace(subscription_expiration_date=aware)
    ) == aware.astimezone(timezone.utc)
    assert subscription_expires_at(
        SimpleNamespace(subscription_expiration_date=1767225600)
    ) == datetime.fromtimestamp(1767225600, tz=timezone.utc)


def test_subscription_expires_at_falls_back_to_configured_period():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert subscription_expires_at(
        SimpleNamespace(subscription_expiration_date=None),
        now=now,
        period_seconds=90,
    ) == now + timedelta(seconds=90)


def test_parse_chosen_inline_session_token_supports_free_and_paid_result_ids():
    assert parse_chosen_inline_session_token("inline:free-token") == (
        "free",
        "free-token",
    )
    assert parse_chosen_inline_session_token("inline_once:one-time-token") == (
        "one_time",
        "one-time-token",
    )
    assert parse_chosen_inline_session_token("sub:subscription-token") == (
        "paid",
        "subscription-token",
    )
    assert parse_chosen_inline_session_token("once:invoice-token") == (
        "paid",
        "invoice-token",
    )
    assert parse_chosen_inline_session_token("inline:   ") == (None, None)
    assert parse_chosen_inline_session_token("unknown:token") == (None, None)


def test_inline_session_is_expired_handles_missing_malformed_and_naive_dates():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert inline_session_is_expired({}, now=now) is True
    assert inline_session_is_expired({"expires_at": "not-a-date"}, now=now) is True
    assert (
        inline_session_is_expired(
            {"expires_at": "2026-01-01T11:59:59"}, now=now
        )
        is True
    )
    assert (
        inline_session_is_expired(
            {"expires_at": "2026-01-01T12:00:01+00:00"}, now=now
        )
        is False
    )


def test_record_inline_access_events_for_promo_and_subscription_sessions():
    store = _FakeStateStore()

    record_successful_inline_access(
        store,
        {
            "access_kind": "promo",
            "user_id": "1001",
            "session_token": "promo-token",
        },
    )
    record_successful_inline_access(
        store,
        {
            "access_kind": "subscription",
            "user_id": "1002",
            "session_token": "sub-token",
        },
    )
    record_failed_inline_access(
        store,
        {
            "access_kind": "subscription",
            "user_id": "1003",
            "session_token": "failed-token",
        },
    )
    record_failed_inline_access(
        store,
        {"access_kind": "free", "user_id": "1004", "session_token": "free-token"},
    )

    assert store.promo_successes == [1001]
    assert store.delivery_events == [
        {
            "user_id": 1002,
            "session_token": "sub-token",
            "access_kind": "subscription",
            "status": "success",
        },
        {
            "user_id": 1003,
            "session_token": "failed-token",
            "access_kind": "subscription",
            "status": "failed",
        },
    ]
