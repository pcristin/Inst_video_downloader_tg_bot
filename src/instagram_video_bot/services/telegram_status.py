"""Status message formatting and safe Telegram status mutations."""

from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardMarkup, Message

from .chaos_text import ChaosText, TextContext

logger = logging.getLogger(__name__)
_REPLY_MARKUP_UNSET = object()


def build_submission_message(
    provider_label: str,
    *,
    queue_position: int,
    joined_existing: bool = False,
    chaos_enabled: bool = False,
    language_code: str = "ru",
) -> str:
    """Build the queued/request accepted status text."""

    return ChaosText.submission(
        TextContext(
            provider_label=provider_label,
            chaos_enabled=chaos_enabled,
            language_code=language_code,
        ),
        queue_position=queue_position,
        joined_existing=joined_existing,
    )


def build_error_message(
    error: Exception,
    *,
    chaos_enabled: bool = False,
    language_code: str = "ru",
) -> str:
    """Build a user-visible download error message."""

    return ChaosText.error(
        error, chaos_enabled=chaos_enabled, language_code=language_code
    )


async def edit_status_message(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None | object = _REPLY_MARKUP_UNSET,
) -> None:
    """Try to edit a transient status message without creating extra chat noise."""

    try:
        await message.edit_text(text, **_markup_kwargs(reply_markup))
    except Exception:
        logger.debug("Failed to edit transient status message", exc_info=True)


async def safe_edit_text(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None | object = _REPLY_MARKUP_UNSET,
) -> None:
    """Edit status text, falling back to a new visible reply for important states."""

    try:
        await message.edit_text(text, **_markup_kwargs(reply_markup))
    except Exception:
        try:
            await message.reply_text(text, **_markup_kwargs(reply_markup))
        except Exception:
            logger.debug("Failed to edit or reply with status update", exc_info=True)


async def delete_status_message(message: Message) -> None:
    """Delete a transient status message after successful completion."""

    try:
        await message.delete()
    except Exception:
        logger.debug("Failed to delete transient status message", exc_info=True)


def _markup_kwargs(reply_markup: Any) -> dict[str, Any]:
    if reply_markup is _REPLY_MARKUP_UNSET:
        return {}
    return {"reply_markup": reply_markup}
