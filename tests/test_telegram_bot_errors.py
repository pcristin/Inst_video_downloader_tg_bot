import asyncio
from types import SimpleNamespace

import pytest
from telegram.error import NetworkError

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.telegram_bot import TelegramBot


@pytest.mark.asyncio
async def test_global_error_handler_handles_network_error():
    bot = TelegramBot()
    context = SimpleNamespace(error=NetworkError("Bad Gateway"))

    await bot._global_error_handler(update=None, context=context)


def test_run_registers_global_error_handler(monkeypatch):
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

        def build(self):
            return FakeApplication()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)

    bot = TelegramBot()
    bot.run()

    admin_handler_contract = [
        ("admin_help_command", "CommandHandler"),
    ]
    inline_handler_contract = [
        ("inline_query_handler", "InlineQueryHandler"),
        ("chosen_inline_result_handler", "ChosenInlineResultHandler"),
        ("inline_callback_handler", "CallbackQueryHandler"),
        ("pre_checkout_handler", "PreCheckoutQueryHandler"),
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
    assert registered["error_handler"] == bot._global_error_handler
    assert registered["post_init"] is not None
    assert registered["ran"] is True


@pytest.mark.asyncio
async def test_inline_announcement_post_init_schedules_background_task(monkeypatch):
    registered = {
        "post_init": None,
        "scheduled": None,
    }

    class FakeApplication:
        bot = object()

        def add_handler(self, _handler):
            pass

        def add_error_handler(self, _handler):
            pass

        def create_task(self, coroutine):
            registered["scheduled"] = coroutine
            return SimpleNamespace()

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

        def build(self):
            return FakeApplication()

    async def slow_announcement(_bot, _state_store):
        await asyncio.sleep(999)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.post_deploy_notifications.send_inline_mode_announcement_once",
        slow_announcement,
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", None)

    bot = TelegramBot()
    bot.run()

    await registered["post_init"](bot.application)

    assert registered["scheduled"] is not None
    registered["scheduled"].close()


def test_legacy_redirect_mode_registers_only_redirect_handlers(monkeypatch):
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

        def build(self):
            return FakeApplication()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "BOT_LEGACY_REDIRECT_MODE", True)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", "igclipbot")

    bot = TelegramBot()
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


def test_inline_announcement_post_init_is_not_registered_without_storage(monkeypatch):
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

        def build(self):
            return FakeApplication()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", None)

    bot = TelegramBot()
    bot.run()

    assert registered["post_init"] is None


@pytest.mark.asyncio
async def test_migration_announcement_post_init_registers_without_inline_storage(
    monkeypatch,
):
    registered = {
        "post_init": None,
        "scheduled": None,
    }

    class FakeApplication:
        bot = object()

        def add_handler(self, _handler):
            pass

        def add_error_handler(self, _handler):
            pass

        def create_task(self, coroutine):
            registered["scheduled"] = coroutine
            return SimpleNamespace()

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

        def build(self):
            return FakeApplication()

    async def slow_migration_announcement(_bot, _state_store, *, target_username):
        assert target_username == "igclipbot"
        await asyncio.sleep(999)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.ApplicationBuilder",
        lambda: FakeBuilder(),
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.post_deploy_notifications.send_bot_migration_announcement_once",
        slow_migration_announcement,
    )
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", "igclipbot")

    bot = TelegramBot()
    bot.run()

    await registered["post_init"](bot.application)

    assert registered["scheduled"] is not None
    registered["scheduled"].close()
