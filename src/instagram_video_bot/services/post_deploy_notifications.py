"""One-time notifications for existing users after deploys."""

from __future__ import annotations

import asyncio
import logging

from telegram.error import Forbidden, TelegramError

from .state_store import StateStore

logger = logging.getLogger(__name__)

INLINE_MODE_ANNOUNCEMENT_KEY = "inline_mode_paid_delivery_2026_05_27"
INLINE_MODE_ANNOUNCEMENT_TEXT = (
    "Inline mode is now available. You can use the bot from any chat by typing "
    "its username and pasting a supported link.\n\n"
    "Only inline mode is paid. Direct links sent to the bot still stay free.\n\n"
    "Inline access is available with a monthly Telegram Stars subscription, an "
    "admin whitelist, or an optional one-time Stars payment for a single delivery."
)


async def send_inline_mode_announcement_once(
    bot,
    state_store: StateStore,
    *,
    pause_seconds: float = 0.05,
) -> dict[str, int]:
    """Send the inline-mode announcement once per historical requester."""

    sent = 0
    failed = 0
    skipped = 0
    user_ids = state_store.list_distinct_request_user_ids()

    for index, user_id in enumerate(user_ids):
        if state_store.notification_was_attempted(
            INLINE_MODE_ANNOUNCEMENT_KEY, user_id
        ):
            skipped += 1
            continue

        try:
            await bot.send_message(chat_id=user_id, text=INLINE_MODE_ANNOUNCEMENT_TEXT)
        except (Forbidden, TelegramError) as exc:
            state_store.record_user_notification(
                notification_key=INLINE_MODE_ANNOUNCEMENT_KEY,
                user_id=user_id,
                status="failed",
                error_class=exc.__class__.__name__,
            )
            failed += 1
            logger.info("Inline mode announcement failed for user %s: %s", user_id, exc)
        else:
            state_store.record_user_notification(
                notification_key=INLINE_MODE_ANNOUNCEMENT_KEY,
                user_id=user_id,
                status="sent",
            )
            sent += 1

        if pause_seconds > 0 and index < len(user_ids) - 1:
            await asyncio.sleep(pause_seconds)

    return {"sent": sent, "failed": failed, "skipped": skipped}
