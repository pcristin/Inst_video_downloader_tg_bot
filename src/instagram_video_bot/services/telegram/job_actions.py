"""Compact Telegram callback payloads and keyboards for request actions."""

from __future__ import annotations

import re
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_DATA_LIMIT = 64
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class JobAction(str, Enum):
    CANCEL = "cancel"
    RETRY = "retry"


def build_job_action_data(action: JobAction, request_id: str) -> str:
    """Build validated callback data within Telegram's byte limit."""

    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ValueError("request_id contains unsupported callback characters")
    data = f"job:{action.value}:{request_id}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        raise ValueError("callback data exceeds Telegram's 64-byte limit")
    return data


def parse_job_action_data(data: str) -> tuple[JobAction, str] | None:
    """Parse trusted-shape job callback data, returning None when malformed."""

    if not data or len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        return None
    parts = data.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "job":
        return None
    try:
        action = JobAction(parts[1])
    except ValueError:
        return None
    request_id = parts[2]
    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        return None
    return action, request_id


def cancel_keyboard(
    request_id: str,
    *,
    language_code: str = "ru",
) -> InlineKeyboardMarkup:
    """Return the action shown while a request can still be cancelled."""

    label = "Cancel" if language_code == "en" else "Отмена"
    return _single_button_keyboard(label, JobAction.CANCEL, request_id)


def retry_keyboard(
    request_id: str,
    *,
    language_code: str = "ru",
) -> InlineKeyboardMarkup:
    """Return the action shown after a safely retryable failure."""

    label = "Retry" if language_code == "en" else "Повторить"
    return _single_button_keyboard(label, JobAction.RETRY, request_id)


def _single_button_keyboard(
    label: str,
    action: JobAction,
    request_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label, callback_data=build_job_action_data(action, request_id)
                )
            ]
        ]
    )
