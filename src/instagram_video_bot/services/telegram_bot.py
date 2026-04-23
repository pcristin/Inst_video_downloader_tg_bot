"""Telegram bot service for handling group-friendly media downloads."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
import logging
from pathlib import Path
import time
from typing import List, Optional

from telegram import InputMediaPhoto, InputMediaVideo, Message, Update
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config.settings import settings
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


class TelegramBot:
    """Telegram bot for downloading media links."""

    INSTAGRAM_VIDEO_PATTERN = RequestParser.URL_PATTERN
    MAX_MEDIA_CAPTION_LENGTH = 1024

    def __init__(self, state_store: StateStore | None = None):
        self.application: Optional[Application] = None
        self.state_store = state_store or StateStore()
        self.job_manager = JobManager(self.state_store)
        self.job_manager.add_state_listener(self._on_job_state_change)
        self.active_request_tasks: dict[str, asyncio.Task[None]] = {}
        self.request_contexts: dict[str, RequestContext] = {}
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
            return

        group_settings = self.state_store.ensure_group_settings(update.effective_chat.id)
        raw_url_count = len(RequestParser.URL_PATTERN.findall(message_text))
        if raw_url_count > settings.MAX_LINKS_PER_MESSAGE:
            await update.message.reply_text(
                f"Only the first {settings.MAX_LINKS_PER_MESSAGE} supported links will be queued."
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
                execute=self._build_job_executor(update.effective_chat.id, parsed_link),
                duplicate_suppression=group_settings["duplicate_suppression"],
            )
            status_message = await update.message.reply_text(
                self._build_submission_message(
                    parsed_link.provider_label,
                    queue_position=submission.queue_position,
                    joined_existing=not submission.is_new_job,
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
            )
            self.request_contexts[submission.request_id] = request_context
            task = asyncio.create_task(self._await_request(context, request_context, submission.job))
            self.active_request_tasks[submission.request_id] = task
            task.add_done_callback(lambda _task, rid=submission.request_id: self._cleanup_request_task(rid))

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show supported providers and usage help."""
        if not update.message:
            return
        await update.message.reply_text(
            "Send a supported link directly in chat.\n"
            "Providers: Instagram, Twitter/X, YouTube Shorts.\n"
            "Commands: /help, /status, /formats, /cancel, /stats\n"
            "Owner commands: /quiet on|off, /dupes on|off, /statsmode on|off,\n"
            "/chatlimit <n>, /userlimit <n>, /admin_status"
        )

    async def formats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show supported URL shapes."""
        if not update.message:
            return
        await update.message.reply_text(
            "Supported formats:\n"
            "- Instagram posts, reels, stories, and share links\n"
            "- Twitter/X status links\n"
            "- YouTube Shorts URLs"
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show a safe queue and health summary."""
        if not update.message or not update.effective_chat:
            return
        snapshot = self.job_manager.get_snapshot(update.effective_chat.id)
        persisted = self.state_store.get_public_status(update.effective_chat.id)
        await update.message.reply_text(
            "Queue status:\n"
            f"- Active jobs: {snapshot['active_jobs']}\n"
            f"- Queued jobs: {snapshot['queued_jobs']}\n"
            f"- Active requests: {snapshot['active_requests']}\n"
            f"- Chat concurrency limit: {snapshot['chat_limit']}\n"
            f"- Per-user limit: {snapshot['user_limit']}\n"
            f"- Completed requests: {persisted['completed']}\n"
            f"- Failed requests: {persisted['failed']}\n"
            f"- Cache hits: {persisted['cache_hits']}"
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
            await update.message.reply_text("You have no active queued or running requests.")
            return

        task = self.active_request_tasks.get(request_id)
        if task and not task.done():
            task.cancel()
        job = self.job_manager.cancel_request(request_id)
        request_context = self.request_contexts.get(request_id)
        if request_context:
            await self._safe_edit_text(request_context.status_message, "🛑 Request cancelled.")
        if job and update.message:
            await update.message.reply_text("Latest request cancelled.")

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show lightweight group stats."""
        if not update.message or not update.effective_chat:
            return
        group_settings = self.state_store.ensure_group_settings(update.effective_chat.id)
        if not group_settings["stats_enabled"]:
            await update.message.reply_text("Stats are disabled for this chat.")
            return

        stats = self.state_store.get_group_stats(update.effective_chat.id)
        top_users = ", ".join(f"{name} ({count})" for name, count in stats["top_users"]) or "No completed requests yet"
        top_providers = ", ".join(
            f"{provider} ({count})" for provider, count in stats["top_providers"]
        ) or "No completed requests yet"
        await update.message.reply_text(
            "Group stats:\n"
            f"- Completed: {stats['completed']}\n"
            f"- Failed: {stats['failed']}\n"
            f"- Cancelled: {stats['cancelled']}\n"
            f"- Top users: {top_users}\n"
            f"- Top providers: {top_providers}"
        )

    async def quiet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle quiet mode for the current chat. Owner-only."""
        await self._toggle_group_setting(
            update,
            context,
            setting_name="quiet_mode",
            command_name="quiet",
            label="Quiet mode",
        )

    async def dupes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle duplicate suppression for the current chat. Owner-only."""
        await self._toggle_group_setting(
            update,
            context,
            setting_name="duplicate_suppression",
            command_name="dupes",
            label="Duplicate suppression",
        )

    async def statsmode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle stats collection visibility for the current chat. Owner-only."""
        await self._toggle_group_setting(
            update,
            context,
            setting_name="stats_enabled",
            command_name="statsmode",
            label="Stats mode",
        )

    async def chatlimit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Override per-chat concurrent job limit. Owner-only."""
        await self._set_numeric_group_setting(
            update,
            context,
            setting_name="chat_max_concurrent_jobs",
            command_name="chatlimit",
            label="Chat concurrency limit",
        )

    async def userlimit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Override per-user active job limit for the current chat. Owner-only."""
        await self._set_numeric_group_setting(
            update,
            context,
            setting_name="user_max_active_jobs",
            command_name="userlimit",
            label="Per-user limit",
        )

    async def admin_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show owner-facing operational status for the current chat."""
        if not update.message or not update.effective_chat:
            return
        if not await self._require_owner(update):
            return
        snapshot = self.job_manager.get_snapshot(update.effective_chat.id)
        admin_status = self.state_store.get_admin_status(update.effective_chat.id)
        settings_row = admin_status["settings"]
        provider_lines = ", ".join(
            f"{provider}:{status}={count}"
            for provider, status, count in admin_status["provider_job_counts"]
        ) or "none"
        failure_lines = "\n".join(
            f"  - {provider} | {error_class} | {normalized_url}"
            for provider, normalized_url, error_class, _finished_at in admin_status["recent_failures"]
        ) or "  - none"
        uptime_seconds = int(time.time() - self.started_at)
        await update.message.reply_text(
            "Admin status:\n"
            f"- Uptime: {uptime_seconds}s\n"
            f"- Quiet mode: {'on' if settings_row['quiet_mode'] else 'off'}\n"
            f"- Duplicate suppression: {'on' if settings_row['duplicate_suppression'] else 'off'}\n"
            f"- Stats mode: {'on' if settings_row['stats_enabled'] else 'off'}\n"
            f"- Chat concurrency limit: {settings_row['chat_max_concurrent_jobs']}\n"
            f"- Per-user limit: {settings_row['user_max_active_jobs']}\n"
            f"- Running jobs: {admin_status['running_jobs']}\n"
            f"- Queued jobs: {admin_status['queued_jobs']}\n"
            f"- Active requests: {snapshot['active_requests']}\n"
            f"- Failed jobs: {admin_status['failed_jobs']}\n"
            f"- Cache entries: {admin_status['cache_entries']}\n"
            f"- Result cache enabled: {'on' if settings.RESULT_CACHE_ENABLED else 'off'}\n"
            f"- Queue manager enabled: {'on' if settings.QUEUE_MANAGER_ENABLED else 'off'}\n"
            f"- Provider job counts: {provider_lines}\n"
            f"- Recent failures:\n{failure_lines}"
        )

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
                        await self._send_media(context, request_context, video_info)
                    except Exception as error:
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
            error_message = self._build_error_message(error)
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
                "❌ An unexpected error occurred. Please try again later."
            )
            self.job_manager.mark_request_failed(request_context.request_id, status="failed")

    def _build_job_executor(self, chat_id: int, parsed_link: ParsedRequestLink):
        """Create the underlying shared job executor closure."""

        async def _execute() -> VideoInfo:
            cached = None
            if settings.RESULT_CACHE_ENABLED:
                cached = self.state_store.get_cached_result(chat_id, parsed_link.normalized_url)
            if cached:
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
            video_info = await downloader.download_video(parsed_link.original_url, output_dir)
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
                        }
                        for item in video_info.media_items
                    ],
                    ttl_seconds=settings.RECENT_RESULT_TTL_SECONDS,
                )
            return video_info

        return _execute

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
                text = f"⬇️ {request_context.provider_label} downloading..."
                await self._edit_status_message(request_context.status_message, text)
            elif job.state == "cancelled":
                text = "🛑 Request cancelled."
                await self._safe_edit_text(request_context.status_message, text)
            else:
                text = "❌ Download failed."
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

        if len(media_items) == 1:
            media_item = media_items[0]
            with open(media_item.file_path, "rb") as media_file:
                if media_item.media_type == "video":
                    await context.bot.send_video(
                        chat_id=request_context.chat_id,
                        video=media_file,
                        caption=caption_text,
                        reply_to_message_id=request_context.original_message_id,
                    )
                else:
                    await context.bot.send_photo(
                        chat_id=request_context.chat_id,
                        photo=media_file,
                        caption=caption_text,
                        reply_to_message_id=request_context.original_message_id,
                    )
            return

        with ExitStack() as stack:
            media_group = []
            for index, media_item in enumerate(media_items):
                media_file = stack.enter_context(open(media_item.file_path, "rb"))
                item_caption = caption_text if index == 0 else None
                if media_item.media_type == "video":
                    media_group.append(InputMediaVideo(media=media_file, caption=item_caption))
                else:
                    media_group.append(InputMediaPhoto(media=media_file, caption=item_caption))

            await context.bot.send_media_group(
                chat_id=request_context.chat_id,
                media=media_group,
                reply_to_message_id=request_context.original_message_id,
            )

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
    ) -> str:
        if joined_existing:
            return f"🔁 {provider_label} already in progress. Waiting for the shared result."
        if queue_position > 1:
            ahead = queue_position - 1
            return f"🕓 {provider_label} queued. {ahead} ahead of you."
        return f"🕓 {provider_label} accepted. Starting shortly."

    @classmethod
    def _build_caption_text(cls, title: str) -> str:
        """Build a Telegram-safe media caption."""
        caption = title.strip()
        if not caption:
            return ""
        full_caption = f"📹 {caption}"
        if len(full_caption) <= cls.MAX_MEDIA_CAPTION_LENGTH:
            return full_caption
        return full_caption[: cls.MAX_MEDIA_CAPTION_LENGTH - 3].rstrip() + "..."

    @staticmethod
    def _build_error_message(error: Exception) -> str:
        error_str = str(error).lower()
        if "authentication failed" in error_str or "cookies have expired" in error_str:
            return (
                "🔐 Instagram authentication failed. "
                "The bot owner needs to refresh the session."
            )
        if "rate-limit" in error_str or "rate limit" in error_str:
            return "⏳ Provider rate limit reached. Please try again later."
        if "unsupported" in error_str:
            return f"❌ {str(error)}"
        if "timed out" in error_str:
            return "⏱️ Download timed out. Please try again."
        return f"❌ Sorry, couldn't download the media: {str(error)}"

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
            await update.message.reply_text(f"Usage: /{command_name} on|off")
            return
        result = self.state_store.update_group_settings(update.effective_chat.id, **{setting_name: desired})
        state = "enabled" if result[setting_name] else "disabled"
        await update.message.reply_text(f"{label} {state}.")

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
            await update.message.reply_text(f"Usage: /{command_name} <positive integer>")
            return
        result = self.state_store.update_group_settings(update.effective_chat.id, **{setting_name: value})
        if setting_name == "chat_max_concurrent_jobs":
            self.job_manager.update_chat_limits(update.effective_chat.id, chat_limit=value)
        elif setting_name == "user_max_active_jobs":
            self.job_manager.update_chat_limits(update.effective_chat.id, user_limit=value)
        await update.message.reply_text(f"{label} set to {result[setting_name]}.")

    async def _require_owner(self, update: Update) -> bool:
        """Return whether the sender is the configured bot owner."""
        if not update.message or not update.effective_user:
            return False
        if settings.BOT_OWNER_USER_ID is None:
            await update.message.reply_text(
                "Owner-only commands are unavailable until BOT_OWNER_USER_ID is configured."
            )
            return False
        if update.effective_user.id != settings.BOT_OWNER_USER_ID:
            await update.message.reply_text("This command is only available to the bot owner.")
            return False
        return True

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
            .build()
        )

        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("formats", self.formats_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("quiet", self.quiet_command))
        self.application.add_handler(CommandHandler("dupes", self.dupes_command))
        self.application.add_handler(CommandHandler("statsmode", self.statsmode_command))
        self.application.add_handler(CommandHandler("chatlimit", self.chatlimit_command))
        self.application.add_handler(CommandHandler("userlimit", self.userlimit_command))
        self.application.add_handler(CommandHandler("admin_status", self.admin_status_command))
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_message,
            )
        )
        self.application.add_error_handler(self._global_error_handler)

        logger.info("Bot started and ready to process messages")
        self.application.run_polling()
