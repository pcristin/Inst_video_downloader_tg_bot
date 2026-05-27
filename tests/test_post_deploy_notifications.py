import pytest
from telegram.error import Forbidden

from src.instagram_video_bot.services.post_deploy_notifications import (
    INLINE_MODE_ANNOUNCEMENT_KEY,
    send_inline_mode_announcement_once,
)
from src.instagram_video_bot.services.state_store import StateStore


class _FakeBot:
    def __init__(self, forbidden_user_ids=None):
        self.messages = []
        self.forbidden_user_ids = set(forbidden_user_ids or [])

    async def send_message(self, **kwargs):
        if kwargs["chat_id"] in self.forbidden_user_ids:
            raise Forbidden("bot cannot initiate conversation")
        self.messages.append(kwargs)


def test_distinct_historical_users_are_read_from_request_events(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_job("job-1", 77, "https://x.com/a/status/1", "twitter", "queued")
    store.create_request(
        "req-1",
        "job-1",
        77,
        1001,
        "@alice",
        "twitter",
        "https://x.com/a/status/1",
        "completed",
    )
    store.create_request(
        "req-2",
        "job-1",
        77,
        1001,
        "@alice",
        "twitter",
        "https://x.com/a/status/1",
        "completed",
    )
    store.create_request(
        "req-3",
        "job-1",
        77,
        1002,
        "@bob",
        "twitter",
        "https://x.com/a/status/1",
        "completed",
    )

    assert store.list_distinct_request_user_ids() == [1001, 1002]


@pytest.mark.asyncio
async def test_announcement_sends_once_and_records_successes(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_job("job-1", 77, "https://x.com/a/status/1", "twitter", "queued")
    store.create_request(
        "req-1",
        "job-1",
        77,
        1001,
        "@alice",
        "twitter",
        "https://x.com/a/status/1",
        "completed",
    )
    fake_bot = _FakeBot()

    first = await send_inline_mode_announcement_once(fake_bot, store)
    second = await send_inline_mode_announcement_once(fake_bot, store)

    assert first == {"sent": 1, "failed": 0, "skipped": 0}
    assert second == {"sent": 0, "failed": 0, "skipped": 1}
    assert len(fake_bot.messages) == 1
    assert store.notification_was_sent(INLINE_MODE_ANNOUNCEMENT_KEY, 1001) is True


@pytest.mark.asyncio
async def test_announcement_records_forbidden_failures(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_job("job-1", 77, "https://x.com/a/status/1", "twitter", "queued")
    store.create_request(
        "req-1",
        "job-1",
        77,
        1001,
        "@alice",
        "twitter",
        "https://x.com/a/status/1",
        "completed",
    )

    result = await send_inline_mode_announcement_once(
        _FakeBot(forbidden_user_ids={1001}), store
    )

    assert result == {"sent": 0, "failed": 1, "skipped": 0}
    assert store.notification_was_attempted(INLINE_MODE_ANNOUNCEMENT_KEY, 1001) is True
