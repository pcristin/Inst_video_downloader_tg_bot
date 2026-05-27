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
        "message_handler": None,
        "post_init": None,
        "ran": False,
    }

    class FakeApplication:
        def add_handler(self, handler):
            registered["message_handler"] = handler

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

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.ApplicationBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")

    bot = TelegramBot()
    bot.run()

    assert registered["message_handler"] is not None
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

    bot = TelegramBot()
    bot.run()

    await registered["post_init"](bot.application)

    assert registered["scheduled"] is not None
    registered["scheduled"].close()
