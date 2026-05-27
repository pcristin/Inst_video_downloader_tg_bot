import pytest
from telegram.error import Forbidden, TelegramError

from src.instagram_video_bot.services.post_deploy_notifications import (
    INLINE_MODE_ANNOUNCEMENT_KEY,
    INLINE_PROMO_REFUND_ANNOUNCEMENT_KEY,
    INLINE_PROMO_REFUND_ANNOUNCEMENT_TEXT,
    send_inline_mode_announcement_once,
    send_inline_promo_refund_announcement_once,
)
from src.instagram_video_bot.services.state_store import StateStore


class _FakeBot:
    def __init__(self, forbidden_user_ids=None, telegram_error_user_ids=None):
        self.messages = []
        self.forbidden_user_ids = set(forbidden_user_ids or [])
        self.telegram_error_user_ids = set(telegram_error_user_ids or [])

    async def send_message(self, **kwargs):
        if kwargs["chat_id"] in self.forbidden_user_ids:
            raise Forbidden("bot cannot initiate conversation")
        if kwargs["chat_id"] in self.telegram_error_user_ids:
            raise TelegramError("temporary telegram failure")
        self.messages.append(kwargs)


class _AttemptAwareBot:
    def __init__(self, store):
        self.store = store
        self.messages = []

    async def send_message(self, **kwargs):
        assert self.store.notification_was_attempted(
            INLINE_MODE_ANNOUNCEMENT_KEY,
            kwargs["chat_id"],
        )
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
async def test_announcement_records_attempt_before_sending(tmp_path):
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
    fake_bot = _AttemptAwareBot(store)

    result = await send_inline_mode_announcement_once(fake_bot, store)

    assert result == {"sent": 1, "failed": 0, "skipped": 0}
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


@pytest.mark.asyncio
async def test_announcement_records_generic_telegram_errors_and_continues(tmp_path):
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
        1002,
        "@bob",
        "twitter",
        "https://x.com/a/status/1",
        "completed",
    )

    fake_bot = _FakeBot(telegram_error_user_ids={1001})
    result = await send_inline_mode_announcement_once(fake_bot, store, pause_seconds=0)

    assert result == {"sent": 1, "failed": 1, "skipped": 0}
    assert [message["chat_id"] for message in fake_bot.messages] == [1002]
    assert store.notification_was_attempted(INLINE_MODE_ANNOUNCEMENT_KEY, 1001) is True
    assert store.notification_was_sent(INLINE_MODE_ANNOUNCEMENT_KEY, 1002) is True


@pytest.mark.asyncio
async def test_announcement_retries_generic_telegram_errors_on_later_run(tmp_path):
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

    first = await send_inline_mode_announcement_once(
        _FakeBot(telegram_error_user_ids={1001}),
        store,
        pause_seconds=0,
    )
    healthy_bot = _FakeBot()
    second = await send_inline_mode_announcement_once(healthy_bot, store, pause_seconds=0)

    assert first == {"sent": 0, "failed": 1, "skipped": 0}
    assert second == {"sent": 1, "failed": 0, "skipped": 0}
    assert [message["chat_id"] for message in healthy_bot.messages] == [1001]
    assert store.notification_was_sent(INLINE_MODE_ANNOUNCEMENT_KEY, 1001) is True


@pytest.mark.asyncio
async def test_promo_refund_announcement_sends_once_with_promo_copy(tmp_path):
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

    first = await send_inline_promo_refund_announcement_once(fake_bot, store, pause_seconds=0)
    second = await send_inline_promo_refund_announcement_once(fake_bot, store, pause_seconds=0)

    assert first == {"sent": 1, "failed": 0, "skipped": 0}
    assert second == {"sent": 0, "failed": 0, "skipped": 1}
    assert len(fake_bot.messages) == 1
    assert "first 3 successful inline downloads are free" in INLINE_PROMO_REFUND_ANNOUNCEMENT_TEXT
    assert store.notification_was_sent(INLINE_PROMO_REFUND_ANNOUNCEMENT_KEY, 1001) is True
