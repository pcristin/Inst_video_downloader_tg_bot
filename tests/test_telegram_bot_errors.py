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
    registered = {"error_handler": None, "message_handler": None, "ran": False}

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

        def build(self):
            return FakeApplication()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.ApplicationBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(settings, "BOT_TOKEN", "test-token")

    bot = TelegramBot()
    bot.run()

    assert registered["message_handler"] is not None
    assert registered["error_handler"] == bot._global_error_handler
    assert registered["ran"] is True
