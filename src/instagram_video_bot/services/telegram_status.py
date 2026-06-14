"""Status message formatting and safe Telegram status mutations."""

from __future__ import annotations

import logging

from telegram import Message

from .chaos_text import ChaosText, TextContext

logger = logging.getLogger(__name__)


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


async def edit_status_message(message: Message, text: str) -> None:
    """Try to edit a transient status message without creating extra chat noise."""

    try:
        await message.edit_text(text)
    except Exception:
        logger.debug("Failed to edit transient status message", exc_info=True)


async def safe_edit_text(message: Message, text: str) -> None:
    """Edit status text, falling back to a new visible reply for important states."""

    try:
        await message.edit_text(text)
    except Exception:
        try:
            await message.reply_text(text)
        except Exception:
            logger.debug("Failed to edit or reply with status update", exc_info=True)


async def delete_status_message(message: Message) -> None:
    """Delete a transient status message after successful completion."""

    try:
        await message.delete()
    except Exception:
        logger.debug("Failed to delete transient status message", exc_info=True)
