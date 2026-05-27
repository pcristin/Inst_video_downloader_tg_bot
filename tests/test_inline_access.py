from src.instagram_video_bot.services.inline_access import (
    InlinePaymentPayload,
    build_inline_result_id,
    build_one_time_payload,
    build_subscription_payload,
    parse_inline_result_id,
    parse_inline_payment_payload,
    validate_star_amount,
)


def test_inline_result_id_contains_session_token():
    assert build_inline_result_id("abc123") == "inline:abc123"


def test_inline_result_id_parses_session_token():
    assert parse_inline_result_id("inline:abc123") == "abc123"


def test_invalid_inline_result_ids_return_none():
    assert parse_inline_result_id("bad:abc123") is None
    assert parse_inline_result_id("inline:") is None
    assert parse_inline_result_id("inline:   ") is None


def test_subscription_payload_round_trips():
    payload = build_subscription_payload(user_id=1001, session_token="s1")

    assert parse_inline_payment_payload(payload) == InlinePaymentPayload(
        kind="subscription",
        user_id=1001,
        session_token="s1",
    )


def test_subscription_payload_accepts_positional_arguments():
    assert build_subscription_payload(1001, "s1") == "inline_sub:v1:1001:s1"


def test_one_time_payload_round_trips():
    payload = build_one_time_payload(user_id=1001, session_token="s1")

    assert parse_inline_payment_payload(payload) == InlinePaymentPayload(
        kind="one_time",
        user_id=1001,
        session_token="s1",
    )


def test_one_time_payload_accepts_positional_arguments():
    assert build_one_time_payload(1001, "s1") == "inline_once:v1:1001:s1"


def test_invalid_payload_returns_none():
    assert parse_inline_payment_payload("bad:v1:1001:s1") is None
    assert parse_inline_payment_payload("inline_once:v1:nope:s1") is None


def test_payment_payload_rejects_empty_session_tokens():
    assert parse_inline_payment_payload("inline_sub:v1:1001:") is None
    assert parse_inline_payment_payload("inline_sub:v1:1001:   ") is None


def test_star_amount_validation_accepts_telegram_range():
    assert validate_star_amount("1") == 1
    assert validate_star_amount("10000") == 10000
    assert validate_star_amount("0") is None
    assert validate_star_amount("10001") is None
