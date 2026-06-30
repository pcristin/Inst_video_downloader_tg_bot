"""Direct Telegram message intake workflow."""

from __future__ import annotations

import asyncio
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ...config.settings import settings
from ..chaos_text import ChaosText
from ..request_parser import RequestParser
from .request_context import RequestContext


class TelegramRequestIntake:
    """Handle incoming text/caption messages and queue provider downloads."""

    def __init__(self, bot: Any):
        self._bot = bot

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming messages by queueing supported provider links."""
        bot = self._bot
        if (
            getattr(update, "edited_message", None)
            or getattr(update, "edited_channel_post", None)
            or getattr(update, "edited_business_message", None)
        ):
            return
        message = update.effective_message
        if not message or not update.effective_chat:
            return
        request_user_id = bot._request_user_id(update)
        if request_user_id is None:
            return
        if settings.BOT_LEGACY_REDIRECT_MODE and settings.BOT_MIGRATION_TARGET_USERNAME:
            await bot.legacy_redirect_handler(update, context)
            return

        bot._purge_expired_cache()
        language_code = bot._language_for_update(update)
        message_text = message.text or getattr(message, "caption", None) or ""
        extracted_links = RequestParser.extract_supported_links(
            message_text,
            limit=settings.MAX_LINKS_PER_MESSAGE,
        )
        if not extracted_links:
            return

        group_settings = bot.state_store.ensure_group_settings(update.effective_chat.id)
        raw_url_count = len(RequestParser.URL_PATTERN.findall(message_text))
        if raw_url_count > settings.MAX_LINKS_PER_MESSAGE:
            await message.reply_text(
                ChaosText.too_many_links(settings.MAX_LINKS_PER_MESSAGE, language_code)
            )

        for parsed_link in extracted_links:
            rate_limit = bot._consume_user_rate_limit(request_user_id, source="direct")
            if not rate_limit["allowed"]:
                await message.reply_text(
                    ChaosText.rate_limited(
                        rate_limit["retry_after_seconds"], language_code
                    )
                )
                break
            submission = bot.job_manager.submit(
                chat_id=update.effective_chat.id,
                user_id=request_user_id,
                user_label=bot._request_user_label(update),
                provider=parsed_link.provider,
                provider_label=parsed_link.provider_label,
                original_url=parsed_link.original_url,
                normalized_url=parsed_link.normalized_url,
                execute=bot._build_job_executor(
                    update.effective_chat.id, parsed_link, context
                ),
                duplicate_suppression=group_settings["duplicate_suppression"],
            )
            status_message = await message.reply_text(
                bot._build_submission_message(
                    parsed_link.provider_label,
                    queue_position=submission.queue_position,
                    joined_existing=not submission.is_new_job,
                    chaos_enabled=group_settings["chaos_mode_enabled"],
                    language_code=language_code,
                )
            )
            request_context = RequestContext(
                request_id=submission.request_id,
                chat_id=update.effective_chat.id,
                user_id=request_user_id,
                provider_label=parsed_link.provider_label,
                normalized_url=parsed_link.normalized_url,
                original_url=parsed_link.original_url,
                original_message_id=message.message_id,
                status_message=status_message,
                quiet_mode=group_settings["quiet_mode"],
                joined_existing=not submission.is_new_job,
                chaos_enabled=group_settings["chaos_mode_enabled"],
                language_code=language_code,
            )
            bot.request_contexts[submission.request_id] = request_context
            task = asyncio.create_task(
                bot._await_request(context, request_context, submission.job)
            )
            bot.active_request_tasks[submission.request_id] = task
            task.add_done_callback(
                lambda _task, rid=submission.request_id: bot._cleanup_request_task(rid)
            )
