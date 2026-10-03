"""Shutdown bookkeeping must finish before waiting for losing workers."""

import asyncio
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services import (
    instagram_delivery_race,
    post_deploy_notifications,
    telegram_wiring,
)


@pytest.mark.asyncio
async def test_notifications_cancelled_before_race_cleanup(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def notify(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def diagnose(*args):
        pass

    async def drain():
        assert stopped.is_set()

    class Builder:
        def post_init(self, callback):
            self.initialize = callback
            return self

        def post_stop(self, callback):
            self.stop = callback
            return self

        def post_shutdown(self, callback):
            return self

    monkeypatch.setattr(telegram_wiring.settings, 'INLINE_MODE_ENABLED', False)
    monkeypatch.setattr(telegram_wiring.settings, 'BOT_MIGRATION_TARGET_USERNAME', 'test')
    monkeypatch.setattr(telegram_wiring, '_diagnose_group_privacy', diagnose)
    monkeypatch.setattr(post_deploy_notifications, 'send_bot_migration_announcement_once', notify)
    monkeypatch.setattr(instagram_delivery_race, 'drain_race_cleanup', drain)
    builder = telegram_wiring._configure_post_init(Builder(), SimpleNamespace(state_store=None))
    application = SimpleNamespace(bot=None)
    await builder.initialize(application)
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(builder.stop(application), 1)
    finally:
        # Also reap the notification if the ordering assertion failed.
        async def no_drain():
            pass
        monkeypatch.setattr(instagram_delivery_race, 'drain_race_cleanup', no_drain)
        await asyncio.wait_for(builder.stop(application), 1)
