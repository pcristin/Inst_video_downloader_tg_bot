"""Standard Telegram command workflows."""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..chaos_text import ChaosText
from ..rich_text import RichText, command_reply_rich_text


class TelegramCommandHandlers:
    """Handle standard Telegram commands for the bot facade."""

    def __init__(self, bot: Any):
        self._bot = bot

    async def start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Greet a user and show the shortest useful onboarding message."""
        bot = self._bot
        if not update.message:
            return
        language_code = bot._language_for_update(update)
        await self._reply_rich_text(
            update.message,
            command_reply_rich_text(ChaosText.start(language_code)),
        )

    async def language_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Persist a user's language preference."""
        bot = self._bot
        if not update.message or not update.effective_user:
            return
        current_language = bot._language_for_update(update)
        requested_language = (context.args[0] if context.args else "").strip().lower()
        if requested_language not in {"en", "ru"}:
            await update.message.reply_text(ChaosText.language_usage(current_language))
            return
        bot.state_store.set_user_language(update.effective_user.id, requested_language)
        await update.message.reply_text(ChaosText.language_updated(requested_language))

    async def help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show supported providers and usage help."""
        bot = self._bot
        if not update.message or not update.effective_chat:
            return
        group_settings = bot.state_store.ensure_group_settings(update.effective_chat.id)
        await self._reply_rich_text(
            update.message,
            command_reply_rich_text(
                ChaosText.help(
                    group_settings["chaos_mode_enabled"],
                    bot._language_for_update(update),
                )
            ),
        )

    @staticmethod
    async def _reply_rich_text(message: Any, rich_text: RichText) -> None:
        if rich_text.entities:
            await message.reply_text(rich_text.text, entities=rich_text.entities)
            return
        await message.reply_text(rich_text.text)

    async def admin_help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show owner-only operational command usage."""
        bot = self._bot
        if not update.message:
            return
        if not await bot._require_owner(update):
            return
        await self._reply_rich_text(
            update.message,
            command_reply_rich_text(ChaosText.admin_help()),
        )

    async def formats_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show supported URL shapes."""
        bot = self._bot
        if not update.message:
            return
        await update.message.reply_text(ChaosText.formats(bot._language_for_update(update)))

    async def status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show a safe queue and health summary."""
        bot = self._bot
        if not update.message or not update.effective_chat:
            return
        snapshot = bot.job_manager.get_snapshot(update.effective_chat.id)
        persisted = bot.state_store.get_public_status(update.effective_chat.id)
        group_settings = bot.state_store.ensure_group_settings(update.effective_chat.id)
        await update.message.reply_text(
            ChaosText.status(
                snapshot,
                persisted,
                chaos_enabled=group_settings["chaos_mode_enabled"],
                language_code=bot._language_for_update(update),
            )
        )

    async def cancel_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Cancel the latest active request from the current user."""
        bot = self._bot
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        request_id = bot.job_manager.get_latest_active_request_id(
            update.effective_chat.id,
            update.effective_user.id,
        )
        if not request_id:
            await update.message.reply_text(
                ChaosText.no_active_request(bot._language_for_update(update))
            )
            return

        task = bot.active_request_tasks.get(request_id)
        if task and not task.done():
            task.cancel()
        job = bot.job_manager.cancel_request(request_id)
        request_context = bot.request_contexts.get(request_id)
        if request_context:
            await bot._safe_edit_text(
                request_context.status_message,
                ChaosText.cancelled(
                    request_context.chaos_enabled, request_context.language_code
                ),
            )
        if job and update.message:
            await update.message.reply_text(
                ChaosText.latest_cancelled(bot._language_for_update(update))
            )

    async def stats_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show lightweight group stats."""
        bot = self._bot
        if not update.message or not update.effective_chat:
            return
        group_settings = bot.state_store.ensure_group_settings(update.effective_chat.id)
        if not group_settings["stats_enabled"]:
            await update.message.reply_text(
                ChaosText.stats_disabled(bot._language_for_update(update))
            )
            return

        stats = bot.state_store.get_group_stats(update.effective_chat.id)
        await update.message.reply_text(
            ChaosText.stats(
                stats,
                chaos_enabled=group_settings["chaos_mode_enabled"],
                language_code=bot._language_for_update(update),
            )
        )

    async def chaos_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle or inspect chat-level chaos mode."""
        bot = self._bot
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        action = (context.args[0] if context.args else "status").strip().lower()
        if action == "status":
            settings_row = bot.state_store.ensure_group_settings(
                update.effective_chat.id
            )
            await update.message.reply_text(
                ChaosText.chaos_status(settings_row["chaos_mode_enabled"])
            )
            return

        desired = bot._parse_toggle_arg(action)
        if desired is None:
            await update.message.reply_text(ChaosText.chaos_usage())
            return
        if not await bot._require_chaos_admin(update, context):
            return

        result = bot.state_store.update_group_settings(
            update.effective_chat.id,
            chaos_mode_enabled=desired,
        )
        await update.message.reply_text(
            ChaosText.chaos_updated(result["chaos_mode_enabled"])
        )

    async def quiet_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle quiet mode for the current chat. Owner-only."""
        await self._bot._toggle_group_setting(
            update,
            context,
            setting_name="quiet_mode",
            command_name="quiet",
            label="Тихий режим",
        )

    async def dupes_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle duplicate suppression for the current chat. Owner-only."""
        await self._bot._toggle_group_setting(
            update,
            context,
            setting_name="duplicate_suppression",
            command_name="dupes",
            label="Защита от повторов",
        )

    async def statsmode_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Toggle stats collection visibility for the current chat. Owner-only."""
        await self._bot._toggle_group_setting(
            update,
            context,
            setting_name="stats_enabled",
            command_name="statsmode",
            label="Статистика",
        )

    async def chatlimit_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Override per-chat concurrent job limit. Owner-only."""
        await self._bot._set_numeric_group_setting(
            update,
            context,
            setting_name="chat_max_concurrent_jobs",
            command_name="chatlimit",
            label="Лимит чата",
        )

    async def userlimit_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Override per-user active job limit for the current chat. Owner-only."""
        await self._bot._set_numeric_group_setting(
            update,
            context,
            setting_name="user_max_active_jobs",
            command_name="userlimit",
            label="Лимит на пользователя",
        )
