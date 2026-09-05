import asyncio
import logging
import warnings
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, Update, User
from telegram.error import BadRequest, NetworkError
from telegram.warnings import PTBUserWarning

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services import telegram_wiring
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import TelegramBot
from src.instagram_video_bot.services.telegram_wiring import _diagnose_group_privacy


def _message_update(
    *, chat_type: str, text: str | None = None, caption: str | None = None
) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-1001, type=chat_type),
            from_user=User(id=1001, first_name="User", is_bot=False),
            text=text,
            caption=caption,
        ),
    )


@pytest.fixture
def telegram_bot_factory(tmp_path):
    def build_bot():
        return TelegramBot(state_store=StateStore(tmp_path / "state.db"))

    return build_bot


@pytest.mark.asyncio
async def test_global_error_handler_handles_network_error(telegram_bot_factory):
    bot = telegram_bot_factory()
    context = SimpleNamespace(error=NetworkError("Bad Gateway"))

    await bot._global_error_handler(update=None, context=context)


@pytest.mark.asyncio
async def test_global_error_handler_logs_bad_request_as_api_rejection(
    caplog, telegram_bot_factory
):
    bot = telegram_bot_factory()
    context = SimpleNamespace(error=BadRequest("Can't get stat about the file"))

    with caplog.at_level(logging.WARNING):
        await bot._global_error_handler(update=None, context=context)

    record = caplog.records[-1]
    assert record.levelno == logging.ERROR
    assert record.failure_class == "telegram_bad_request"
    assert record.message == "Telegram API request rejected"


def test_run_registers_global_error_handler(monkeypatch, telegram_bot_factory):
    registered = {
        "error_handler": None,
        "handlers": [],
        "post_init": None,
        "ran": False,
    }

    class FakeApplication:
        def add_handler(self, handler):
            registered["handlers"].append(handler)

        def add_error_handler(self, handler):
            registered["error_handler"] = handler

        def run_polling(self):
            registered["ran"] = True

    class FakeBuilder:
        def token(self, _token):
            return self

        def concurrent_updates(self, _updates):
            return self

        def connection_pool_size(self, _size):
            return self

        def media_write_timeout(self, _timeout):
            return self

        def post_init(self, callback):
            registered["post_init"] = callback
            return self

        def post_stop(self, _callback):
            return self

        def post_shutdown(self, _callback):
            return self

        def build(self):
            return FakeApplication()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_wiring.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)

    bot = telegram_bot_factory()
    bot.run()

    admin_handler_contract = [
        ("admin_help_command", "CommandHandler"),
    ]
    inline_handler_contract = [
        ("inline_query_handler", "InlineQueryHandler"),
        ("chosen_inline_result_handler", "ChosenInlineResultHandler"),
        ("inline_callback_handler", "CallbackQueryHandler"),
        ("inline_action_callback_handler", "CallbackQueryHandler"),
        ("pre_checkout_handler", "PreCheckoutQueryHandler"),
        ("inline_gallery_callback_handler", "CallbackQueryHandler"),
        ("successful_payment_handler", "MessageHandler"),
        ("inline_whitelist_command", "CommandHandler"),
        ("inline_price_command", "CommandHandler"),
        ("inline_onetime_command", "CommandHandler"),
        ("inline_refund_command", "CommandHandler"),
    ]
    callback_names = [
        handler.callback.__name__
        for handler in registered["handlers"]
        if getattr(handler, "callback", None) is not None
    ]
    inline_callback_names = [name for name, _class_name in inline_handler_contract]
    inline_block_start = callback_names.index("inline_query_handler")
    assert (
        callback_names[
            inline_block_start : inline_block_start + len(inline_callback_names)
        ]
        == inline_callback_names
    )
    assert all(
        callback_names.index(callback_name) < callback_names.index("handle_message")
        for callback_name in inline_callback_names
    )
    assert len(
        [name for name in callback_names if name in inline_callback_names]
    ) == len(inline_callback_names)

    handlers_by_callback_name = {
        handler.callback.__name__: handler
        for handler in registered["handlers"]
        if getattr(handler, "callback", None) is not None
    }
    for callback_name, class_name in admin_handler_contract + inline_handler_contract:
        assert type(handlers_by_callback_name[callback_name]).__name__ == class_name
    assert (
        handlers_by_callback_name["inline_callback_handler"].pattern.pattern
        == r"^inline(?:_once)?:[A-Za-z0-9_-]+$"
    )
    assert (
        handlers_by_callback_name["inline_action_callback_handler"].pattern.pattern
        == r"^inline-action:(?:cancel|retry):[A-Za-z0-9_-]+$"
    )
    assert (
        type(handlers_by_callback_name["successful_payment_handler"].filters).__name__
        == "SuccessfulPayment"
    )
    assert handlers_by_callback_name["admin_help_command"].commands == frozenset(
        {"admin_help"}
    )
    assert handlers_by_callback_name["inline_whitelist_command"].commands == frozenset(
        {"inline_whitelist"}
    )
    assert handlers_by_callback_name["inline_price_command"].commands == frozenset(
        {"inline_price"}
    )
    assert handlers_by_callback_name["inline_onetime_command"].commands == frozenset(
        {"inline_onetime"}
    )
    assert handlers_by_callback_name["inline_refund_command"].commands == frozenset(
        {"inline_refund"}
    )
    assert handlers_by_callback_name["start_command"].commands == frozenset({"start"})
    assert handlers_by_callback_name["language_command"].commands == frozenset(
        {"language"}
    )
    assert "handle_message" in callback_names
    message_handler = handlers_by_callback_name["handle_message"]
    supported_url = "https://www.instagram.com/reel/abc/"
    assert message_handler.check_update(
        _message_update(chat_type="group", text=supported_url)
    )
    assert message_handler.check_update(
        _message_update(chat_type="supergroup", text=supported_url)
    )
    assert message_handler.check_update(
        _message_update(chat_type="supergroup", caption=supported_url)
    )
    assert registered["error_handler"] == bot._global_error_handler
    assert registered["post_init"] is not None
    assert registered["ran"] is True


@pytest.mark.asyncio
async def test_group_privacy_diagnostic_warns_when_plain_group_messages_are_hidden(
    caplog,
):
    class PrivacyBot:
        async def get_me(self):
            return SimpleNamespace(can_read_all_group_messages=False)

    with caplog.at_level(logging.WARNING):
        await _diagnose_group_privacy(PrivacyBot())

    assert "privacy mode" in caplog.text.lower()
    assert "botfather" in caplog.text.lower()


@pytest.mark.asyncio
async def test_post_deploy_task_is_owned_without_ptb_startup_warning(
    monkeypatch, telegram_bot_factory
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def skip_group_privacy_diagnostic(_telegram_bot):
        return None

    async def slow_announcement(_bot, _state_store):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        telegram_wiring, "_diagnose_group_privacy", skip_group_privacy_diagnostic
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.post_deploy_notifications.send_inline_mode_announcement_once",
        slow_announcement,
    )
    monkeypatch.setattr(settings, "INLINE_MODE_ENABLED", True)
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", None)

    bot = telegram_bot_factory()
    application = telegram_wiring._configure_post_init(
        telegram_wiring.ApplicationBuilder().token("test-token"), bot
    ).build()
    tasks_before = set(asyncio.all_tasks())

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", PTBUserWarning)
            await application.post_init(application)
        await asyncio.wait_for(started.wait(), timeout=1)

        startup_warnings = [
            warning
            for warning in caught
            if issubclass(warning.category, PTBUserWarning)
        ]
        assert startup_warnings == []
        assert application.post_stop is not None

        await application.post_stop(application)
        await application.post_shutdown(application)

        assert cancelled.is_set()
    finally:
        remaining_tasks = [
            task for task in set(asyncio.all_tasks()) - tasks_before if not task.done()
        ]
        for task in remaining_tasks:
            task.cancel()
        if remaining_tasks:
            await asyncio.gather(*remaining_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_post_deploy_task_failure_reaches_application_error_handlers(
    monkeypatch, telegram_bot_factory
):
    class PostDeployFailure(Exception):
        pass

    handled = asyncio.Event()

    async def skip_group_privacy_diagnostic(_telegram_bot):
        return None

    async def failing_announcement(_bot, _state_store):
        raise PostDeployFailure("announcement failed")

    async def error_handler(_update, context):
        if isinstance(context.error, PostDeployFailure):
            handled.set()

    monkeypatch.setattr(
        telegram_wiring, "_diagnose_group_privacy", skip_group_privacy_diagnostic
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.post_deploy_notifications.send_inline_mode_announcement_once",
        failing_announcement,
    )
    monkeypatch.setattr(settings, "INLINE_MODE_ENABLED", True)
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", None)

    bot = telegram_bot_factory()
    application = telegram_wiring._configure_post_init(
        telegram_wiring.ApplicationBuilder().token("test-token"), bot
    ).build()
    application.add_error_handler(error_handler)

    await application.post_init(application)
    try:
        await asyncio.wait_for(handled.wait(), timeout=1)
    finally:
        await application.post_stop(application)


@pytest.mark.asyncio
async def test_post_shutdown_cleans_task_when_application_never_starts(
    monkeypatch, telegram_bot_factory
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def skip_group_privacy_diagnostic(_telegram_bot):
        return None

    async def slow_announcement(_bot, _state_store):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        telegram_wiring, "_diagnose_group_privacy", skip_group_privacy_diagnostic
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.post_deploy_notifications.send_inline_mode_announcement_once",
        slow_announcement,
    )
    monkeypatch.setattr(settings, "INLINE_MODE_ENABLED", True)
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", None)

    bot = telegram_bot_factory()
    application = telegram_wiring._configure_post_init(
        telegram_wiring.ApplicationBuilder().token("test-token"), bot
    ).build()
    tasks_before = set(asyncio.all_tasks())

    try:
        await application.post_init(application)
        await asyncio.wait_for(started.wait(), timeout=1)

        assert application.post_shutdown is not None

        await application.post_shutdown(application)

        assert cancelled.is_set()
    finally:
        remaining_tasks = [
            task for task in set(asyncio.all_tasks()) - tasks_before if not task.done()
        ]
        for task in remaining_tasks:
            task.cancel()
        if remaining_tasks:
            await asyncio.gather(*remaining_tasks, return_exceptions=True)


def test_legacy_redirect_mode_registers_only_redirect_handlers(
    monkeypatch, telegram_bot_factory
):
    registered = {
        "error_handler": None,
        "handlers": [],
        "post_init": None,
        "ran": False,
    }

    class FakeApplication:
        def add_handler(self, handler):
            registered["handlers"].append(handler)

        def add_error_handler(self, handler):
            registered["error_handler"] = handler

        def run_polling(self):
            registered["ran"] = True

    class FakeBuilder:
        def token(self, _token):
            return self

        def concurrent_updates(self, _updates):
            return self

        def connection_pool_size(self, _size):
            return self

        def media_write_timeout(self, _timeout):
            return self

        def post_init(self, callback):
            registered["post_init"] = callback
            return self

        def post_stop(self, _callback):
            return self

        def post_shutdown(self, _callback):
            return self

        def build(self):
            return FakeApplication()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_wiring.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "BOT_LEGACY_REDIRECT_MODE", True)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", "igclipbot")

    bot = telegram_bot_factory()
    bot.run()

    callback_names = [
        handler.callback.__name__
        for handler in registered["handlers"]
        if getattr(handler, "callback", None) is not None
    ]
    assert callback_names == [
        "legacy_inline_query_handler",
        "legacy_callback_handler",
        "legacy_redirect_handler",
    ]
    assert registered["error_handler"] == bot._global_error_handler
    assert registered["post_init"] is not None
    assert registered["ran"] is True


def test_group_privacy_post_init_is_registered_without_inline_storage(
    monkeypatch, telegram_bot_factory
):
    registered = {"post_init": None}

    class FakeApplication:
        def add_handler(self, _handler):
            pass

        def add_error_handler(self, _handler):
            pass

        def run_polling(self):
            pass

    class FakeBuilder:
        def token(self, _token):
            return self

        def concurrent_updates(self, _updates):
            return self

        def connection_pool_size(self, _size):
            return self

        def media_write_timeout(self, _timeout):
            return self

        def post_init(self, callback):
            registered["post_init"] = callback
            return self

        def post_stop(self, _callback):
            return self

        def post_shutdown(self, _callback):
            return self

        def build(self):
            return FakeApplication()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_wiring.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", None)

    bot = telegram_bot_factory()
    bot.run()

    assert registered["post_init"] is not None


@pytest.mark.asyncio
async def test_migration_announcement_post_init_registers_without_inline_storage(
    monkeypatch, telegram_bot_factory
):
    registered = {
        "post_init": None,
        "post_stop": None,
        "post_shutdown": None,
    }
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class FakeApplication:
        bot = object()

        def add_handler(self, _handler):
            pass

        def add_error_handler(self, _handler):
            pass

        def run_polling(self):
            pass

    class FakeBuilder:
        def token(self, _token):
            return self

        def concurrent_updates(self, _updates):
            return self

        def connection_pool_size(self, _size):
            return self

        def media_write_timeout(self, _timeout):
            return self

        def post_init(self, callback):
            registered["post_init"] = callback
            return self

        def post_stop(self, callback):
            registered["post_stop"] = callback
            return self

        def post_shutdown(self, callback):
            registered["post_shutdown"] = callback
            return self

        def build(self):
            return FakeApplication()

    async def slow_migration_announcement(_bot, _state_store, *, target_username):
        assert target_username == "igclipbot"
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_wiring.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.post_deploy_notifications.send_bot_migration_announcement_once",
        slow_migration_announcement,
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", "igclipbot")

    bot = telegram_bot_factory()
    bot.run()

    await registered["post_init"](bot.application)
    await asyncio.wait_for(started.wait(), timeout=1)

    assert registered["post_stop"] is not None
    assert registered["post_shutdown"] is not None

    await registered["post_stop"](bot.application)

    assert cancelled.is_set()
