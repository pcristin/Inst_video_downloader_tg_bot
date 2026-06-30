"""Telegram bot service for handling group-friendly media downloads."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                      InlineQueryResultArticle, InputInvoiceMessageContent,
                      InputTextMessageContent, LabeledPrice, Message, Update)
from telegram.error import NetworkError, TelegramError
from telegram.ext import Application, ContextTypes

from ..config.settings import settings
from .chaos_text import ChaosText, TextContext
from .inline_access import (build_inline_result_id,
                            build_one_time_entitlement_result_id,
                            build_one_time_payload, build_subscription_payload,
                            generate_session_token,
                            parse_inline_payment_payload, validate_star_amount)
from .inline_delivery import (InlineCachedMediaItem, build_inline_input_media,
                              upload_first_media_to_storage)
from .job_manager import JobManager, SharedJob
from .request_parser import ParsedRequestLink, RequestParser
from .state_store import CachedMediaEntry, StateStore
from .telegram_cache import purge_expired_cache_files, video_info_from_cache
from .telegram_inline_sessions import (inline_session_is_expired,
                                       parse_chosen_inline_session_token,
                                       record_failed_inline_access,
                                       record_successful_inline_access,
                                       subscription_expires_at)
from .telegram_media_sender import TelegramMediaSender
from .telegram_media_retry import classify_telegram_delivery_error
from .telegram_performance import (build_admin_performance_summary,
                                   format_performance_summary)
from .telegram_provider_metrics import record_provider_metrics
from .telegram_status import (build_error_message, build_submission_message,
                              delete_status_message, edit_status_message,
                              safe_edit_text)
from .telegram_update_helpers import (language_from_profile,
                                      parse_positive_int_arg, parse_toggle_arg,
                                      request_user_id, request_user_label,
                                      user_label)
from .telegram_wiring import build_telegram_application
from .telegram.command_handlers import TelegramCommandHandlers
from .telegram.request_context import RequestContext
from .telegram.request_intake import TelegramRequestIntake
from .video_downloader import (DownloadError, MediaItem, VideoDownloader,
                               VideoDownloadError, VideoInfo)

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for downloading media links."""

    INSTAGRAM_VIDEO_PATTERN = RequestParser.URL_PATTERN
    MAX_MEDIA_CAPTION_LENGTH = TelegramMediaSender.MAX_MEDIA_CAPTION_LENGTH
    TELEGRAM_MEDIA_GROUP_LIMIT = TelegramMediaSender.TELEGRAM_MEDIA_GROUP_LIMIT

    def __init__(self, state_store: StateStore | None = None):
        self.application: Optional[Application] = None
        self.state_store = state_store or StateStore()
        self.media_sender = TelegramMediaSender(self.state_store)
        self.job_manager = JobManager(self.state_store)
        self.job_manager.add_state_listener(self._on_job_state_change)
        self.active_request_tasks: dict[str, asyncio.Task[None]] = {}
        self.request_contexts: dict[str, RequestContext] = {}
        self.request_intake = TelegramRequestIntake(self)
        self.command_handlers = TelegramCommandHandlers(self)
        self._inline_delivery_session_tokens: set[str] = set()
        self.started_at = time.time()
        self._purge_expired_cache()

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming messages by queueing supported provider links."""
        await self.request_intake.handle_message(update, context)

    async def start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Greet a user and show the shortest useful onboarding message."""
        await self.command_handlers.start_command(update, context)

    async def language_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Persist a user's language preference."""
        await self.command_handlers.language_command(update, context)

    async def help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show supported providers and usage help."""
        await self.command_handlers.help_command(update, context)

    async def admin_help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show owner-only operational command usage."""
        await self.command_handlers.admin_help_command(update, context)

    async def legacy_redirect_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Redirect any old-bot message to the new bot username."""
        if not update.message or not settings.BOT_MIGRATION_TARGET_USERNAME:
            return
        await update.message.reply_text(
            ChaosText.bot_migration_redirect(settings.BOT_MIGRATION_TARGET_USERNAME)
        )

    async def legacy_callback_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Redirect stale inline callbacks from the old bot."""
        if not update.callback_query or not settings.BOT_MIGRATION_TARGET_USERNAME:
            return
        await update.callback_query.answer(
            ChaosText.bot_migration_redirect(settings.BOT_MIGRATION_TARGET_USERNAME),
            show_alert=True,
        )

    async def legacy_inline_query_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Return a migration result for inline queries sent to the old bot."""
        if not update.inline_query or not settings.BOT_MIGRATION_TARGET_USERNAME:
            return
        username = settings.BOT_MIGRATION_TARGET_USERNAME.strip().removeprefix("@")
        await update.inline_query.answer(
            [
                InlineQueryResultArticle(
                    id=f"bot_migration_{username}",
                    title=f"Мы переехали в @{username}",
                    description=f"Открой нового бота: https://t.me/{username}",
                    input_message_content=InputTextMessageContent(
                        ChaosText.bot_migration_redirect(username)
                    ),
                )
            ],
            cache_time=30,
            is_personal=True,
        )

    async def formats_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show supported URL shapes."""
        await self.command_handlers.formats_command(update, context)

    async def status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show a safe queue and health summary."""
        await self.command_handlers.status_command(update, context)

    async def cancel_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Cancel the latest active request from the current user."""
        await self.command_handlers.cancel_command(update, context)

    async def stats_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show lightweight group stats."""
        await self.command_handlers.stats_command(update, context)

    async def chaos_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle or inspect chat-level chaos mode."""
        await self.command_handlers.chaos_command(update, context)

    async def quiet_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle quiet mode for the current chat. Owner-only."""
        await self.command_handlers.quiet_command(update, context)

    async def dupes_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle duplicate suppression for the current chat. Owner-only."""
        await self.command_handlers.dupes_command(update, context)

    async def statsmode_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle stats collection visibility for the current chat. Owner-only."""
        await self.command_handlers.statsmode_command(update, context)

    async def chatlimit_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Override per-chat concurrent job limit. Owner-only."""
        await self.command_handlers.chatlimit_command(update, context)

    async def userlimit_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Override per-user active job limit for the current chat. Owner-only."""
        await self.command_handlers.userlimit_command(update, context)

    async def admin_status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show owner-facing operational status for the current chat."""
        if not update.message or not update.effective_chat:
            return
        if not await self._require_owner(update):
            return
        snapshot = self.job_manager.get_snapshot(update.effective_chat.id)
        admin_status = self.state_store.get_admin_status(update.effective_chat.id)
        performance = self._build_admin_performance_summary(
            chat_id=update.effective_chat.id,
            duplicate_joins=self.state_store.get_group_stats(update.effective_chat.id)[
                "duplicate_joins"
            ],
            recent_failures=admin_status["recent_failures"],
        )
        settings_row = admin_status["settings"]
        provider_lines = (
            ", ".join(
                f"{provider}:{status}={count}"
                for provider, status, count in admin_status["provider_job_counts"]
            )
            or "нет"
        )
        failure_lines = (
            "\n".join(
                f"  - {provider} | {error_class} | {normalized_url}"
                for provider, normalized_url, error_class, _finished_at in admin_status[
                    "recent_failures"
                ]
            )
            or "  - нет"
        )
        uptime_seconds = int(time.time() - self.started_at)
        await update.message.reply_text(
            "Админ-статус:\n"
            f"- Аптайм: {uptime_seconds}с\n"
            f"- Тихий режим: {self._ru_on_off(settings_row['quiet_mode'])}\n"
            f"- Защита от повторов: {self._ru_on_off(settings_row['duplicate_suppression'], feminine=True)}\n"
            f"- Статистика: {self._ru_on_off(settings_row['stats_enabled'], feminine=True)}\n"
            f"- Режим хаоса: {self._ru_on_off(settings_row['chaos_mode_enabled'])}\n"
            f"- Лимит чата: {settings_row['chat_max_concurrent_jobs']}\n"
            f"- Лимит на пользователя: {settings_row['user_max_active_jobs']}\n"
            f"- Выполняется задач: {admin_status['running_jobs']}\n"
            f"- В очереди задач: {admin_status['queued_jobs']}\n"
            f"- Зависших активных задач: {admin_status['stale_active_jobs']}\n"
            f"- Provider timeout за час: {admin_status['recent_provider_timeouts']}\n"
            f"- Активные запросы: {snapshot['active_requests']}\n"
            f"- Ошибочных задач: {admin_status['failed_jobs']}\n"
            f"- Записей в кэше: {admin_status['cache_entries']}\n"
            f"- Кэш результатов: {self._ru_on_off(settings.RESULT_CACHE_ENABLED)}\n"
            f"- Менеджер очереди: {self._ru_on_off(settings.QUEUE_MANAGER_ENABLED)}\n"
            f"- Задачи по площадкам: {provider_lines}\n"
            f"- Последние ошибки:\n{failure_lines}\n\n"
            f"{self._format_performance_summary(performance)}"
        )

    async def admin_global_status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show owner-facing operational status across all chats."""
        if not update.message:
            return
        if not await self._require_owner(update):
            return
        snapshot = self.job_manager.get_global_snapshot()
        admin_status = self.state_store.get_global_admin_status()
        performance = self._build_admin_performance_summary(
            chat_id=None,
            duplicate_joins=admin_status["duplicate_joins"],
            recent_failures=admin_status["recent_failures"],
        )
        provider_lines = (
            ", ".join(
                f"{provider}:{status}={count}"
                for provider, status, count in admin_status["provider_job_counts"]
            )
            or "нет"
        )
        failure_lines = (
            "\n".join(
                f"  - {provider} | {error_class} | {normalized_url}"
                for provider, normalized_url, error_class, _finished_at in admin_status[
                    "recent_failures"
                ]
            )
            or "  - нет"
        )
        uptime_seconds = int(time.time() - self.started_at)
        await update.message.reply_text(
            "Глобальный админ-статус:\n"
            f"- Аптайм: {uptime_seconds}с\n"
            f"- Чатов с задачами: {admin_status['chats_with_jobs']}\n"
            f"- Пользователей с запросами: {admin_status['users_with_requests']}\n"
            f"- Выполняется задач: {admin_status['running_jobs']}\n"
            f"- В очереди задач: {admin_status['queued_jobs']}\n"
            f"- Зависших активных задач: {admin_status['stale_active_jobs']}\n"
            f"- Provider timeout за час: {admin_status['recent_provider_timeouts']}\n"
            f"- Активные запросы: {snapshot['active_requests']}\n"
            f"- Глобальный лимит: {snapshot['global_limit']}\n"
            f"- Ошибочных задач: {admin_status['failed_jobs']}\n"
            f"- Записей в кэше: {admin_status['cache_entries']}\n"
            f"- Кэш результатов: {self._ru_on_off(settings.RESULT_CACHE_ENABLED)}\n"
            f"- Менеджер очереди: {self._ru_on_off(settings.QUEUE_MANAGER_ENABLED)}\n"
            f"- Задачи по площадкам: {provider_lines}\n"
            f"- Последние ошибки:\n{failure_lines}\n\n"
            f"{self._format_performance_summary(performance)}"
        )

    async def inline_whitelist_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Manage owner-controlled true-inline whitelist."""
        if not update.message:
            return
        if not await self._require_owner(update):
            return
        args = list(getattr(context, "args", []) or [])
        action = args[0].lower() if args else ""
        if action == "list":
            users = self.state_store.list_inline_whitelist_users()
            await update.message.reply_text(ChaosText.inline_whitelist_list(users))
            return
        if action not in {"add", "remove"} or len(args) != 2:
            await update.message.reply_text(ChaosText.inline_whitelist_usage())
            return
        user_id = self._parse_positive_int_arg(args[1])
        if user_id is None:
            await update.message.reply_text(ChaosText.inline_whitelist_usage())
            return
        if action == "add":
            self.state_store.add_inline_whitelist_user(
                user_id,
                added_by_user_id=update.effective_user.id,
                note="owner_command",
            )
            await update.message.reply_text(ChaosText.inline_whitelist_added(user_id))
            return
        self.state_store.remove_inline_whitelist_user(user_id)
        await update.message.reply_text(ChaosText.inline_whitelist_removed(user_id))

    async def inline_price_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Manage Stars subscription price for true-inline mode."""
        if not update.message:
            return
        if not await self._require_owner(update):
            return
        args = list(getattr(context, "args", []) or [])
        if len(args) != 2 or args[0].lower() != "subscription":
            await update.message.reply_text(ChaosText.inline_price_usage())
            return
        stars = validate_star_amount(args[1])
        if stars is None:
            await update.message.reply_text(ChaosText.inline_price_usage())
            return
        runtime = self.state_store.update_inline_runtime_settings(
            subscription_stars=stars
        )
        await update.message.reply_text(
            ChaosText.inline_subscription_price_updated(runtime["subscription_stars"])
        )

    async def inline_onetime_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Manage optional one-time Stars payments for true-inline mode."""
        if not update.message:
            return
        if not await self._require_owner(update):
            return
        args = list(getattr(context, "args", []) or [])
        action = args[0].lower() if args else ""
        if action == "off" and len(args) == 1:
            runtime = self.state_store.update_inline_runtime_settings(
                one_time_enabled=False
            )
            await update.message.reply_text(ChaosText.inline_onetime_updated(runtime))
            return
        if action != "on" or len(args) != 2:
            await update.message.reply_text(ChaosText.inline_onetime_usage())
            return
        stars = validate_star_amount(args[1])
        if stars is None:
            await update.message.reply_text(ChaosText.inline_onetime_usage())
            return
        runtime = self.state_store.update_inline_runtime_settings(
            one_time_enabled=True,
            one_time_stars=stars,
        )
        await update.message.reply_text(ChaosText.inline_onetime_updated(runtime))

    async def inline_refund_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Refund a Telegram Stars inline payment. Owner-only."""
        if not update.message:
            return
        if not await self._require_owner(update):
            return

        args = list(getattr(context, "args", []) or [])
        if len(args) not in {1, 2} or not args[0].strip():
            await update.message.reply_text(ChaosText.inline_refund_usage())
            return

        telegram_payment_charge_id = args[0].strip()
        fallback_user_id = None
        if len(args) == 2:
            fallback_user_id = self._parse_positive_int_arg(args[1])
            if fallback_user_id is None:
                await update.message.reply_text(ChaosText.inline_refund_usage())
                return

        payment_kind: str | None = None
        payment_id: str | None = None
        user_id = fallback_user_id
        one_time_payment = self.state_store.get_inline_one_time_payment_by_charge_id(
            telegram_payment_charge_id
        )
        if one_time_payment is not None:
            if one_time_payment["status"] == "refunded":
                await update.message.reply_text(
                    ChaosText.inline_refund_already_refunded()
                )
                return
            payment_kind = "one_time"
            payment_id = one_time_payment["payment_id"]
            user_id = int(one_time_payment["user_id"])
        else:
            subscription = self.state_store.get_inline_subscription_by_charge_id(
                telegram_payment_charge_id
            )
            if subscription is not None:
                if subscription["status"] == "refunded":
                    await update.message.reply_text(
                        ChaosText.inline_refund_already_refunded()
                    )
                    return
                payment_kind = "subscription"
                user_id = int(subscription["user_id"])

        if user_id is None:
            await update.message.reply_text(ChaosText.inline_refund_not_found())
            return

        try:
            await context.bot.refund_star_payment(
                user_id=user_id,
                telegram_payment_charge_id=telegram_payment_charge_id,
            )
        except TelegramError:
            logger.exception(
                "Owner inline refund failed for charge %s", telegram_payment_charge_id
            )
            if payment_kind == "one_time" and payment_id is not None:
                self.state_store.mark_inline_one_time_payment_refund_failed(
                    payment_id,
                    reason="owner_command:TelegramError",
                )
            await update.message.reply_text(ChaosText.inline_refund_failed())
            return

        if payment_kind == "one_time" and payment_id is not None:
            self.state_store.mark_inline_one_time_payment_refunded(
                payment_id,
                reason="owner_command",
            )
        elif payment_kind == "subscription":
            self.state_store.mark_inline_subscription_refunded(user_id)
        await update.message.reply_text(ChaosText.inline_refund_sent(user_id))

    async def inline_query_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Return true inline placeholder results that will be edited into media."""

        query = update.inline_query
        if not query or not settings.INLINE_MODE_ENABLED:
            return
        await self._evaluate_expired_inline_subscription_refunds(context)
        parsed_links = RequestParser.extract_supported_links(query.query, limit=1)
        if not parsed_links:
            result = InlineQueryResultArticle(
                id="inline-help",
                title="Paste a supported media link",
                input_message_content=InputTextMessageContent(
                    "Paste an Instagram, X/Twitter, or YouTube Shorts link."
                ),
            )
            await query.answer([result], cache_time=0, is_personal=True)
            return

        parsed_link = parsed_links[0]
        if settings.INLINE_STORAGE_CHAT_ID is None:
            result = InlineQueryResultArticle(
                id="inline-storage-missing",
                title="Inline delivery is not configured",
                input_message_content=InputTextMessageContent(
                    ChaosText.inline_storage_missing()
                ),
            )
            await query.answer([result], cache_time=0, is_personal=True)
            return

        access_kind = self._paid_or_free_inline_access_kind_for_user(query.from_user.id)
        if access_kind is not None:
            await self._answer_inline_delivery_option(
                query,
                parsed_link,
                one_time_entitlement=False,
                access_kind=access_kind,
            )
            return

        if settings.INLINE_SUBSCRIPTION_REQUIRED:
            self.state_store.release_stale_inline_one_time_claims(
                older_than=datetime.now(timezone.utc)
                - timedelta(seconds=settings.INLINE_ONE_TIME_CLAIM_RECOVERY_SECONDS)
            )
            one_time_payment = self.state_store.get_available_inline_one_time_payment(
                user_id=query.from_user.id,
                provider=parsed_link.provider,
                normalized_url=parsed_link.normalized_url,
            )
            if one_time_payment is not None:
                await self._answer_inline_delivery_option(
                    query,
                    parsed_link,
                    one_time_entitlement=True,
                    access_kind="one_time",
                )
                return
            if (
                self.state_store.get_inline_promo_success_count(query.from_user.id)
                < settings.INLINE_FREE_SUCCESSFUL_DELIVERIES
            ):
                await self._answer_inline_delivery_option(
                    query,
                    parsed_link,
                    one_time_entitlement=False,
                    access_kind="promo",
                )
                return
            await self._answer_paid_inline_options(context, query, parsed_link)
            return

        await self._answer_inline_delivery_option(
            query,
            parsed_link,
            one_time_entitlement=False,
            access_kind="free",
        )

    async def _answer_inline_delivery_option(
        self,
        query: Any,
        parsed_link: ParsedRequestLink,
        *,
        one_time_entitlement: bool,
        access_kind: str,
    ) -> None:
        session_token = generate_session_token()
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.INLINE_SESSION_TTL_SECONDS
        )
        self.state_store.create_inline_session(
            session_token=session_token,
            user_id=query.from_user.id,
            original_url=parsed_link.original_url,
            normalized_url=parsed_link.normalized_url,
            provider=parsed_link.provider,
            provider_label=parsed_link.provider_label,
            expires_at=expires_at,
            access_kind=access_kind,
        )
        result_id = (
            build_one_time_entitlement_result_id(session_token)
            if one_time_entitlement
            else build_inline_result_id(session_token)
        )
        callback_prefix = "inline_once" if one_time_entitlement else "inline"
        result = InlineQueryResultArticle(
            id=result_id,
            title="Send media here",
            description=parsed_link.provider_label,
            input_message_content=InputTextMessageContent(
                ChaosText.inline_preparing(parsed_link.provider_label)
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Preparing",
                            callback_data=f"{callback_prefix}:{session_token}",
                        )
                    ]
                ]
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)

    async def chosen_inline_result_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Capture the inline message ID and start delivery."""

        chosen = update.chosen_inline_result
        if not chosen or not chosen.inline_message_id:
            return
        result_kind, session_token = self._parse_chosen_inline_session_token(
            chosen.result_id
        )
        if not session_token:
            return
        if result_kind == "paid":
            return
        claim_status = self._claim_inline_delivery_session(
            session_token,
            user_id=chosen.from_user.id,
            inline_message_id=chosen.inline_message_id,
        )
        if claim_status != "claimed":
            return
        rate_limit = self._consume_user_rate_limit(chosen.from_user.id, source="inline")
        if not rate_limit["allowed"]:
            self._inline_delivery_session_tokens.discard(session_token)
            self.state_store.mark_inline_session_status(session_token, "failed")
            await self._safe_edit_inline_text(
                context,
                inline_message_id=chosen.inline_message_id,
                text=ChaosText.rate_limited(rate_limit["retry_after_seconds"]),
            )
            return
        one_time_payment_id = None
        if result_kind == "one_time":
            one_time_payment_id = self._claim_one_time_payment_for_session(
                session_token,
                user_id=chosen.from_user.id,
            )
            if one_time_payment_id is None:
                self._inline_delivery_session_tokens.discard(session_token)
                self.state_store.mark_inline_session_status(session_token, "failed")
                await self._safe_edit_inline_text(
                    context,
                    inline_message_id=chosen.inline_message_id,
                    text=ChaosText.inline_delivery_failed(),
                )
                return
        self._schedule_inline_delivery(
            context,
            session_token=session_token,
            one_time_payment_id=one_time_payment_id,
        )

    async def inline_callback_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Fallback: user taps the inline keyboard if chosen feedback did not start delivery."""

        query = update.callback_query
        if (
            not query
            or not query.inline_message_id
            or not query.data
            or not query.from_user
        ):
            return
        result_kind, session_token = self._parse_chosen_inline_session_token(query.data)
        if not session_token or result_kind == "paid":
            return
        claim_status = self._claim_inline_delivery_session(
            session_token,
            user_id=query.from_user.id,
            inline_message_id=query.inline_message_id,
        )
        if claim_status == "expired":
            await query.answer("This inline request expired.")
            return
        if claim_status == "duplicate":
            await query.answer("Already preparing media.")
            return
        rate_limit = self._consume_user_rate_limit(query.from_user.id, source="inline")
        if not rate_limit["allowed"]:
            self._inline_delivery_session_tokens.discard(session_token)
            self.state_store.mark_inline_session_status(session_token, "failed")
            await query.answer("Rate limit reached.")
            await self._safe_edit_inline_text(
                context,
                inline_message_id=query.inline_message_id,
                text=ChaosText.rate_limited(rate_limit["retry_after_seconds"]),
            )
            return
        await query.answer("Preparing media.")
        one_time_payment_id = None
        if result_kind == "one_time":
            one_time_payment_id = self._claim_one_time_payment_for_session(
                session_token,
                user_id=query.from_user.id,
            )
            if one_time_payment_id is None:
                self._inline_delivery_session_tokens.discard(session_token)
                self.state_store.mark_inline_session_status(session_token, "failed")
                await self._safe_edit_inline_text(
                    context,
                    inline_message_id=query.inline_message_id,
                    text=ChaosText.inline_delivery_failed(),
                )
                return
        self._schedule_inline_delivery(
            context,
            session_token=session_token,
            one_time_payment_id=one_time_payment_id,
        )

    async def _answer_paid_inline_options(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        query: Any,
        parsed_link: ParsedRequestLink,
    ) -> None:
        runtime = self.state_store.get_inline_runtime_settings()
        session_token = generate_session_token()
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.INLINE_SESSION_TTL_SECONDS
        )
        subscription_payload = build_subscription_payload(
            user_id=query.from_user.id, session_token=session_token
        )
        try:
            subscription_invoice_link = await context.bot.create_invoice_link(
                title="Inline Mode",
                description="Monthly access to send downloaded media into any chat.",
                payload=subscription_payload,
                provider_token="",
                currency="XTR",
                prices=[
                    LabeledPrice("Inline Mode Monthly", runtime["subscription_stars"])
                ],
                subscription_period=settings.INLINE_SUBSCRIPTION_PERIOD_SECONDS,
            )
        except TelegramError:
            logger.exception("Failed to create inline subscription invoice link")
            result = InlineQueryResultArticle(
                id="inline-payment-unavailable",
                title="Inline payments are temporarily unavailable",
                input_message_content=InputTextMessageContent(
                    ChaosText.inline_payment_unavailable()
                ),
            )
            await query.answer([result], cache_time=0, is_personal=True)
            return

        self.state_store.create_inline_session(
            session_token=session_token,
            user_id=query.from_user.id,
            original_url=parsed_link.original_url,
            normalized_url=parsed_link.normalized_url,
            provider=parsed_link.provider,
            provider_label=parsed_link.provider_label,
            expires_at=expires_at,
        )
        results = [
            InlineQueryResultArticle(
                id=f"sub:{session_token}",
                title=f"Subscribe for {runtime['subscription_stars']} Stars/month",
                input_message_content=InputTextMessageContent(
                    "Open the invoice to activate inline mode, then run this inline query again."
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Pay with Stars", url=subscription_invoice_link
                            )
                        ]
                    ]
                ),
            ),
        ]
        if runtime["one_time_enabled"]:
            results.append(
                InlineQueryResultArticle(
                    id=f"once:{session_token}",
                    title=f"Pay {runtime['one_time_stars']} Stars for this link",
                    input_message_content=InputInvoiceMessageContent(
                        title="One Inline Download",
                        description="One-time access for this inline media link.",
                        payload=build_one_time_payload(
                            user_id=query.from_user.id, session_token=session_token
                        ),
                        provider_token="",
                        currency="XTR",
                        prices=[
                            LabeledPrice(
                                "One Inline Download", runtime["one_time_stars"]
                            )
                        ],
                    ),
                )
            )
        await query.answer(results, cache_time=0, is_personal=True)

    async def pre_checkout_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Approve Telegram Stars inline invoices only when payload and amount match runtime settings."""

        query = getattr(update, "pre_checkout_query", None)
        if not query or not query.from_user:
            return
        payload = parse_inline_payment_payload(query.invoice_payload)
        runtime = self.state_store.get_inline_runtime_settings()
        approved = False
        if (
            payload is not None
            and query.currency == "XTR"
            and payload.user_id == query.from_user.id
        ):
            if payload.kind == "subscription":
                approved = query.total_amount == runtime["subscription_stars"]
            elif (
                payload.kind == "one_time"
                and runtime["one_time_enabled"]
                and query.total_amount == runtime["one_time_stars"]
            ):
                session = self.state_store.get_inline_session(
                    payload.session_token,
                    user_id=payload.user_id,
                )
                approved = session is not None and not self._inline_session_is_expired(
                    session
                )
        if approved:
            await query.answer(ok=True)
            return
        await query.answer(
            ok=False, error_message="Inline payment details are no longer valid."
        )

    async def successful_payment_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Record successful Stars payments and deliver or refund linked inline sessions."""

        if not update.message or not update.effective_user:
            return
        payment = getattr(update.message, "successful_payment", None)
        if payment is None:
            return
        payload = parse_inline_payment_payload(payment.invoice_payload)
        if (
            payload is None
            or payload.user_id != update.effective_user.id
            or payment.currency != "XTR"
        ):
            return

        if payload.kind == "subscription":
            self.state_store.record_inline_subscription(
                user_id=payload.user_id,
                expires_at=self._subscription_expires_at(payment),
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                provider_payment_charge_id=getattr(
                    payment, "provider_payment_charge_id", ""
                )
                or "",
                total_amount=payment.total_amount,
                started_at=datetime.now(timezone.utc),
            )
            return

        if payload.kind != "one_time":
            return
        existing_payment = self.state_store.get_inline_one_time_payment_by_charge_id(
            payment.telegram_payment_charge_id
        )
        if existing_payment is not None:
            return
        session = self.state_store.get_inline_session(
            payload.session_token, user_id=payload.user_id
        )
        payment_id = self.state_store.record_inline_one_time_payment(
            user_id=payload.user_id,
            session_token=payload.session_token,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            total_amount=payment.total_amount,
            provider=str(session["provider"]) if session else None,
            normalized_url=str(session["normalized_url"]) if session else None,
        )
        if session is None or self._inline_session_is_expired(session):
            await self._refund_one_time_payment(
                context,
                payment_id=payment_id,
                user_id=payload.user_id,
                reason="inline_session_expired",
            )
            return
        return

    @staticmethod
    def _subscription_expires_at(payment: Any) -> datetime:
        return subscription_expires_at(payment)

    @staticmethod
    def _parse_chosen_inline_session_token(
        result_id: str,
    ) -> tuple[str | None, str | None]:
        return parse_chosen_inline_session_token(result_id)

    @staticmethod
    def _inline_session_is_expired(session: dict[str, Any]) -> bool:
        return inline_session_is_expired(session)

    def _claim_inline_delivery_session(
        self,
        session_token: str,
        *,
        user_id: int,
        inline_message_id: str,
    ) -> str:
        session = self.state_store.get_inline_session(session_token, user_id=user_id)
        if session is None or self._inline_session_is_expired(session):
            return "expired"
        if (
            session.get("inline_message_id")
            or session.get("status") != "created"
            or session_token in self._inline_delivery_session_tokens
        ):
            return "duplicate"
        self._inline_delivery_session_tokens.add(session_token)
        self.state_store.attach_inline_message(
            session_token, inline_message_id=inline_message_id
        )
        self.state_store.mark_inline_session_status(session_token, "delivering")
        return "claimed"

    def _claim_one_time_payment_for_session(
        self, session_token: str, *, user_id: int
    ) -> str | None:
        session = self.state_store.get_inline_session(session_token, user_id=user_id)
        if session is None:
            return None
        payment = self.state_store.get_available_inline_one_time_payment(
            user_id=user_id,
            provider=str(session["provider"]),
            normalized_url=str(session["normalized_url"]),
        )
        if payment is None:
            return None
        payment_id = str(payment["payment_id"])
        request_id = f"inline:{session_token}"
        if not self.state_store.claim_inline_one_time_payment(
            payment_id, request_id=request_id
        ):
            return None
        return payment_id

    def _paid_or_free_inline_access_kind_for_user(self, user_id: int) -> str | None:
        if not settings.INLINE_SUBSCRIPTION_REQUIRED:
            return "free"
        if self.state_store.is_inline_whitelisted(user_id):
            return "whitelist"
        if self.state_store.has_active_inline_subscription(user_id):
            return "subscription"
        return None

    def _consume_user_rate_limit(self, user_id: int, *, source: str) -> dict[str, Any]:
        return self.state_store.check_user_rate_limit(
            user_id,
            limit=settings.USER_RATE_LIMIT_REQUESTS,
            window_seconds=settings.USER_RATE_LIMIT_WINDOW_SECONDS,
            source=source,
        )

    def _schedule_inline_delivery(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        session_token: str,
        one_time_payment_id: str | None,
    ) -> None:
        task = asyncio.create_task(
            self._deliver_inline_session(
                context,
                session_token=session_token,
                one_time_payment_id=one_time_payment_id,
            )
        )
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(
                lambda _task, token=session_token: self._inline_delivery_session_tokens.discard(
                    token
                )
            )

    async def _deliver_inline_session(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        session_token: str,
        one_time_payment_id: str | None,
    ) -> None:
        """Download, cache, and edit the chosen inline placeholder into media."""

        inline_message_id = None
        failure_stage = "preflight"
        try:
            session = self.state_store.get_inline_session(session_token)
            if session is None or not session.get("inline_message_id"):
                return
            inline_message_id = session["inline_message_id"]
            if settings.INLINE_STORAGE_CHAT_ID is None:
                self._mark_inline_session_failed_and_record_access(
                    session_token,
                    failure_class="inline_storage_missing",
                    failure_stage="preflight",
                    error_class=None,
                )
                if one_time_payment_id:
                    await self._refund_one_time_payment(
                        context,
                        payment_id=one_time_payment_id,
                        user_id=int(session["user_id"]),
                        reason="inline_storage_missing",
                    )
                await self._safe_edit_inline_text(
                    context,
                    inline_message_id=inline_message_id,
                    text=ChaosText.inline_storage_missing(),
                )
                return

            cache_key = f"{session['provider']}:{session['normalized_url']}"
            cached = self.state_store.get_inline_cached_media(cache_key)
            if cached:
                media_item = cached["media_items"][0]
            else:
                parsed_link = ParsedRequestLink(
                    original_url=session["original_url"],
                    normalized_url=session["normalized_url"],
                    provider=session["provider"],
                    provider_label=session["provider_label"],
                )
                output_dir = settings.CACHE_DIR / "inline" / session_token
                try:
                    async with self.job_manager.bounded_execution(
                        chat_id=int(session["user_id"]),
                        user_id=int(session["user_id"]),
                        provider=parsed_link.provider,
                        provider_label=parsed_link.provider_label,
                    ):
                        output_dir.mkdir(parents=True, exist_ok=True)
                        failure_stage = "download"
                        video_info = await VideoDownloader().download_video(
                            parsed_link.original_url, output_dir
                        )
                        failure_stage = "storage_upload"
                        inline_item = await upload_first_media_to_storage(
                            context.bot,
                            storage_chat_id=settings.INLINE_STORAGE_CHAT_ID,
                            video_info=video_info,
                        )
                finally:
                    shutil.rmtree(output_dir, ignore_errors=True)
                media_item = {
                    "media_type": inline_item.media_type,
                    "file_id": inline_item.file_id,
                    "caption": inline_item.caption,
                    "duration": inline_item.duration,
                    "width": inline_item.width,
                    "height": inline_item.height,
                }
                self.state_store.save_inline_cached_media(
                    cache_key=cache_key,
                    provider=parsed_link.provider,
                    normalized_url=parsed_link.normalized_url,
                    media_items=[media_item],
                )

            failure_stage = "inline_edit"
            input_media = build_inline_input_media(InlineCachedMediaItem(**media_item))
            await context.bot.edit_message_media(
                inline_message_id=inline_message_id, media=input_media
            )
            self.state_store.mark_inline_session_status(session_token, "delivered")
            self._record_successful_inline_access(session)
            if one_time_payment_id:
                self.state_store.mark_inline_one_time_payment_delivered(
                    one_time_payment_id,
                    request_id=f"inline:{session_token}",
                )
        except Exception as exc:
            logger.exception("Inline delivery failed for session %s", session_token)
            failed_session = self._mark_inline_session_failed_and_record_access(
                session_token,
                failure_class=self._classify_inline_delivery_failure(
                    exc,
                    failure_stage=failure_stage,
                ),
                failure_stage=failure_stage,
                error_class=exc.__class__.__name__,
            )
            if one_time_payment_id:
                user_id = int(failed_session["user_id"]) if failed_session else 0
                await self._refund_one_time_payment(
                    context,
                    payment_id=one_time_payment_id,
                    user_id=user_id,
                    reason="download_failed",
                )
            if inline_message_id is not None:
                await self._safe_edit_inline_text(
                    context,
                    inline_message_id=inline_message_id,
                    text=ChaosText.inline_delivery_failed(),
                )
        finally:
            self._inline_delivery_session_tokens.discard(session_token)

    def _mark_inline_session_failed_and_record_access(
        self,
        session_token: str,
        *,
        failure_class: str,
        failure_stage: str,
        error_class: str | None,
    ) -> dict[str, Any] | None:
        self.state_store.mark_inline_session_failed(
            session_token,
            failure_class=failure_class,
            failure_stage=failure_stage,
            error_class=error_class,
        )
        failed_session = self.state_store.get_inline_session(session_token)
        if failed_session is not None:
            self._record_failed_inline_access(failed_session)
        return failed_session

    @staticmethod
    def _classify_inline_delivery_failure(
        error: Exception, *, failure_stage: str
    ) -> str:
        if failure_stage == "download":
            return "download_failed"
        if failure_stage == "preflight":
            return "inline_delivery_failed"
        if failure_stage in {"storage_upload", "inline_edit"} and isinstance(
            error, TelegramError
        ):
            return classify_telegram_delivery_error(error)
        return "inline_delivery_failed"

    def _record_successful_inline_access(self, session: dict[str, Any]) -> None:
        record_successful_inline_access(self.state_store, session)

    def _record_failed_inline_access(self, session: dict[str, Any]) -> None:
        record_failed_inline_access(self.state_store, session)

    async def _evaluate_expired_inline_subscription_refunds(
        self,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        for (
            subscription
        ) in self.state_store.list_expired_unchecked_inline_subscriptions():
            user_id = int(subscription["user_id"])
            try:
                started_at = datetime.fromisoformat(str(subscription["started_at"]))
                expires_at = datetime.fromisoformat(str(subscription["expires_at"]))
            except ValueError:
                self.state_store.mark_inline_subscription_auto_refund_failed(
                    user_id,
                    reason="malformed_subscription_period",
                )
                continue
            stats = self.state_store.get_subscription_delivery_stats(
                user_id=user_id,
                started_at=started_at,
                expires_at=expires_at,
            )
            reason = f"failure_rate:{stats['failure_rate']:.2f}"
            if (
                stats["attempts"] > 0
                and stats["failure_rate"]
                >= settings.INLINE_SUBSCRIPTION_AUTO_REFUND_FAILURE_THRESHOLD
            ):
                try:
                    await context.bot.refund_star_payment(
                        user_id=user_id,
                        telegram_payment_charge_id=str(
                            subscription["telegram_payment_charge_id"]
                        ),
                    )
                except TelegramError as exc:
                    self.state_store.mark_inline_subscription_auto_refund_failed(
                        user_id,
                        reason=f"{reason}:{exc.__class__.__name__}",
                    )
                else:
                    self.state_store.mark_inline_subscription_auto_refunded(
                        user_id, reason=reason
                    )
                continue
            self.state_store.mark_inline_subscription_refund_checked(
                user_id, reason=reason
            )

    async def _refund_one_time_payment(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        payment_id: str,
        user_id: int,
        reason: str,
    ) -> None:
        payment = self.state_store.get_inline_one_time_payment(payment_id)
        if not payment or payment["status"] != "paid":
            return
        try:
            await context.bot.refund_star_payment(
                user_id=user_id,
                telegram_payment_charge_id=payment["telegram_payment_charge_id"],
            )
        except TelegramError as exc:
            self.state_store.mark_inline_one_time_payment_refund_failed(
                payment_id,
                reason=f"{reason}:{exc.__class__.__name__}",
            )
            return
        self.state_store.mark_inline_one_time_payment_refunded(
            payment_id, reason=reason
        )

    @staticmethod
    async def _safe_edit_inline_text(
        context: ContextTypes.DEFAULT_TYPE,
        *,
        inline_message_id: str,
        text: str,
    ) -> None:
        try:
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id, text=text
            )
        except Exception:
            logger.debug("Failed to edit inline placeholder text", exc_info=True)

    async def _await_request(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: RequestContext,
        job: SharedJob,
    ) -> None:
        """Wait for a shared job result and deliver it to one requester."""
        try:
            if not job.result_future:
                raise DownloadError("Job result future was not initialized")
            video_info = await job.result_future
            while True:
                if self.job_manager.is_delivery_request(
                    job, request_context.request_id
                ):
                    try:
                        delivery_started_at = time.perf_counter()
                        await self._send_media(context, request_context, video_info)
                        self.state_store.record_delivery_metrics(
                            job.job_id,
                            delivery_duration_ms=self._elapsed_ms(delivery_started_at),
                        )
                    except Exception as error:
                        self.state_store.record_delivery_metrics(
                            job.job_id,
                            delivery_duration_ms=self._elapsed_ms(delivery_started_at),
                        )
                        self.state_store.update_job_status(
                            job.job_id,
                            job.state,
                            error.__class__.__name__,
                        )
                        handed_off = self.job_manager.mark_delivery_failed(
                            job,
                            request_context.request_id,
                            error,
                        )
                        if handed_off:
                            logger.warning(
                                "Delivery handoff triggered after Telegram send failure",
                                extra={
                                    "request_id": request_context.request_id,
                                    "job_id": job.job_id,
                                    "chat_id": request_context.chat_id,
                                },
                            )
                            continue
                        raise
                    self.job_manager.mark_delivery_completed(job)
                    break
                delivered = await self.job_manager.wait_for_delivery(job)
                if delivered:
                    break
                if job.delivery_request_id is not None:
                    continue
                if job.last_delivery_error is not None:
                    raise job.last_delivery_error
                raise RuntimeError("Shared delivery finished without a result")
            await self._delete_status_message(request_context.status_message)
            self.job_manager.mark_request_completed(
                request_context.request_id,
                cache_hit=video_info.from_cache,
            )
            if (
                not video_info.from_cache
                and not settings.RESULT_CACHE_ENABLED
                and len(job.requesters) == 1
            ):
                self._cleanup_files([item.file_path for item in video_info.media_items])
        except asyncio.CancelledError:
            self.job_manager.mark_request_failed(
                request_context.request_id, status="cancelled"
            )
            raise
        except VideoDownloadError as error:
            error_message = self._build_error_message(
                error,
                chaos_enabled=request_context.chaos_enabled,
                language_code=request_context.language_code,
            )
            logger.error(
                "Download error for %s: %s", request_context.original_url, error
            )
            if self.job_manager.is_delivery_request(job, request_context.request_id):
                self.job_manager.mark_delivery_failed(
                    job, request_context.request_id, error
                )
            await self._safe_edit_text(request_context.status_message, error_message)
            self.job_manager.mark_request_failed(
                request_context.request_id, status="failed"
            )
        except Exception as error:
            logger.exception(
                "Unexpected error while delivering %s", request_context.original_url
            )
            if self.job_manager.is_delivery_request(job, request_context.request_id):
                self.job_manager.mark_delivery_failed(
                    job, request_context.request_id, error
                )
            await self._safe_edit_text(
                request_context.status_message,
                ChaosText.unexpected_error(request_context.language_code),
            )
            self.job_manager.mark_request_failed(
                request_context.request_id, status="failed"
            )

    def _build_job_executor(
        self,
        chat_id: int,
        parsed_link: ParsedRequestLink,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        """Create the underlying shared job executor closure."""

        async def _execute(job: SharedJob) -> VideoInfo:
            cached = None
            if settings.RESULT_CACHE_ENABLED:
                cached = self.state_store.get_cached_result(
                    chat_id, parsed_link.normalized_url
                )
            if cached:
                self.state_store.record_cache_hit(job.job_id)
                return self._video_info_from_cache(cached)

            downloader = VideoDownloader()
            cache_segment = (
                parsed_link.normalized_url.replace("https://", "")
                .replace("http://", "")
                .replace("/", "_")
            )
            output_dir = (
                settings.CACHE_DIR / parsed_link.provider / cache_segment
                if settings.RESULT_CACHE_ENABLED
                else settings.TEMP_DIR / parsed_link.provider
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            download_started_at = time.perf_counter()
            try:
                video_info = await downloader.download_video(
                    parsed_link.original_url, output_dir
                )
            except Exception as error:
                self._record_provider_metrics(
                    job.job_id,
                    getattr(downloader, "last_provider_metrics", None),
                    download_duration_ms=self._elapsed_ms(download_started_at),
                    failure_class=error.__class__.__name__,
                )
                await self._notify_owner_about_low_account_pool(
                    context,
                    getattr(downloader, "last_account_health_event", None),
                )
                raise
            self._record_provider_metrics(
                job.job_id,
                getattr(downloader, "last_provider_metrics", None),
                download_duration_ms=self._elapsed_ms(download_started_at),
            )
            if settings.RESULT_CACHE_ENABLED:
                self.state_store.save_cached_result(
                    chat_id=chat_id,
                    normalized_url=parsed_link.normalized_url,
                    provider=parsed_link.provider,
                    title=video_info.title,
                    media_items=[
                        {
                            "file_path": str(item.file_path),
                            "media_type": item.media_type,
                            "caption": item.caption,
                            "duration": item.duration,
                            "width": item.width,
                            "height": item.height,
                        }
                        for item in video_info.media_items
                    ],
                    ttl_seconds=settings.RECENT_RESULT_TTL_SECONDS,
                )
            await self._notify_owner_about_low_account_pool(
                context,
                getattr(downloader, "last_account_health_event", None),
            )
            return video_info

        return _execute

    def _record_provider_metrics(
        self,
        job_id: str,
        provider_metrics: Any | None,
        *,
        download_duration_ms: int,
        failure_class: str | None = None,
    ) -> None:
        record_provider_metrics(
            self.state_store,
            job_id,
            provider_metrics,
            download_duration_ms=download_duration_ms,
            failure_class=failure_class,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _format_performance_summary(performance: dict) -> str:
        return format_performance_summary(performance)

    def _build_admin_performance_summary(
        self,
        *,
        chat_id: int | None,
        duplicate_joins: int,
        recent_failures: list[tuple[str, str, str, str]],
    ) -> dict[str, Any]:
        return build_admin_performance_summary(
            self.state_store,
            chat_id=chat_id,
            duplicate_joins=duplicate_joins,
            recent_failures=recent_failures,
        )

    async def _on_job_state_change(self, job: SharedJob) -> None:
        """Propagate shared job state changes to per-request status messages."""
        if job.state not in {"running", "failed", "cancelled"}:
            return
        for request_id, request_context in list(self.request_contexts.items()):
            if request_id not in job.requesters:
                continue
            if job.state == "running":
                if request_context.quiet_mode or request_context.joined_existing:
                    continue
                text = ChaosText.running(
                    TextContext(
                        provider_label=request_context.provider_label,
                        chaos_enabled=request_context.chaos_enabled,
                        language_code=request_context.language_code,
                    )
                )
                await self._edit_status_message(request_context.status_message, text)
            elif job.state == "cancelled":
                text = ChaosText.cancelled(
                    request_context.chaos_enabled, request_context.language_code
                )
                await self._safe_edit_text(request_context.status_message, text)
            else:
                text = ChaosText.failed(
                    request_context.chaos_enabled, request_context.language_code
                )
                await self._safe_edit_text(request_context.status_message, text)

    async def _global_error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle unhandled Telegram polling/runtime exceptions."""
        error = context.error
        if isinstance(error, NetworkError):
            logger.warning(
                "Transient Telegram network error",
                extra={"failure_class": "telegram_network", "error": str(error)},
            )
            return
        if isinstance(error, TelegramError):
            logger.error(
                "Telegram API error",
                extra={"failure_class": "telegram_api", "error": str(error)},
            )
            return

        logger.exception(
            "Unhandled Telegram runtime error",
            extra={"failure_class": "telegram_unhandled", "error": str(error)},
        )

    async def _send_media(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: RequestContext,
        video_info: VideoInfo,
    ) -> None:
        """Send one media item or a multi-item album based on downloader result."""
        await self.media_sender.send_media(context, request_context, video_info)

    @staticmethod
    def _cleanup_files(files: list[Path]) -> None:
        """Delete downloaded files safely."""
        TelegramMediaSender.cleanup_files(files)

    def _purge_expired_cache(self) -> None:
        """Delete expired cache files and state rows."""
        purge_expired_cache_files(
            self.state_store,
            result_cache_enabled=settings.RESULT_CACHE_ENABLED,
        )

    @staticmethod
    def _video_info_from_cache(cached: CachedMediaEntry) -> VideoInfo:
        return video_info_from_cache(cached)

    @staticmethod
    def _build_submission_message(
        provider_label: str,
        *,
        queue_position: int,
        joined_existing: bool = False,
        chaos_enabled: bool = False,
        language_code: str = "ru",
    ) -> str:
        return build_submission_message(
            provider_label,
            queue_position=queue_position,
            joined_existing=joined_existing,
            chaos_enabled=chaos_enabled,
            language_code=language_code,
        )

    @classmethod
    def _build_caption_text(cls, title: str) -> str:
        """Build a Telegram-safe media caption."""
        return TelegramMediaSender.build_caption_text(title)

    @staticmethod
    def _telegram_video_kwargs(media_item: MediaItem) -> dict[str, object]:
        """Build optional Telegram video metadata from a media item."""
        return TelegramMediaSender.telegram_video_kwargs(media_item)

    @staticmethod
    def _build_error_message(
        error: Exception,
        *,
        chaos_enabled: bool = False,
        language_code: str = "ru",
    ) -> str:
        return build_error_message(
            error, chaos_enabled=chaos_enabled, language_code=language_code
        )

    @staticmethod
    async def _edit_status_message(message: Message, text: str) -> None:
        """Try to edit a transient status message without creating extra chat noise."""
        await edit_status_message(message, text)

    @staticmethod
    async def _safe_edit_text(message: Message, text: str) -> None:
        """Edit status text, falling back to a new visible reply for important states."""
        await safe_edit_text(message, text)

    @staticmethod
    async def _delete_status_message(message: Message) -> None:
        """Delete a transient status message after successful completion."""
        await delete_status_message(message)

    @staticmethod
    def _user_label(update: Update) -> str:
        return user_label(update)

    @staticmethod
    def _request_user_id(update: Update) -> int | None:
        return request_user_id(update)

    @classmethod
    def _request_user_label(cls, update: Update) -> str:
        return request_user_label(update)

    def _language_for_update(self, update: Update) -> str:
        """Resolve explicit user preference, then Telegram profile language, then English."""
        user = update.effective_user
        if user is None:
            return "en"
        stored_language = self.state_store.get_user_language(user.id)
        if stored_language in {"en", "ru"}:
            return stored_language
        return self._language_from_profile(getattr(user, "language_code", None))

    @staticmethod
    def _language_from_profile(language_code: str | None) -> str:
        return language_from_profile(language_code)

    async def _toggle_group_setting(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        setting_name: str,
        command_name: str,
        label: str,
    ) -> None:
        """Update a boolean group setting after owner validation."""
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        if not await self._require_owner(update):
            return
        desired = self._parse_toggle_arg(context.args[0] if context.args else "")
        if desired is None:
            await update.message.reply_text(ChaosText.setting_usage(command_name))
            return
        result = self.state_store.update_group_settings(
            update.effective_chat.id, **{setting_name: desired}
        )
        await update.message.reply_text(
            ChaosText.setting_updated(label, result[setting_name])
        )

    async def _set_numeric_group_setting(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        setting_name: str,
        command_name: str,
        label: str,
    ) -> None:
        """Update an integer group setting after owner validation."""
        if not update.message or not update.effective_chat:
            return
        if not await self._require_owner(update):
            return
        value = self._parse_positive_int_arg(context.args[0] if context.args else "")
        if value is None:
            await update.message.reply_text(
                ChaosText.numeric_setting_usage(command_name)
            )
            return
        result = self.state_store.update_group_settings(
            update.effective_chat.id, **{setting_name: value}
        )
        if setting_name == "chat_max_concurrent_jobs":
            self.job_manager.update_chat_limits(
                update.effective_chat.id, chat_limit=value
            )
        elif setting_name == "user_max_active_jobs":
            self.job_manager.update_chat_limits(
                update.effective_chat.id, user_limit=value
            )
        await update.message.reply_text(
            ChaosText.numeric_setting_updated(label, result[setting_name])
        )

    @staticmethod
    def _ru_on_off(value: bool, *, feminine: bool = False) -> str:
        if feminine:
            return "включена" if value else "выключена"
        return "включен" if value else "выключен"

    async def _require_owner(self, update: Update) -> bool:
        """Return whether the sender is the configured bot owner."""
        if not update.message or not update.effective_user:
            return False
        if settings.BOT_OWNER_USER_ID is None:
            await update.message.reply_text(ChaosText.owner_unconfigured())
            return False
        if update.effective_user.id != settings.BOT_OWNER_USER_ID:
            await update.message.reply_text(ChaosText.owner_required())
            return False
        return True

    async def _require_chaos_admin(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> bool:
        """Return whether the sender can manage chat-level chaos mode."""
        if not update.message or not update.effective_chat or not update.effective_user:
            return False
        if getattr(update.effective_chat, "type", "") == "private":
            return True
        if (
            settings.BOT_OWNER_USER_ID is not None
            and update.effective_user.id == settings.BOT_OWNER_USER_ID
        ):
            return True
        try:
            member = await context.bot.get_chat_member(
                update.effective_chat.id,
                update.effective_user.id,
            )
        except Exception:
            await update.message.reply_text(ChaosText.admin_required())
            return False
        if getattr(member, "status", "") in {"administrator", "creator"}:
            return True
        await update.message.reply_text(ChaosText.admin_required())
        return False

    @staticmethod
    def _parse_toggle_arg(value: str) -> bool | None:
        return parse_toggle_arg(value)

    @staticmethod
    def _parse_positive_int_arg(value: str) -> int | None:
        return parse_positive_int_arg(value)

    async def _notify_owner_about_low_account_pool(self, context, event) -> None:
        if event is None or not event.should_alert_owner:
            return
        if settings.BOT_OWNER_USER_ID is None:
            logger.warning(
                "Skipping low account pool owner alert: BOT_OWNER_USER_ID is not configured"
            )
            return
        if not getattr(context, "bot", None):
            logger.warning(
                "Skipping low account pool owner alert: Telegram bot context is unavailable"
            )
            return

        text = (
            "Instagram account pool warning:\n"
            f"Usable accounts left: {event.available_accounts} of {event.total_accounts}.\n"
            f"Low-watermark threshold: {event.low_watermark}.\n"
            f"Last removed account: {event.username}.\n"
            f"Reason: {event.reason} after {event.consecutive_failures} sequential failures."
        )
        try:
            await context.bot.send_message(
                chat_id=settings.BOT_OWNER_USER_ID, text=text
            )
        except TelegramError as exc:
            logger.warning("Failed to send low account pool owner alert: %s", exc)

    def _cleanup_request_task(self, request_id: str) -> None:
        self.active_request_tasks.pop(request_id, None)
        self.request_contexts.pop(request_id, None)

    def run(self) -> None:
        """Start the Telegram bot."""
        if not settings.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is not set in environment variables")

        self.application, mode = build_telegram_application(self)
        if mode == "legacy_redirect":
            logger.info("Bot started in legacy redirect mode")
            self.application.run_polling()
            return

        logger.info("Bot started and ready to process messages")
        self.application.run_polling()
