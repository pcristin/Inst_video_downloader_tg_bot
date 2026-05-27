"""Telegram bot service for handling group-friendly media downloads."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import datetime as dtm
import logging
from pathlib import Path
import time
from typing import Any, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputInvoiceMessageContent,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    LabeledPrice,
    Message,
    Update,
)
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config.settings import settings
from .chaos_text import ChaosText, TextContext
from .download_models import ProviderExecutionMetrics
from .inline_access import (
    build_inline_result_id,
    build_one_time_payload,
    build_subscription_payload,
    generate_session_token,
    parse_inline_payment_payload,
    parse_inline_result_id,
    validate_star_amount,
)
from .inline_delivery import (
    InlineCachedMediaItem,
    build_inline_input_media,
    upload_first_media_to_storage,
)
from .job_manager import JobManager, SharedJob
from .request_parser import ParsedRequestLink, RequestParser
from .state_store import CachedMediaEntry, StateStore
from .video_downloader import DownloadError, MediaItem, VideoDownloadError, VideoDownloader, VideoInfo

logger = logging.getLogger(__name__)


@dataclass
class RequestContext:
    """Telegram state for one user request tied to a shared job."""

    request_id: str
    chat_id: int
    user_id: int
    provider_label: str
    normalized_url: str
    original_url: str
    original_message_id: int
    status_message: Message
    quiet_mode: bool
    joined_existing: bool
    chaos_enabled: bool = False


class TelegramBot:
    """Telegram bot for downloading media links."""

    INSTAGRAM_VIDEO_PATTERN = RequestParser.URL_PATTERN
    MAX_MEDIA_CAPTION_LENGTH = 1024
    TELEGRAM_MEDIA_GROUP_LIMIT = 10

    def __init__(self, state_store: StateStore | None = None):
        self.application: Optional[Application] = None
        self.state_store = state_store or StateStore()
        self.job_manager = JobManager(self.state_store)
        self.job_manager.add_state_listener(self._on_job_state_change)
        self.active_request_tasks: dict[str, asyncio.Task[None]] = {}
        self.request_contexts: dict[str, RequestContext] = {}
        self._inline_delivery_session_tokens: set[str] = set()
        self.started_at = time.time()
        self._purge_expired_cache()

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming messages by queueing supported provider links."""
        if not update.message or not update.message.text or not update.effective_chat or not update.effective_user:
            return

        self._purge_expired_cache()
        message_text = update.message.text
        extracted_links = RequestParser.extract_supported_links(
            message_text,
            limit=settings.MAX_LINKS_PER_MESSAGE,
        )
        if not extracted_links:
            if self._message_is_from_owner(update) and await self._whitelist_forwarded_visible_user(update):
                return
            return

        group_settings = self.state_store.ensure_group_settings(update.effective_chat.id)
        raw_url_count = len(RequestParser.URL_PATTERN.findall(message_text))
        if raw_url_count > settings.MAX_LINKS_PER_MESSAGE:
            await update.message.reply_text(
                ChaosText.too_many_links(settings.MAX_LINKS_PER_MESSAGE)
            )

        for parsed_link in extracted_links:
            submission = self.job_manager.submit(
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
                user_label=self._user_label(update),
                provider=parsed_link.provider,
                provider_label=parsed_link.provider_label,
                original_url=parsed_link.original_url,
                normalized_url=parsed_link.normalized_url,
                execute=self._build_job_executor(update.effective_chat.id, parsed_link, context),
                duplicate_suppression=group_settings["duplicate_suppression"],
            )
            status_message = await update.message.reply_text(
                self._build_submission_message(
                    parsed_link.provider_label,
                    queue_position=submission.queue_position,
                    joined_existing=not submission.is_new_job,
                    chaos_enabled=group_settings["chaos_mode_enabled"],
                )
            )
            request_context = RequestContext(
                request_id=submission.request_id,
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
                provider_label=parsed_link.provider_label,
                normalized_url=parsed_link.normalized_url,
                original_url=parsed_link.original_url,
                original_message_id=update.message.message_id,
                status_message=status_message,
                quiet_mode=group_settings["quiet_mode"],
                joined_existing=not submission.is_new_job,
                chaos_enabled=group_settings["chaos_mode_enabled"],
            )
            self.request_contexts[submission.request_id] = request_context
            task = asyncio.create_task(self._await_request(context, request_context, submission.job))
            self.active_request_tasks[submission.request_id] = task
            task.add_done_callback(lambda _task, rid=submission.request_id: self._cleanup_request_task(rid))

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show supported providers and usage help."""
        if not update.message or not update.effective_chat:
            return
        group_settings = self.state_store.ensure_group_settings(update.effective_chat.id)
        await update.message.reply_text(ChaosText.help(group_settings["chaos_mode_enabled"]))

    async def formats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show supported URL shapes."""
        if not update.message:
            return
        await update.message.reply_text(ChaosText.formats())

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show a safe queue and health summary."""
        if not update.message or not update.effective_chat:
            return
        snapshot = self.job_manager.get_snapshot(update.effective_chat.id)
        persisted = self.state_store.get_public_status(update.effective_chat.id)
        group_settings = self.state_store.ensure_group_settings(update.effective_chat.id)
        await update.message.reply_text(
            ChaosText.status(snapshot, persisted, chaos_enabled=group_settings["chaos_mode_enabled"])
        )

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cancel the latest active request from the current user."""
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        request_id = self.job_manager.get_latest_active_request_id(
            update.effective_chat.id,
            update.effective_user.id,
        )
        if not request_id:
            await update.message.reply_text(ChaosText.no_active_request())
            return

        task = self.active_request_tasks.get(request_id)
        if task and not task.done():
            task.cancel()
        job = self.job_manager.cancel_request(request_id)
        request_context = self.request_contexts.get(request_id)
        if request_context:
            await self._safe_edit_text(
                request_context.status_message,
                ChaosText.cancelled(request_context.chaos_enabled),
            )
        if job and update.message:
            await update.message.reply_text(ChaosText.latest_cancelled())

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show lightweight group stats."""
        if not update.message or not update.effective_chat:
            return
        group_settings = self.state_store.ensure_group_settings(update.effective_chat.id)
        if not group_settings["stats_enabled"]:
            await update.message.reply_text(ChaosText.stats_disabled())
            return

        stats = self.state_store.get_group_stats(update.effective_chat.id)
        await update.message.reply_text(
            ChaosText.stats(stats, chaos_enabled=group_settings["chaos_mode_enabled"])
        )

    async def chaos_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle or inspect chat-level chaos mode."""
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        action = (context.args[0] if context.args else "status").strip().lower()
        if action == "status":
            settings_row = self.state_store.ensure_group_settings(update.effective_chat.id)
            await update.message.reply_text(ChaosText.chaos_status(settings_row["chaos_mode_enabled"]))
            return

        desired = self._parse_toggle_arg(action)
        if desired is None:
            await update.message.reply_text(ChaosText.chaos_usage())
            return
        if not await self._require_chaos_admin(update, context):
            return

        result = self.state_store.update_group_settings(
            update.effective_chat.id,
            chaos_mode_enabled=desired,
        )
        await update.message.reply_text(ChaosText.chaos_updated(result["chaos_mode_enabled"]))

    async def quiet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle quiet mode for the current chat. Owner-only."""
        await self._toggle_group_setting(
            update,
            context,
            setting_name="quiet_mode",
            command_name="quiet",
            label="Тихий режим",
        )

    async def dupes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle duplicate suppression for the current chat. Owner-only."""
        await self._toggle_group_setting(
            update,
            context,
            setting_name="duplicate_suppression",
            command_name="dupes",
            label="Защита от повторов",
        )

    async def statsmode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle stats collection visibility for the current chat. Owner-only."""
        await self._toggle_group_setting(
            update,
            context,
            setting_name="stats_enabled",
            command_name="statsmode",
            label="Статистика",
        )

    async def chatlimit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Override per-chat concurrent job limit. Owner-only."""
        await self._set_numeric_group_setting(
            update,
            context,
            setting_name="chat_max_concurrent_jobs",
            command_name="chatlimit",
            label="Лимит чата",
        )

    async def userlimit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Override per-user active job limit for the current chat. Owner-only."""
        await self._set_numeric_group_setting(
            update,
            context,
            setting_name="user_max_active_jobs",
            command_name="userlimit",
            label="Лимит на пользователя",
        )

    async def admin_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show owner-facing operational status for the current chat."""
        if not update.message or not update.effective_chat:
            return
        if not await self._require_owner(update):
            return
        snapshot = self.job_manager.get_snapshot(update.effective_chat.id)
        admin_status = self.state_store.get_admin_status(update.effective_chat.id)
        performance = self._build_admin_performance_summary(
            chat_id=update.effective_chat.id,
            duplicate_joins=self.state_store.get_group_stats(update.effective_chat.id)["duplicate_joins"],
            recent_failures=admin_status["recent_failures"],
        )
        settings_row = admin_status["settings"]
        provider_lines = ", ".join(
            f"{provider}:{status}={count}"
            for provider, status, count in admin_status["provider_job_counts"]
        ) or "нет"
        failure_lines = "\n".join(
            f"  - {provider} | {error_class} | {normalized_url}"
            for provider, normalized_url, error_class, _finished_at in admin_status["recent_failures"]
        ) or "  - нет"
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

    async def admin_global_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        provider_lines = ", ".join(
            f"{provider}:{status}={count}"
            for provider, status, count in admin_status["provider_job_counts"]
        ) or "нет"
        failure_lines = "\n".join(
            f"  - {provider} | {error_class} | {normalized_url}"
            for provider, normalized_url, error_class, _finished_at in admin_status["recent_failures"]
        ) or "  - нет"
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

    async def inline_whitelist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    async def inline_price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        runtime = self.state_store.update_inline_runtime_settings(subscription_stars=stars)
        await update.message.reply_text(ChaosText.inline_subscription_price_updated(runtime["subscription_stars"]))

    async def inline_onetime_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manage optional one-time Stars payments for true-inline mode."""
        if not update.message:
            return
        if not await self._require_owner(update):
            return
        args = list(getattr(context, "args", []) or [])
        action = args[0].lower() if args else ""
        if action == "off" and len(args) == 1:
            runtime = self.state_store.update_inline_runtime_settings(one_time_enabled=False)
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

    async def inline_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Return true inline placeholder results that will be edited into media."""

        query = update.inline_query
        if not query or not settings.INLINE_MODE_ENABLED:
            return
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
        if settings.INLINE_SUBSCRIPTION_REQUIRED and not self.state_store.user_has_inline_access(query.from_user.id):
            await self._answer_paid_inline_options(query, parsed_link)
            return

        session_token = generate_session_token()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.INLINE_SESSION_TTL_SECONDS)
        self.state_store.create_inline_session(
            session_token=session_token,
            user_id=query.from_user.id,
            original_url=parsed_link.original_url,
            normalized_url=parsed_link.normalized_url,
            provider=parsed_link.provider,
            provider_label=parsed_link.provider_label,
            expires_at=expires_at,
        )
        result = InlineQueryResultArticle(
            id=build_inline_result_id(session_token),
            title="Send media here",
            description=parsed_link.provider_label,
            input_message_content=InputTextMessageContent(
                ChaosText.inline_preparing(parsed_link.provider_label)
            ),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Preparing", callback_data=f"inline:{session_token}")]]
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)

    async def chosen_inline_result_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Capture the inline message ID and start delivery."""

        chosen = update.chosen_inline_result
        if not chosen or not chosen.inline_message_id:
            return
        session_token = parse_inline_result_id(chosen.result_id)
        if not session_token:
            return
        claim_status = self._claim_inline_delivery_session(
            session_token,
            user_id=chosen.from_user.id,
            inline_message_id=chosen.inline_message_id,
        )
        if claim_status != "claimed":
            return
        self._schedule_inline_delivery(context, session_token=session_token, one_time_payment_id=None)

    async def inline_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Fallback: user taps the inline keyboard if chosen feedback did not start delivery."""

        query = update.callback_query
        if not query or not query.inline_message_id or not query.data or not query.from_user:
            return
        session_token = query.data.removeprefix("inline:")
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
        await query.answer("Preparing media.")
        self._schedule_inline_delivery(context, session_token=session_token, one_time_payment_id=None)

    async def _answer_paid_inline_options(self, query: Any, parsed_link: ParsedRequestLink) -> None:
        runtime = self.state_store.get_inline_runtime_settings()
        session_token = generate_session_token()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.INLINE_SESSION_TTL_SECONDS)
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
                input_message_content=InputInvoiceMessageContent(
                    title="Inline Mode",
                    description="Monthly access to send downloaded media into any chat.",
                    payload=build_subscription_payload(user_id=query.from_user.id, session_token=session_token),
                    provider_token="",
                    currency="XTR",
                    prices=[LabeledPrice("Inline Mode Monthly", runtime["subscription_stars"])],
                    api_kwargs={
                        "subscription_period": settings.INLINE_SUBSCRIPTION_PERIOD_SECONDS,
                    },
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
                        payload=build_one_time_payload(user_id=query.from_user.id, session_token=session_token),
                        provider_token="",
                        currency="XTR",
                        prices=[LabeledPrice("One Inline Download", runtime["one_time_stars"])],
                    ),
                )
            )
        await query.answer(results, cache_time=0, is_personal=True)

    async def pre_checkout_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Approve Telegram Stars inline invoices only when payload and amount match runtime settings."""

        query = getattr(update, "pre_checkout_query", None)
        if not query or not query.from_user:
            return
        payload = parse_inline_payment_payload(query.invoice_payload)
        runtime = self.state_store.get_inline_runtime_settings()
        approved = (
            payload is not None
            and query.currency == "XTR"
            and payload.user_id == query.from_user.id
            and (
                (
                    payload.kind == "subscription"
                    and query.total_amount == runtime["subscription_stars"]
                )
                or (
                    payload.kind == "one_time"
                    and runtime["one_time_enabled"]
                    and query.total_amount == runtime["one_time_stars"]
                )
            )
        )
        if approved:
            await query.answer(ok=True)
            return
        await query.answer(ok=False, error_message="Inline payment details are no longer valid.")

    async def successful_payment_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Record successful Stars payments and deliver or refund linked inline sessions."""

        if not update.message or not update.effective_user:
            return
        payment = getattr(update.message, "successful_payment", None)
        if payment is None:
            return
        payload = parse_inline_payment_payload(payment.invoice_payload)
        if payload is None or payload.user_id != update.effective_user.id or payment.currency != "XTR":
            return

        runtime = self.state_store.get_inline_runtime_settings()
        if payload.kind == "subscription":
            if payment.total_amount != runtime["subscription_stars"]:
                return
            self.state_store.record_inline_subscription(
                user_id=payload.user_id,
                expires_at=self._subscription_expires_at(payment),
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                provider_payment_charge_id=getattr(payment, "provider_payment_charge_id", "") or "",
                total_amount=payment.total_amount,
            )
            session = self.state_store.get_inline_session(payload.session_token, user_id=payload.user_id)
            if session and session.get("inline_message_id") and not self._inline_session_is_expired(session):
                await self._deliver_inline_session(
                    context,
                    session_token=payload.session_token,
                    one_time_payment_id=None,
                )
            return

        if (
            payload.kind != "one_time"
            or not runtime["one_time_enabled"]
            or payment.total_amount != runtime["one_time_stars"]
        ):
            return
        payment_id = self.state_store.record_inline_one_time_payment(
            user_id=payload.user_id,
            session_token=payload.session_token,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            total_amount=payment.total_amount,
        )
        session = self.state_store.get_inline_session(payload.session_token, user_id=payload.user_id)
        if session is None or self._inline_session_is_expired(session):
            await self._refund_one_time_payment(
                context,
                payment_id=payment_id,
                user_id=payload.user_id,
                reason="inline_session_expired",
            )
            return
        await self._deliver_inline_session(
            context,
            session_token=payload.session_token,
            one_time_payment_id=payment_id,
        )

    @staticmethod
    def _subscription_expires_at(payment: Any) -> datetime:
        expires_at = getattr(payment, "subscription_expiration_date", None)
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                return expires_at.replace(tzinfo=timezone.utc)
            return expires_at.astimezone(timezone.utc)
        if isinstance(expires_at, (int, float)):
            return datetime.fromtimestamp(expires_at, tz=timezone.utc)
        return datetime.now(timezone.utc) + timedelta(seconds=settings.INLINE_SUBSCRIPTION_PERIOD_SECONDS)

    @staticmethod
    def _inline_session_is_expired(session: dict[str, Any]) -> bool:
        expires_at_raw = session.get("expires_at")
        if not expires_at_raw:
            return True
        try:
            expires_at = datetime.fromisoformat(str(expires_at_raw))
        except ValueError:
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)

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
        self.state_store.attach_inline_message(session_token, inline_message_id=inline_message_id)
        self.state_store.mark_inline_session_status(session_token, "delivering")
        return "claimed"

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
            task.add_done_callback(lambda _task, token=session_token: self._inline_delivery_session_tokens.discard(token))

    async def _deliver_inline_session(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        session_token: str,
        one_time_payment_id: str | None,
    ) -> None:
        """Download, cache, and edit the chosen inline placeholder into media."""

        inline_message_id = None
        try:
            session = self.state_store.get_inline_session(session_token)
            if session is None or not session.get("inline_message_id"):
                return
            inline_message_id = session["inline_message_id"]
            if settings.INLINE_STORAGE_CHAT_ID is None:
                self.state_store.mark_inline_session_status(session_token, "failed")
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
                output_dir.mkdir(parents=True, exist_ok=True)
                video_info = await VideoDownloader().download_video(parsed_link.original_url, output_dir)
                inline_item = await upload_first_media_to_storage(
                    context.bot,
                    storage_chat_id=settings.INLINE_STORAGE_CHAT_ID,
                    video_info=video_info,
                )
                media_item = {
                    "media_type": inline_item.media_type,
                    "file_id": inline_item.file_id,
                    "caption": inline_item.caption,
                }
                self.state_store.save_inline_cached_media(
                    cache_key=cache_key,
                    provider=parsed_link.provider,
                    normalized_url=parsed_link.normalized_url,
                    media_items=[media_item],
                )

            input_media = build_inline_input_media(InlineCachedMediaItem(**media_item))
            await context.bot.edit_message_media(inline_message_id=inline_message_id, media=input_media)
            self.state_store.mark_inline_session_status(session_token, "delivered")
            if one_time_payment_id:
                self.state_store.mark_inline_one_time_payment_delivered(
                    one_time_payment_id,
                    request_id=f"inline:{session_token}",
                )
        except Exception:
            logger.exception("Inline delivery failed for session %s", session_token)
            self.state_store.mark_inline_session_status(session_token, "failed")
            if one_time_payment_id:
                session = self.state_store.get_inline_session(session_token)
                user_id = int(session["user_id"]) if session else 0
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
        self.state_store.mark_inline_one_time_payment_refunded(payment_id, reason=reason)

    @staticmethod
    async def _safe_edit_inline_text(
        context: ContextTypes.DEFAULT_TYPE,
        *,
        inline_message_id: str,
        text: str,
    ) -> None:
        try:
            await context.bot.edit_message_text(inline_message_id=inline_message_id, text=text)
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
                if self.job_manager.is_delivery_request(job, request_context.request_id):
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
            self.job_manager.mark_request_failed(request_context.request_id, status="cancelled")
            raise
        except VideoDownloadError as error:
            error_message = self._build_error_message(
                error,
                chaos_enabled=request_context.chaos_enabled,
            )
            logger.error("Download error for %s: %s", request_context.original_url, error)
            if self.job_manager.is_delivery_request(job, request_context.request_id):
                self.job_manager.mark_delivery_failed(job, request_context.request_id, error)
            await self._safe_edit_text(request_context.status_message, error_message)
            self.job_manager.mark_request_failed(request_context.request_id, status="failed")
        except Exception as error:
            logger.exception("Unexpected error while delivering %s", request_context.original_url)
            if self.job_manager.is_delivery_request(job, request_context.request_id):
                self.job_manager.mark_delivery_failed(job, request_context.request_id, error)
            await self._safe_edit_text(
                request_context.status_message,
                ChaosText.unexpected_error()
            )
            self.job_manager.mark_request_failed(request_context.request_id, status="failed")

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
                cached = self.state_store.get_cached_result(chat_id, parsed_link.normalized_url)
            if cached:
                self.state_store.record_cache_hit(job.job_id)
                return self._video_info_from_cache(cached)

            downloader = VideoDownloader()
            cache_segment = (
                parsed_link.normalized_url
                .replace("https://", "")
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
                video_info = await downloader.download_video(parsed_link.original_url, output_dir)
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
        provider_metrics: ProviderExecutionMetrics | None,
        *,
        download_duration_ms: int,
        failure_class: str | None = None,
    ) -> None:
        """Persist provider execution metrics without leaking provider internals."""
        metrics = provider_metrics or ProviderExecutionMetrics(provider="unknown")
        effective_failure_class = getattr(metrics, "failure_class", None) or failure_class
        self.state_store.record_download_metrics(
            job_id,
            download_duration_ms=download_duration_ms,
            retry_count=int(getattr(metrics, "retry_count", 0) or 0),
            instagram_fast_status=getattr(metrics, "instagram_fast_status", None),
            instagram_fast_duration_ms=getattr(metrics, "instagram_fast_duration_ms", None),
            instagram_fast_budget_exhausted=bool(
                getattr(metrics, "instagram_fast_budget_exhausted", False)
            ),
            instagram_fast_endpoint_timings_json=getattr(
                metrics, "instagram_fast_endpoint_timings_json", None
            ),
            instagram_fallback_attempted=bool(getattr(metrics, "instagram_fallback_attempted", False)),
            instagram_account_attempts=int(getattr(metrics, "instagram_account_attempts", 0) or 0),
            instagram_account_retries=int(getattr(metrics, "instagram_account_retries", 0) or 0),
            instagram_auth_failures=int(getattr(metrics, "instagram_auth_failures", 0) or 0),
            instagram_success_path=getattr(metrics, "instagram_success_path", None),
            instagram_fallback_path=getattr(metrics, "instagram_fallback_path", None),
            instagram_metadata_reused=bool(
                getattr(metrics, "instagram_metadata_reused", False)
            ),
            failure_class=effective_failure_class,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _format_performance_summary(performance: dict) -> str:
        total_jobs = int(performance.get("total_jobs", 0) or 0)
        cache_hits = int(performance.get("cache_hits", 0) or 0)
        cache_rate = float(performance.get("cache_hit_rate", 0.0) or 0.0) * 100
        duplicate_joins = int(performance.get("duplicate_joins", 0) or 0)
        lines = [
            "Производительность:",
            f"- Окно: последние {total_jobs} задач",
            f"- Кэш: {cache_hits} ({cache_rate:.0f}%)",
            f"- Повторы: {duplicate_joins}",
            f"- Queue wait avg: {int(performance.get('avg_queue_wait_ms', 0) or 0)}мс",
            f"- Telegram delivery avg: {int(performance.get('avg_delivery_ms', 0) or 0)}мс",
        ]

        providers = performance.get("providers", {})
        if providers:
            for provider, provider_summary in sorted(providers.items()):
                lines.append(
                    "- "
                    f"{ChaosText.provider_name(provider)}: "
                    f"{provider_summary.get('jobs', 0)} задач, "
                    f"queue avg {provider_summary.get('avg_queue_wait_ms', 0)}мс, "
                    f"download avg {provider_summary.get('avg_download_ms', 0)}мс, "
                    f"delivery avg {provider_summary.get('avg_delivery_ms', 0)}мс"
                )
        else:
            lines.append("- Провайдеры: нет данных")

        instagram = performance.get("instagram", {})
        lines.append(
            "- Instagram fast-path: "
            f"ошибок {int(instagram.get('fast_failed', 0) or 0)}, "
            f"fallback {int(instagram.get('fallback_count', 0) or 0)}, "
            f"retries {int(instagram.get('account_retries', 0) or 0)}, "
            f"auth {int(instagram.get('auth_failures', 0) or 0)}"
        )

        failure_classes = sorted(
            {
                str(error_class)
                for error_class in performance.get("failure_classes", [])
                if error_class and error_class != "unknown"
            }
        )
        lines.append(
            "- Классы ошибок: "
            + (", ".join(failure_classes) if failure_classes else "нет")
        )
        return "\n".join(lines)

    def _build_admin_performance_summary(
        self,
        *,
        chat_id: int | None,
        duplicate_joins: int,
        recent_failures: list[tuple[str, str, str, str]],
    ) -> dict[str, Any]:
        performance = self.state_store.get_performance_summary(chat_id, limit=50)
        performance["duplicate_joins"] = duplicate_joins
        performance["failure_classes"] = list(performance.get("failure_classes", [])) + [
            error_class
            for _provider, _normalized_url, error_class, _finished_at in recent_failures
        ]
        return performance

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
                    )
                )
                await self._edit_status_message(request_context.status_message, text)
            elif job.state == "cancelled":
                text = ChaosText.cancelled(request_context.chaos_enabled)
                await self._safe_edit_text(request_context.status_message, text)
            else:
                text = ChaosText.failed(request_context.chaos_enabled)
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

    @staticmethod
    def _validate_media_files(files: List[Path]) -> None:
        """Validate that all files exist and are non-empty."""
        for file_path in files:
            if not file_path.exists():
                raise VideoDownloadError(f"Media file not found at {file_path}")
            if file_path.stat().st_size == 0:
                raise VideoDownloadError(f"Media file is empty: {file_path}")

    async def _send_media(
        self, context: ContextTypes.DEFAULT_TYPE, request_context: RequestContext, video_info: VideoInfo
    ) -> None:
        """Send one media item or a multi-item album based on downloader result."""
        media_items = video_info.media_items
        self._validate_media_files([item.file_path for item in media_items])
        caption_text = self._build_caption_text(video_info.title)

        telegram_file_ids: list[str | None] = []

        if len(media_items) == 1:
            media_item = media_items[0]
            telegram_file_ids.append(
                await self._send_single_media_item(
                    context,
                    request_context,
                    media_item,
                    caption_text,
                )
            )
            self._persist_telegram_file_ids(request_context, telegram_file_ids)
            return

        caption_available = caption_text
        for offset in range(0, len(media_items), self.TELEGRAM_MEDIA_GROUP_LIMIT):
            chunk = media_items[offset : offset + self.TELEGRAM_MEDIA_GROUP_LIMIT]
            chunk_caption = caption_available if offset == 0 else None
            if len(chunk) == 1:
                telegram_file_ids.append(
                    await self._send_single_media_item(
                        context,
                        request_context,
                        chunk[0],
                        chunk_caption,
                    )
                )
            else:
                telegram_file_ids.extend(
                    await self._send_media_group_chunk(
                        context,
                        request_context,
                        chunk,
                        chunk_caption,
                    )
                )
        self._persist_telegram_file_ids(request_context, telegram_file_ids)

    async def _send_single_media_item(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: RequestContext,
        media_item: MediaItem,
        caption_text: str | None,
    ) -> str | None:
        if media_item.telegram_file_id:
            try:
                message = await self._send_single_media_value(
                    context,
                    request_context,
                    media_item,
                    caption_text,
                    media_item.telegram_file_id,
                )
                return self._extract_telegram_file_id(message, media_item.media_type)
            except BadRequest as exc:
                if not self._is_rejected_telegram_file_id(exc):
                    raise
                logger.info(
                    "Cached Telegram file_id rejected; retrying local media upload",
                    extra={
                        "chat_id": request_context.chat_id,
                        "media_type": media_item.media_type,
                        "failure_class": "telegram_file_id_rejected",
                    },
                )

        with open(media_item.file_path, "rb") as media_file:
            message = await self._send_single_media_value(
                context,
                request_context,
                media_item,
                caption_text,
                media_file,
            )
        return self._extract_telegram_file_id(message, media_item.media_type)

    async def _send_single_media_value(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: RequestContext,
        media_item: MediaItem,
        caption_text: str | None,
        media_value: Any,
    ) -> Message:
        if media_item.media_type == "video":
            return await context.bot.send_video(
                chat_id=request_context.chat_id,
                video=media_value,
                caption=caption_text,
                reply_to_message_id=request_context.original_message_id,
                **self._telegram_video_kwargs(media_item),
            )
        return await context.bot.send_photo(
            chat_id=request_context.chat_id,
            photo=media_value,
            caption=caption_text,
            reply_to_message_id=request_context.original_message_id,
        )

    async def _send_media_group_chunk(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: RequestContext,
        media_items: list[MediaItem],
        caption_text: str | None,
    ) -> list[str | None]:
        try:
            messages = await self._send_media_group_values(
                context,
                request_context,
                media_items,
                caption_text,
            )
        except BadRequest as exc:
            if not any(item.telegram_file_id for item in media_items) or not self._is_rejected_telegram_file_id(exc):
                raise
            logger.info(
                "Cached Telegram file_id rejected in media group; retrying local media upload",
                extra={
                    "chat_id": request_context.chat_id,
                    "media_count": len(media_items),
                    "failure_class": "telegram_file_id_rejected",
                },
            )
            messages = await self._send_media_group_values(
                context,
                request_context,
                media_items,
                caption_text,
                force_local_upload=True,
            )
        return [
            self._extract_telegram_file_id(message, media_item.media_type)
            for message, media_item in zip(messages or [], media_items)
        ]

    async def _send_media_group_values(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: RequestContext,
        media_items: list[MediaItem],
        caption_text: str | None,
        *,
        force_local_upload: bool = False,
    ) -> list[Message]:
        with ExitStack() as stack:
            media_group = []
            for index, media_item in enumerate(media_items):
                media_value = None if force_local_upload else media_item.telegram_file_id
                if not media_value:
                    media_value = stack.enter_context(open(media_item.file_path, "rb"))
                item_caption = caption_text if index == 0 else None
                if media_item.media_type == "video":
                    media_group.append(
                        InputMediaVideo(
                            media=media_value,
                            caption=item_caption,
                            **self._telegram_video_kwargs(media_item),
                        )
                    )
                else:
                    media_group.append(InputMediaPhoto(media=media_value, caption=item_caption))
            return await context.bot.send_media_group(
                chat_id=request_context.chat_id,
                media=media_group,
                reply_to_message_id=request_context.original_message_id,
            )

    @staticmethod
    def _is_rejected_telegram_file_id(error: TelegramError) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "wrong file identifier",
                "file_id_invalid",
                "file reference expired",
            )
        )

    def _persist_telegram_file_ids(
        self,
        request_context: RequestContext,
        telegram_file_ids: list[str | None],
    ) -> None:
        if not telegram_file_ids or not any(telegram_file_ids):
            return
        self.state_store.update_cached_telegram_file_ids(
            request_context.chat_id,
            request_context.normalized_url,
            telegram_file_ids,
        )

    @staticmethod
    def _extract_telegram_file_id(message: Any, media_type: str) -> str | None:
        if media_type == "video":
            video = getattr(message, "video", None)
            return getattr(video, "file_id", None)
        photos = getattr(message, "photo", None)
        if photos:
            return getattr(photos[-1], "file_id", None)
        return None

    @staticmethod
    def _cleanup_files(files: List[Path]) -> None:
        """Delete downloaded files safely."""
        for file_path in files:
            try:
                file_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to clean up file %s: %s", file_path, exc)

    def _purge_expired_cache(self) -> None:
        """Delete expired cache files and state rows."""
        if not settings.RESULT_CACHE_ENABLED:
            return
        for path in self.state_store.purge_expired_results():
            try:
                path.unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to delete expired cache file %s", path)

    @staticmethod
    def _video_info_from_cache(cached: CachedMediaEntry) -> VideoInfo:
        media_items = [
            MediaItem(
                file_path=Path(item["file_path"]),
                media_type=item["media_type"],
                caption=item.get("caption"),
                duration=item.get("duration"),
                width=item.get("width"),
                height=item.get("height"),
                telegram_file_id=item.get("telegram_file_id"),
            )
            for item in cached.media_items
        ]
        primary = media_items[0]
        return VideoInfo(
            file_path=primary.file_path,
            title=cached.title,
            description=cached.title,
            media_items=media_items,
            primary_media_type=primary.media_type,
            from_cache=True,
        )

    @staticmethod
    def _build_submission_message(
        provider_label: str,
        *,
        queue_position: int,
        joined_existing: bool = False,
        chaos_enabled: bool = False,
    ) -> str:
        return ChaosText.submission(
            TextContext(provider_label=provider_label, chaos_enabled=chaos_enabled),
            queue_position=queue_position,
            joined_existing=joined_existing,
        )

    @classmethod
    def _build_caption_text(cls, title: str) -> str:
        """Build a Telegram-safe media caption."""
        caption = title.strip()
        if not caption:
            return ""
        full_caption = ChaosText.media_caption(caption)
        if len(full_caption) <= cls.MAX_MEDIA_CAPTION_LENGTH:
            return full_caption
        return full_caption[: cls.MAX_MEDIA_CAPTION_LENGTH - 3].rstrip() + "..."

    @staticmethod
    def _telegram_video_kwargs(media_item: MediaItem) -> dict[str, object]:
        """Build optional Telegram video metadata from a media item."""
        kwargs: dict[str, object] = {}
        if media_item.width:
            kwargs["width"] = int(media_item.width)
        if media_item.height:
            kwargs["height"] = int(media_item.height)
        if media_item.duration is not None:
            kwargs["duration"] = dtm.timedelta(seconds=max(0, round(float(media_item.duration))))
        if media_item.file_path.suffix.lower() in {".mp4", ".mov"}:
            kwargs["supports_streaming"] = True
        return kwargs

    @staticmethod
    def _build_error_message(error: Exception, *, chaos_enabled: bool = False) -> str:
        return ChaosText.error(error, chaos_enabled=chaos_enabled)

    @staticmethod
    async def _edit_status_message(message: Message, text: str) -> None:
        """Try to edit a transient status message without creating extra chat noise."""
        try:
            await message.edit_text(text)
        except Exception:
            logger.debug("Failed to edit transient status message", exc_info=True)

    @staticmethod
    async def _safe_edit_text(message: Message, text: str) -> None:
        """Edit status text, falling back to a new visible reply for important states."""
        try:
            await message.edit_text(text)
        except Exception:
            try:
                await message.reply_text(text)
            except Exception:
                logger.debug("Failed to edit or reply with status update", exc_info=True)

    @staticmethod
    async def _delete_status_message(message: Message) -> None:
        """Delete a transient status message after successful completion."""
        try:
            await message.delete()
        except Exception:
            logger.debug("Failed to delete transient status message", exc_info=True)

    @staticmethod
    def _user_label(update: Update) -> str:
        user = update.effective_user
        if user is None:
            return "unknown"
        if user.username:
            return f"@{user.username}"
        if user.full_name:
            return user.full_name
        return str(user.id)

    def _message_is_from_owner(self, update: Update) -> bool:
        return (
            settings.BOT_OWNER_USER_ID is not None
            and update.effective_user is not None
            and update.effective_user.id == settings.BOT_OWNER_USER_ID
        )

    async def _whitelist_forwarded_visible_user(self, update: Update) -> bool:
        if not update.message or not update.effective_user:
            return False
        forwarded_user_id = self._forwarded_visible_user_id(update.message)
        if forwarded_user_id is None:
            return False
        self.state_store.add_inline_whitelist_user(
            forwarded_user_id,
            added_by_user_id=update.effective_user.id,
            note="owner_forward",
        )
        await update.message.reply_text(ChaosText.inline_whitelist_forward_added(forwarded_user_id))
        return True

    @staticmethod
    def _forwarded_visible_user_id(message: Message) -> int | None:
        forward_from = getattr(message, "forward_from", None)
        if getattr(forward_from, "id", None) is not None:
            return int(forward_from.id)
        forward_origin = getattr(message, "forward_origin", None)
        sender_user = getattr(forward_origin, "sender_user", None)
        if getattr(sender_user, "id", None) is not None:
            return int(sender_user.id)
        return None

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
        result = self.state_store.update_group_settings(update.effective_chat.id, **{setting_name: desired})
        await update.message.reply_text(ChaosText.setting_updated(label, result[setting_name]))

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
            await update.message.reply_text(ChaosText.numeric_setting_usage(command_name))
            return
        result = self.state_store.update_group_settings(update.effective_chat.id, **{setting_name: value})
        if setting_name == "chat_max_concurrent_jobs":
            self.job_manager.update_chat_limits(update.effective_chat.id, chat_limit=value)
        elif setting_name == "user_max_active_jobs":
            self.job_manager.update_chat_limits(update.effective_chat.id, user_limit=value)
        await update.message.reply_text(ChaosText.numeric_setting_updated(label, result[setting_name]))

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
        if settings.BOT_OWNER_USER_ID is not None and update.effective_user.id == settings.BOT_OWNER_USER_ID:
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
        normalized = value.strip().lower()
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            return True
        if normalized in {"off", "disable", "disabled", "false", "0"}:
            return False
        return None

    @staticmethod
    def _parse_positive_int_arg(value: str) -> int | None:
        stripped = value.strip()
        if not stripped.isdigit():
            return None
        parsed = int(stripped)
        if parsed <= 0:
            return None
        return parsed

    async def _notify_owner_about_low_account_pool(self, context, event) -> None:
        if event is None or not event.should_alert_owner:
            return
        if settings.BOT_OWNER_USER_ID is None:
            logger.warning("Skipping low account pool owner alert: BOT_OWNER_USER_ID is not configured")
            return
        if not getattr(context, "bot", None):
            logger.warning("Skipping low account pool owner alert: Telegram bot context is unavailable")
            return

        text = (
            "Instagram account pool warning:\n"
            f"Usable accounts left: {event.available_accounts} of {event.total_accounts}.\n"
            f"Low-watermark threshold: {event.low_watermark}.\n"
            f"Last removed account: {event.username}.\n"
            f"Reason: {event.reason} after {event.consecutive_failures} sequential failures."
        )
        try:
            await context.bot.send_message(chat_id=settings.BOT_OWNER_USER_ID, text=text)
        except TelegramError as exc:
            logger.warning("Failed to send low account pool owner alert: %s", exc)

    def _cleanup_request_task(self, request_id: str) -> None:
        self.active_request_tasks.pop(request_id, None)
        self.request_contexts.pop(request_id, None)

    def run(self) -> None:
        """Start the Telegram bot."""
        if not settings.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is not set in environment variables")

        self.application = (
            ApplicationBuilder()
            .token(settings.BOT_TOKEN)
            .concurrent_updates(settings.TELEGRAM_CONCURRENT_UPDATES)
            .connection_pool_size(settings.TELEGRAM_CONNECTION_POOL_SIZE)
            .media_write_timeout(settings.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS)
            .build()
        )

        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("formats", self.formats_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("chaos", self.chaos_command))
        self.application.add_handler(CommandHandler("quiet", self.quiet_command))
        self.application.add_handler(CommandHandler("dupes", self.dupes_command))
        self.application.add_handler(CommandHandler("statsmode", self.statsmode_command))
        self.application.add_handler(CommandHandler("chatlimit", self.chatlimit_command))
        self.application.add_handler(CommandHandler("userlimit", self.userlimit_command))
        self.application.add_handler(CommandHandler("admin_status", self.admin_status_command))
        self.application.add_handler(CommandHandler("admin_global_status", self.admin_global_status_command))
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_message,
            )
        )
        self.application.add_error_handler(self._global_error_handler)

        logger.info("Bot started and ready to process messages")
        self.application.run_polling()
