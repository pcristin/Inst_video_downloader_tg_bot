"""Compact Telegram callback payloads and keyboards for inline sessions."""

from __future__ import annotations

import re
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_DATA_LIMIT = 64
_SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
INLINE_GALLERY_PATTERN = r"^inline-gallery:([A-Za-z0-9_-]+):([0-9]{1,3})$"


def parse_inline_gallery_data(data: str) -> tuple[str, int] | None:
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        return None
    match = re.fullmatch(INLINE_GALLERY_PATTERN, data)
    return (match[1], int(match[2])) if match else None


def inline_gallery_keyboard(
    session_token: str, index: int, count: int
) -> InlineKeyboardMarkup | None:
    if count <= 1:
        return None
    if (
        not _SESSION_TOKEN_PATTERN.fullmatch(session_token)
        or not 0 <= index < count <= 999
    ):
        raise ValueError("Invalid inline gallery")

    def button(label: str, target: int) -> InlineKeyboardButton:
        data = f"inline-gallery:{session_token}:{target}"
        if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
            raise ValueError("callback data exceeds Telegram's 64-byte limit")
        return InlineKeyboardButton(label, callback_data=data)

    return InlineKeyboardMarkup(
        [
            [
                button("‹", (index - 1) % count),
                button(f"{index + 1}/{count}", index),
                button("›", (index + 1) % count),
            ]
        ]
    )


class InlineAction(str, Enum):
    CANCEL = "cancel"
    RETRY = "retry"


def build_inline_action_data(action: InlineAction, session_token: str) -> str:
    """Build validated inline-session callback data."""

    if not _SESSION_TOKEN_PATTERN.fullmatch(session_token):
        raise ValueError("session_token contains unsupported callback characters")
    data = f"inline-action:{action.value}:{session_token}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        raise ValueError("callback data exceeds Telegram's 64-byte limit")
    return data


def parse_inline_action_data(data: str) -> tuple[InlineAction, str] | None:
    """Parse trusted-shape inline action data, returning None when malformed."""

    if not data or len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        return None
    parts = data.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "inline-action":
        return None
    try:
        action = InlineAction(parts[1])
    except ValueError:
        return None
    session_token = parts[2]
    if not _SESSION_TOKEN_PATTERN.fullmatch(session_token):
        return None
    return action, session_token


def inline_cancel_keyboard(
    session_token: str,
    *,
    language_code: str = "ru",
) -> InlineKeyboardMarkup:
    """Return the action shown while inline delivery is safely cancellable."""

    label = "Cancel" if language_code == "en" else "Отмена"
    return _single_button_keyboard(label, InlineAction.CANCEL, session_token)


def inline_retry_keyboard(
    session_token: str,
    *,
    language_code: str = "ru",
) -> InlineKeyboardMarkup:
    """Return the action shown after a safely retryable inline failure."""

    label = "Retry" if language_code == "en" else "Повторить"
    return _single_button_keyboard(label, InlineAction.RETRY, session_token)


def _single_button_keyboard(
    label: str,
    action: InlineAction,
    session_token: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_data=build_inline_action_data(action, session_token),
                )
            ]
        ]
    )
