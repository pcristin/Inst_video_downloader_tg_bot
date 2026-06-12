"""Telegram application construction and handler registration."""

from __future__ import annotations

import logging
from typing import Any

from telegram.ext import (Application, ApplicationBuilder,
                          CallbackQueryHandler, ChosenInlineResultHandler,
                          CommandHandler, InlineQueryHandler, MessageHandler,
                          PreCheckoutQueryHandler, filters)

from ..config.settings import settings

logger = logging.getLogger(__name__)


def build_telegram_application(bot: Any) -> tuple[Application, str]:
    """Build and wire a Telegram application for the configured bot mode."""

    builder = (
        ApplicationBuilder()
        .token(settings.BOT_TOKEN)
        .concurrent_updates(settings.TELEGRAM_CONCURRENT_UPDATES)
        .connection_pool_size(settings.TELEGRAM_CONNECTION_POOL_SIZE)
        .media_write_timeout(settings.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS)
    )
    builder = _configure_post_init(builder, bot)
    application = builder.build()

    if settings.BOT_LEGACY_REDIRECT_MODE and settings.BOT_MIGRATION_TARGET_USERNAME:
        _register_legacy_redirect_handlers(application, bot)
        mode = "legacy_redirect"
    else:
        _register_standard_handlers(application, bot)
        mode = "standard"

    application.add_error_handler(bot._global_error_handler)
    return application, mode


def _configure_post_init(builder: Any, bot: Any) -> Any:
    has_inline_post_init = (
        settings.INLINE_MODE_ENABLED and settings.INLINE_STORAGE_CHAT_ID is not None
    )
    has_migration_post_init = bool(settings.BOT_MIGRATION_TARGET_USERNAME)
    if not (has_inline_post_init or has_migration_post_init):
        return builder

    from .post_deploy_notifications import (
        send_bot_migration_announcement_once,
        send_inline_mode_announcement_once,
        send_inline_promo_refund_announcement_once)

    async def _post_init(application: Application) -> None:
        application.create_task(_run_post_deploy_tasks(application))

    async def _run_post_deploy_tasks(application: Application) -> None:
        if has_inline_post_init:
            await bot._evaluate_expired_inline_subscription_refunds(application)
            inline_result = await send_inline_mode_announcement_once(
                application.bot, bot.state_store
            )
            promo_result = await send_inline_promo_refund_announcement_once(
                application.bot, bot.state_store
            )
            logger.info("Inline mode announcement result: %s", inline_result)
            logger.info("Inline promo/refund announcement result: %s", promo_result)
        if settings.BOT_MIGRATION_TARGET_USERNAME:
            migration_result = await send_bot_migration_announcement_once(
                application.bot,
                bot.state_store,
                target_username=settings.BOT_MIGRATION_TARGET_USERNAME,
            )
            logger.info("Bot migration announcement result: %s", migration_result)

    return builder.post_init(_post_init)


def _register_legacy_redirect_handlers(application: Application, bot: Any) -> None:
    application.add_handler(InlineQueryHandler(bot.legacy_inline_query_handler))
    application.add_handler(CallbackQueryHandler(bot.legacy_callback_handler))
    application.add_handler(MessageHandler(filters.ALL, bot.legacy_redirect_handler))


def _register_standard_handlers(application: Application, bot: Any) -> None:
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("language", bot.language_command))
    application.add_handler(CommandHandler("admin_help", bot.admin_help_command))
    application.add_handler(CommandHandler("status", bot.status_command))
    application.add_handler(CommandHandler("formats", bot.formats_command))
    application.add_handler(CommandHandler("cancel", bot.cancel_command))
    application.add_handler(CommandHandler("stats", bot.stats_command))
    application.add_handler(CommandHandler("chaos", bot.chaos_command))
    application.add_handler(CommandHandler("quiet", bot.quiet_command))
    application.add_handler(CommandHandler("dupes", bot.dupes_command))
    application.add_handler(CommandHandler("statsmode", bot.statsmode_command))
    application.add_handler(CommandHandler("chatlimit", bot.chatlimit_command))
    application.add_handler(CommandHandler("userlimit", bot.userlimit_command))
    application.add_handler(CommandHandler("admin_status", bot.admin_status_command))
    application.add_handler(
        CommandHandler("admin_global_status", bot.admin_global_status_command)
    )
    application.add_handler(InlineQueryHandler(bot.inline_query_handler))
    application.add_handler(ChosenInlineResultHandler(bot.chosen_inline_result_handler))
    application.add_handler(
        CallbackQueryHandler(
            bot.inline_callback_handler,
            pattern=r"^inline(?:_once)?:[A-Za-z0-9_-]+$",
        )
    )
    application.add_handler(PreCheckoutQueryHandler(bot.pre_checkout_handler))
    application.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, bot.successful_payment_handler)
    )
    application.add_handler(
        CommandHandler("inline_whitelist", bot.inline_whitelist_command)
    )
    application.add_handler(CommandHandler("inline_price", bot.inline_price_command))
    application.add_handler(
        CommandHandler("inline_onetime", bot.inline_onetime_command)
    )
    application.add_handler(CommandHandler("inline_refund", bot.inline_refund_command))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            bot.handle_message,
        )
    )
