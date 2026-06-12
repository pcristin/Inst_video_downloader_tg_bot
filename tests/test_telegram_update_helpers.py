from types import SimpleNamespace

from src.instagram_video_bot.services.telegram_update_helpers import (
    forwarded_visible_user_id, language_from_profile, parse_positive_int_arg,
    parse_toggle_arg, request_user_id, request_user_label)


def _update(*, user=None, message=None):
    return SimpleNamespace(
        effective_user=user,
        effective_message=message,
    )


def test_request_user_id_uses_effective_user_before_sender_chat():
    user = SimpleNamespace(id=1001, username="alice", full_name="Alice")
    message = SimpleNamespace(sender_chat=SimpleNamespace(id=-100123))

    assert request_user_id(_update(user=user, message=message)) == 1001


def test_request_user_id_falls_back_to_sender_chat_id():
    message = SimpleNamespace(sender_chat=SimpleNamespace(id=-100123))

    assert request_user_id(_update(message=message)) == -100123


def test_request_user_label_prefers_username_then_sender_chat_title():
    user = SimpleNamespace(id=1001, username="alice", full_name="Alice")
    sender_chat_message = SimpleNamespace(
        sender_chat=SimpleNamespace(id=-100123, title="News Channel", username=None)
    )

    assert request_user_label(_update(user=user)) == "@alice"
    assert request_user_label(_update(message=sender_chat_message)) == "News Channel"


def test_language_from_profile_maps_russian_variants_to_ru():
    assert language_from_profile("ru") == "ru"
    assert language_from_profile("ru-RU") == "ru"
    assert language_from_profile("en-US") == "en"
    assert language_from_profile(None) == "en"


def test_forwarded_visible_user_id_supports_old_and_new_telegram_shapes():
    legacy_message = SimpleNamespace(forward_from=SimpleNamespace(id=1001))
    origin_message = SimpleNamespace(
        forward_from=None,
        forward_origin=SimpleNamespace(sender_user=SimpleNamespace(id=1002)),
    )

    assert forwarded_visible_user_id(legacy_message) == 1001
    assert forwarded_visible_user_id(origin_message) == 1002


def test_parse_toggle_arg_and_positive_int_arg():
    assert parse_toggle_arg("on") is True
    assert parse_toggle_arg("enable") is True
    assert parse_toggle_arg("0") is False
    assert parse_toggle_arg("maybe") is None
    assert parse_positive_int_arg("3") == 3
    assert parse_positive_int_arg("0") is None
    assert parse_positive_int_arg("abc") is None
