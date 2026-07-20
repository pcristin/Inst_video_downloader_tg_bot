import pytest

from src.instagram_video_bot.services.telegram.job_actions import (
    JobAction,
    build_job_action_data,
    cancel_keyboard,
    parse_job_action_data,
    retry_keyboard,
)
from src.instagram_video_bot.services.telegram_status import (
    edit_status_message,
    safe_edit_text,
)


class _Message:
    def __init__(self):
        self.edits = []
        self.replies = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def test_cancel_keyboard_uses_compact_request_callback():
    request_id = "a" * 32

    markup = cancel_keyboard(request_id, language_code="en")

    button = markup.inline_keyboard[0][0]
    assert button.text == "Cancel"
    assert button.callback_data == f"job:cancel:{request_id}"
    assert len(button.callback_data.encode()) <= 64


def test_retry_callback_round_trip():
    data = build_job_action_data(JobAction.RETRY, "request-1")

    assert parse_job_action_data(data) == (JobAction.RETRY, "request-1")


def test_russian_retry_keyboard_is_localized():
    markup = retry_keyboard("request-1", language_code="ru")

    assert markup.inline_keyboard[0][0].text == "Повторить"


@pytest.mark.parametrize(
    "data",
    [
        "",
        "job:unknown:request-1",
        "job:retry:",
        "job:retry:bad.request",
        f"job:retry:{'a' * 65}",
    ],
)
def test_invalid_callback_data_is_rejected(data):
    assert parse_job_action_data(data) is None


def test_builder_rejects_callback_over_telegram_limit():
    with pytest.raises(ValueError, match="64-byte"):
        build_job_action_data(JobAction.RETRY, "a" * 60)


@pytest.mark.asyncio
async def test_status_edit_can_replace_or_remove_reply_markup():
    message = _Message()
    markup = retry_keyboard("request-1", language_code="en")

    await edit_status_message(message, "failed", reply_markup=markup)
    await edit_status_message(message, "cancelled", reply_markup=None)

    assert message.edits == [
        ("failed", {"reply_markup": markup}),
        ("cancelled", {"reply_markup": None}),
    ]


@pytest.mark.asyncio
async def test_status_edit_without_markup_keeps_legacy_call_shape():
    message = _Message()

    await safe_edit_text(message, "running")

    assert message.edits == [("running", {})]
