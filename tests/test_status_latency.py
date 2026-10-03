import asyncio

import pytest

from src.instagram_video_bot.services.telegram_status import edit_status_message
from src.instagram_video_bot.config.settings import settings


@pytest.mark.asyncio
async def test_stuck_cosmetic_edit_has_small_budget(monkeypatch):
    monkeypatch.setattr(
        settings, "TELEGRAM_STATUS_TIMEOUT_SECONDS", 0.01, raising=False
    )

    class Message:
        async def edit_text(self, *args, **kwargs):
            await asyncio.Event().wait()

    await asyncio.wait_for(edit_status_message(Message(), "preparing"), timeout=0.2)
