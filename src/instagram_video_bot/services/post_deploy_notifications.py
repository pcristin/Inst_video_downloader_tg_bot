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
INLINE_PROMO_REFUND_ANNOUNCEMENT_KEY = "inline_promo_refund_2026_05_27"
INLINE_PROMO_REFUND_ANNOUNCEMENT_TEXT = (
    "Inline mode promo update:\n\n"
    "Your first 3 successful inline downloads are free. After that, inline mode "
    "requires a Telegram Stars subscription, admin whitelist access, or an optional "
    "one-time payment when enabled.\n\n"
    "Subscription protection is included: if 30% or more of completed inline "
    "deliveries fail because of bot/provider issues during your subscription period, "
    "the subscription is automatically refunded after the period ends.\n\n"
    "Direct links sent to the bot remain free."
)


async def send_inline_mode_announcement_once(
    bot,
    state_store: StateStore,
    *,
    pause_seconds: float = 0.05,
) -> dict[str, int]:
    """Send the inline-mode announcement once per historical requester."""

    return await _send_announcement_once(
        bot,
        state_store,
        notification_key=INLINE_MODE_ANNOUNCEMENT_KEY,
        text=INLINE_MODE_ANNOUNCEMENT_TEXT,
        pause_seconds=pause_seconds,
        log_label="Inline mode announcement",
    )


async def send_inline_promo_refund_announcement_once(
    bot,
    state_store: StateStore,
    *,
    pause_seconds: float = 0.05,
) -> dict[str, int]:
    """Send the inline promo/refund announcement once per historical requester."""

    return await _send_announcement_once(
        bot,
        state_store,
        notification_key=INLINE_PROMO_REFUND_ANNOUNCEMENT_KEY,
        text=INLINE_PROMO_REFUND_ANNOUNCEMENT_TEXT,
        pause_seconds=pause_seconds,
        log_label="Inline promo/refund announcement",
    )


async def _send_announcement_once(
    bot,
    state_store: StateStore,
    *,
    notification_key: str,
    text: str,
    pause_seconds: float,
    log_label: str,
) -> dict[str, int]:
    """Send one notification key once per historical requester."""

    sent = 0
    failed = 0
    skipped = 0
    user_ids = state_store.list_distinct_request_user_ids()

    for index, user_id in enumerate(user_ids):
        if state_store.notification_should_skip(
            notification_key, user_id
        ):
            skipped += 1
            continue

        state_store.record_user_notification(
            notification_key=notification_key,
            user_id=user_id,
            status="attempted",
        )

        try:
            await bot.send_message(chat_id=user_id, text=text)
        except Forbidden as exc:
            state_store.record_user_notification(
                notification_key=notification_key,
                user_id=user_id,
                status="failed",
                error_class=exc.__class__.__name__,
            )
            failed += 1
            logger.info("%s failed for user %s: %s", log_label, user_id, exc)
        except TelegramError as exc:
            state_store.record_user_notification(
                notification_key=notification_key,
                user_id=user_id,
                status="retryable_failed",
                error_class=exc.__class__.__name__,
            )
            failed += 1
            logger.info(
                "Inline mode announcement will retry for user %s after Telegram error: %s",
                user_id,
                exc,
            )
        else:
            state_store.record_user_notification(
                notification_key=notification_key,
                user_id=user_id,
                status="sent",
            )
            sent += 1

        if pause_seconds > 0 and index < len(user_ids) - 1:
            await asyncio.sleep(pause_seconds)

    return {"sent": sent, "failed": failed, "skipped": skipped}
