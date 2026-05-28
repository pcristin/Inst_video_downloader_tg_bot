from datetime import datetime, timedelta, timezone

from src.instagram_video_bot.config.settings import settings as app_settings
from src.instagram_video_bot.services.state_store import StateStore


def test_inline_session_lifecycle(tmp_path):
    store = StateStore(tmp_path / "state.db")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=expires_at,
    )
    store.attach_inline_message("s1", inline_message_id="inline-msg")

    session = store.get_inline_session("s1", user_id=1001)
    assert session["inline_message_id"] == "inline-msg"
    assert session["status"] == "chosen"


def test_whitelist_grants_inline_access(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.add_inline_whitelist_user(1001, added_by_user_id=42, note="friend")

    assert store.user_has_inline_access(1001) is True


def test_active_subscription_grants_inline_access(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.record_inline_subscription(
        user_id=1001,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=5,
    )

    assert store.has_active_inline_subscription(1001) is True
    assert store.user_has_inline_access(1001) is True


def test_naive_future_subscription_expiry_is_treated_as_utc(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.record_inline_subscription(
        user_id=1001,
        expires_at=datetime.now() + timedelta(days=30),
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=5,
    )

    assert store.has_active_inline_subscription(1001) is True


def test_expired_subscription_does_not_grant_inline_access(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.record_inline_subscription(
        user_id=1001,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=5,
    )

    assert store.has_active_inline_subscription(1001) is False
    assert store.user_has_inline_access(1001) is False


def test_runtime_billing_settings_can_change(tmp_path):
    store = StateStore(tmp_path / "state.db")

    settings = store.update_inline_runtime_settings(
        subscription_stars=5,
        one_time_enabled=True,
        one_time_stars=2,
    )

    assert settings["subscription_stars"] == 5
    assert settings["one_time_enabled"] is True
    assert settings["one_time_stars"] == 2


def test_runtime_billing_settings_default_to_config_values(tmp_path):
    store = StateStore(tmp_path / "state.db")

    settings = store.get_inline_runtime_settings()

    assert settings["subscription_stars"] == app_settings.INLINE_SUBSCRIPTION_STARS
    assert settings["one_time_enabled"] == app_settings.INLINE_ONE_TIME_ENABLED
    assert settings["one_time_stars"] == app_settings.INLINE_ONE_TIME_STARS


def test_runtime_billing_settings_ignore_malformed_int_values(tmp_path):
    store = StateStore(tmp_path / "state.db")
    now = datetime.now(timezone.utc).isoformat()
    with store._lock, store._conn:
        store._conn.executemany(
            """
            INSERT INTO inline_runtime_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            [
                ("subscription_stars", "many", now),
                ("one_time_stars", "few", now),
            ],
        )

    settings = store.get_inline_runtime_settings()

    assert settings["subscription_stars"] == app_settings.INLINE_SUBSCRIPTION_STARS
    assert settings["one_time_stars"] == app_settings.INLINE_ONE_TIME_STARS


def test_runtime_billing_settings_parse_true_boolean_token(tmp_path):
    store = StateStore(tmp_path / "state.db")
    with store._lock, store._conn:
        store._conn.execute(
            """
            INSERT INTO inline_runtime_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            ("one_time_enabled", "true", datetime.now(timezone.utc).isoformat()),
        )

    settings = store.get_inline_runtime_settings()

    assert settings["one_time_enabled"] is True


def test_one_time_payment_can_be_refunded(tmp_path):
    store = StateStore(tmp_path / "state.db")
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )

    store.mark_inline_one_time_payment_refunded(payment_id, reason="download_failed")

    payment = store.get_inline_one_time_payment(payment_id)
    assert payment["status"] == "refunded"
    assert payment["refund_reason"] == "download_failed"


def test_one_time_payment_can_be_found_by_telegram_charge_id(tmp_path):
    store = StateStore(tmp_path / "state.db")
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )

    payment = store.get_inline_one_time_payment_by_charge_id("tg-charge")

    assert payment["payment_id"] == payment_id
    assert payment["telegram_payment_charge_id"] == "tg-charge"
    assert store.get_inline_one_time_payment_by_charge_id("missing") is None


def test_one_time_payment_can_be_claimed_as_link_entitlement(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="invoice-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="invoice-session",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )

    payment = store.get_available_inline_one_time_payment(
        user_id=1001,
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/abc/",
    )
    claimed = store.claim_inline_one_time_payment(
        payment_id, request_id="inline:delivery-session"
    )
    duplicate_claimed = store.claim_inline_one_time_payment(
        payment_id, request_id="inline:other"
    )

    assert payment["payment_id"] == payment_id
    assert claimed is True
    assert duplicate_claimed is False
    assert (
        store.get_inline_one_time_payment(payment_id)["request_id"]
        == "inline:delivery-session"
    )
    assert (
        store.get_available_inline_one_time_payment(
            user_id=1001,
            provider="instagram",
            normalized_url="https://www.instagram.com/reel/abc/",
        )
        is None
    )


def test_stale_one_time_payment_claim_becomes_available_again(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="invoice-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="invoice-session",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )
    store.claim_inline_one_time_payment(payment_id, request_id="inline:lost-session")
    stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE inline_one_time_payments SET updated_at = ? WHERE payment_id = ?",
            (stale_time.isoformat(), payment_id),
        )

    released = store.release_stale_inline_one_time_claims(
        older_than=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert released == 1
    assert store.get_inline_one_time_payment(payment_id)["request_id"] is None


def test_stale_one_time_payment_claim_keeps_active_delivery_claimed(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="invoice-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.create_inline_session(
        session_token="delivery-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="invoice-session",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )
    store.attach_inline_message("delivery-session", inline_message_id="inline-msg")
    store.mark_inline_session_status("delivery-session", "delivering")
    store.claim_inline_one_time_payment(
        payment_id, request_id="inline:delivery-session"
    )
    stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE inline_one_time_payments SET updated_at = ? WHERE payment_id = ?",
            (stale_time.isoformat(), payment_id),
        )

    released = store.release_stale_inline_one_time_claims(
        older_than=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert released == 0
    assert (
        store.get_inline_one_time_payment(payment_id)["request_id"]
        == "inline:delivery-session"
    )


def test_stale_one_time_payment_claim_releases_abandoned_delivery_session(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="invoice-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.create_inline_session(
        session_token="delivery-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="invoice-session",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )
    store.attach_inline_message("delivery-session", inline_message_id="inline-msg")
    store.mark_inline_session_status("delivery-session", "delivering")
    store.claim_inline_one_time_payment(
        payment_id, request_id="inline:delivery-session"
    )
    stale_time = datetime.now(timezone.utc) - timedelta(hours=8)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE inline_one_time_payments SET updated_at = ? WHERE payment_id = ?",
            (stale_time.isoformat(), payment_id),
        )
        store._conn.execute(
            "UPDATE inline_sessions SET updated_at = ? WHERE session_token = ?",
            (stale_time.isoformat(), "delivery-session"),
        )

    released = store.release_stale_inline_one_time_claims(
        older_than=datetime.now(timezone.utc) - timedelta(hours=6)
    )

    assert released == 1
    assert store.get_inline_one_time_payment(payment_id)["request_id"] is None


def test_refunded_one_time_payment_cannot_later_be_delivered(tmp_path):
    store = StateStore(tmp_path / "state.db")
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )
    store.mark_inline_one_time_payment_refunded(payment_id, reason="download_failed")

    store.mark_inline_one_time_payment_delivered(payment_id, request_id="request-1")

    payment = store.get_inline_one_time_payment(payment_id)
    assert payment["status"] == "refunded"
    assert payment["request_id"] is None
    assert payment["refund_reason"] == "download_failed"


def test_missing_one_time_payment_transition_does_not_create_row(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.mark_inline_one_time_payment_refunded("missing", reason="download_failed")

    assert store.get_inline_one_time_payment("missing") is None


def test_one_time_payment_id_is_uuid_hex(tmp_path):
    store = StateStore(tmp_path / "state.db")

    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="tg-charge",
        total_amount=2,
    )

    assert len(payment_id) == 32
    int(payment_id, 16)


def test_one_time_payment_delivered_and_refund_failed_transitions(tmp_path):
    store = StateStore(tmp_path / "state.db")
    delivered_payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="tg-charge-1",
        total_amount=2,
    )
    refund_failed_payment_id = store.record_inline_one_time_payment(
        user_id=1002,
        session_token="s2",
        telegram_payment_charge_id="tg-charge-2",
        total_amount=2,
    )

    store.mark_inline_one_time_payment_delivered(
        delivered_payment_id, request_id="request-1"
    )
    store.mark_inline_one_time_payment_refund_failed(
        refund_failed_payment_id,
        reason="telegram_refund_error",
    )

    delivered = store.get_inline_one_time_payment(delivered_payment_id)
    refund_failed = store.get_inline_one_time_payment(refund_failed_payment_id)
    assert delivered["status"] == "delivered"
    assert delivered["request_id"] == "request-1"
    assert delivered["refund_reason"] is None
    assert refund_failed["status"] == "refund_failed"
    assert refund_failed["request_id"] is None
    assert refund_failed["refund_reason"] == "telegram_refund_error"


def test_whitelist_users_can_be_listed_and_removed(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.add_inline_whitelist_user(1001, added_by_user_id=42, note="friend")

    users = store.list_inline_whitelist_users()
    assert users[0]["user_id"] == 1001
    assert users[0]["note"] == "friend"

    store.remove_inline_whitelist_user(1001)

    assert store.is_inline_whitelisted(1001) is False
    assert store.list_inline_whitelist_users() == []


def test_cached_inline_media_round_trips(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.save_inline_cached_media(
        cache_key="instagram:https://www.instagram.com/reel/abc/",
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/abc/",
        media_items=[
            {"media_type": "video", "file_id": "video-file-id", "caption": "caption"}
        ],
    )

    cached = store.get_inline_cached_media(
        "instagram:https://www.instagram.com/reel/abc/"
    )
    assert cached is not None
    assert cached["media_items"][0]["file_id"] == "video-file-id"


def test_user_rate_limit_uses_sliding_window(tmp_path):
    store = StateStore(tmp_path / "state.db")
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)

    first = store.check_user_rate_limit(1001, limit=2, window_seconds=60, now=now)
    second = store.check_user_rate_limit(
        1001, limit=2, window_seconds=60, now=now + timedelta(seconds=10)
    )
    third = store.check_user_rate_limit(
        1001, limit=2, window_seconds=60, now=now + timedelta(seconds=20)
    )
    after_window = store.check_user_rate_limit(
        1001, limit=2, window_seconds=60, now=now + timedelta(seconds=61)
    )

    assert first["allowed"] is True
    assert second["allowed"] is True
    assert third["allowed"] is False
    assert third["retry_after_seconds"] == 40
    assert after_window["allowed"] is True


def test_inline_promo_success_count_is_lifetime_per_user(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.record_inline_promo_success(1001)
    store.record_inline_promo_success(1001)
    store.record_inline_promo_success(1002)

    assert store.get_inline_promo_success_count(1001) == 2
    assert store.get_inline_promo_success_count(1002) == 1
    assert store.get_inline_promo_success_count(9999) == 0


def test_subscription_delivery_stats_count_period_events(tmp_path):
    store = StateStore(tmp_path / "state.db")
    started_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    expires_at = datetime(2026, 6, 1, tzinfo=timezone.utc)

    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s1",
        access_kind="subscription",
        status="success",
        occurred_at=started_at + timedelta(days=1),
    )
    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s2",
        access_kind="subscription",
        status="failed",
        occurred_at=started_at + timedelta(days=2),
    )
    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s3",
        access_kind="subscription",
        status="failed",
        occurred_at=expires_at + timedelta(seconds=1),
    )
    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s4",
        access_kind="promo",
        status="failed",
        occurred_at=started_at + timedelta(days=3),
    )

    stats = store.get_subscription_delivery_stats(
        user_id=1001,
        started_at=started_at,
        expires_at=expires_at,
    )

    assert stats == {"success": 1, "failed": 1, "attempts": 2, "failure_rate": 0.5}


def test_expired_unchecked_subscriptions_are_listed_and_marked(tmp_path):
    store = StateStore(tmp_path / "state.db")
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    store.record_inline_subscription(
        user_id=1001,
        expires_at=now - timedelta(seconds=1),
        telegram_payment_charge_id="charge-1",
        provider_payment_charge_id="provider-1",
        total_amount=100,
        started_at=now - timedelta(days=30),
    )
    store.record_inline_subscription(
        user_id=1002,
        expires_at=now + timedelta(days=1),
        telegram_payment_charge_id="charge-2",
        provider_payment_charge_id="provider-2",
        total_amount=100,
        started_at=now - timedelta(days=29),
    )

    expired = store.list_expired_unchecked_inline_subscriptions(now=now)
    store.mark_inline_subscription_auto_refunded(1001, reason="failure_rate:0.50")

    assert [row["user_id"] for row in expired] == [1001]
    subscription = store.get_inline_subscription(1001)
    assert subscription["status"] == "auto_refunded"
    assert subscription["refund_reason"] == "failure_rate:0.50"
    assert subscription["auto_refund_checked_at"] is not None


def test_user_language_defaults_to_none_and_can_be_persisted(tmp_path):
    store = StateStore(tmp_path / "state.db")

    assert store.get_user_language(1001) is None

    store.set_user_language(1001, "en")
    assert store.get_user_language(1001) == "en"

    store.set_user_language(1001, "ru")
    assert store.get_user_language(1001) == "ru"
