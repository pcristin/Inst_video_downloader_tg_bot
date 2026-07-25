import pytest

from src.instagram_video_bot.services.telegram.inline_actions import (
    InlineAction,
    build_inline_action_data,
    inline_cancel_keyboard,
    inline_retry_keyboard,
    parse_inline_action_data,
)


def test_cancel_keyboard_uses_compact_session_callback():
    session_token = "s" * 24

    markup = inline_cancel_keyboard(session_token, language_code="en")

    button = markup.inline_keyboard[0][0]
    assert button.text == "Cancel"
    assert button.callback_data == f"inline-action:cancel:{session_token}"
    assert len(button.callback_data.encode()) <= 64


def test_retry_callback_round_trip():
    data = build_inline_action_data(InlineAction.RETRY, "session-1")

    assert parse_inline_action_data(data) == (InlineAction.RETRY, "session-1")


def test_russian_retry_keyboard_is_localized():
    markup = inline_retry_keyboard("session-1", language_code="ru")

    assert markup.inline_keyboard[0][0].text == "Повторить"


@pytest.mark.parametrize(
    "data",
    [
        "",
        "inline-action:unknown:session-1",
        "inline-action:retry:",
        "inline-action:retry:bad.session",
        f"inline-action:retry:{'s' * 50}",
    ],
)
def test_invalid_inline_action_data_is_rejected(data):
    assert parse_inline_action_data(data) is None


def test_builder_rejects_callback_over_telegram_limit():
    with pytest.raises(ValueError, match="64-byte"):
        build_inline_action_data(InlineAction.RETRY, "s" * 50)
