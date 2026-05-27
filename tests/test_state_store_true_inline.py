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

    store.mark_inline_one_time_payment_delivered(delivered_payment_id, request_id="request-1")
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
        media_items=[{"media_type": "video", "file_id": "video-file-id", "caption": "caption"}],
    )

    cached = store.get_inline_cached_media("instagram:https://www.instagram.com/reel/abc/")
    assert cached is not None
    assert cached["media_items"][0]["file_id"] == "video-file-id"
