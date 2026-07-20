import asyncio
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import TelegramBot


class _StatusMessage:
    def __init__(self, message_id=501):
        self.message_id = message_id
        self.edits = []
        self.replies = []
        self.deleted = False

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class _Message:
    def __init__(self, text):
        self.text = text
        self.caption = None
        self.message_id = 101
        self.replies = []
        self.status_messages = []

    async def reply_text(self, text, **kwargs):
        status = _StatusMessage()
        self.replies.append((text, kwargs))
        self.status_messages.append(status)
        return status


class _Update:
    def __init__(self, text, *, chat_id=77, user_id=1001):
        self.edited_message = None
        self.edited_channel_post = None
        self.edited_business_message = None
        self.callback_query = None
        self.effective_message = _Message(text)
        self.message = self.effective_message
        self.effective_chat = SimpleNamespace(id=chat_id, type="private")
        self.effective_user = SimpleNamespace(
            id=user_id,
            username="alice",
            full_name="Alice",
            language_code="en",
        )


class _Context:
    def __init__(self):
        self.bot = SimpleNamespace()


class _CallbackQuery:
    def __init__(self, data, message, *, user_id=1001):
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(
            id=user_id,
            username="alice",
            full_name="Alice",
            language_code="en",
        )
        self.answers = []

    async def answer(self, text=None, *, show_alert=False):
        self.answers.append((text, show_alert))


class _CallbackUpdate:
    def __init__(self, query, *, chat_id=77):
        self.callback_query = query
        self.effective_chat = SimpleNamespace(id=chat_id, type="private")
        self.effective_user = query.from_user
        self.effective_message = query.message
        self.message = None


@pytest.mark.asyncio
async def test_initial_status_has_cancel_keyboard(monkeypatch, tmp_path):
    bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    release = asyncio.Event()

    async def execute(_job):
        await release.wait()
        return SimpleNamespace(media_items=[], from_cache=True)

    monkeypatch.setattr(bot, "_build_job_executor", lambda *_args: execute)
    update = _Update("https://x.com/example/status/123")

    await bot.handle_message(update, _Context())

    request_id = next(iter(bot.request_contexts))
    reply_markup = update.message.replies[0][1]["reply_markup"]
    button = reply_markup.inline_keyboard[0][0]
    assert button.text == "Cancel"
    assert button.callback_data == f"job:cancel:{request_id}"

    request_tasks = list(bot.active_request_tasks.values())
    bot.job_manager.cancel_request(request_id)
    release.set()
    await asyncio.wait_for(
        asyncio.gather(*request_tasks, return_exceptions=True),
        timeout=1,
    )


async def _submit_waiting_request(bot, monkeypatch):
    release = asyncio.Event()

    async def execute(_job):
        await release.wait()
        return SimpleNamespace(media_items=[], from_cache=True)

    monkeypatch.setattr(bot, "_build_job_executor", lambda *_args: execute)
    update = _Update("https://x.com/example/status/123")
    await bot.handle_message(update, _Context())
    request_id = next(iter(bot.request_contexts))
    status = update.message.status_messages[0]
    return request_id, status, release


@pytest.mark.asyncio
async def test_cancel_job_action_cancels_owned_request(monkeypatch, tmp_path):
    bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    request_id, status, release = await _submit_waiting_request(bot, monkeypatch)
    request_tasks = list(bot.active_request_tasks.values())
    query = _CallbackQuery(f"job:cancel:{request_id}", status)

    await bot.job_action_callback_handler(_CallbackUpdate(query), _Context())

    row = bot.state_store.get_request_for_action(request_id)
    assert row is not None
    assert row["status"] == "cancelled"
    assert query.answers == [("Request cancelled.", False)]
    assert status.edits[-1] == ("Request cancelled.", {"reply_markup": None})

    release.set()
    await asyncio.wait_for(
        asyncio.gather(*request_tasks, return_exceptions=True), timeout=1
    )


@pytest.mark.asyncio
async def test_cancel_job_action_rejects_another_user(monkeypatch, tmp_path):
    bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    request_id, status, release = await _submit_waiting_request(bot, monkeypatch)
    request_tasks = list(bot.active_request_tasks.values())
    query = _CallbackQuery(
        f"job:cancel:{request_id}",
        status,
        user_id=2002,
    )

    await bot.job_action_callback_handler(_CallbackUpdate(query), _Context())

    row = bot.state_store.get_request_for_action(request_id)
    assert row is not None
    assert row["status"] in {"queued", "running"}
    assert query.answers == [("This action belongs to another request.", True)]

    bot.job_manager.cancel_request(request_id)
    release.set()
    await asyncio.wait_for(
        asyncio.gather(*request_tasks, return_exceptions=True), timeout=1
    )


def _persist_failed_request(store, *, retryable=True, request_id="failed-request"):
    normalized_url = "https://x.com/example/status/123"
    store.create_job("failed-job", 77, normalized_url, "twitter", "failed")
    store.create_request(
        request_id=request_id,
        job_id="failed-job",
        chat_id=77,
        user_id=1001,
        user_label="alice",
        provider="twitter",
        normalized_url=normalized_url,
        status="failed",
    )
    store.update_request_status(
        request_id,
        "failed",
        failure_reason="provider_timeout",
        retryable=retryable,
    )


@pytest.mark.asyncio
async def test_retry_action_uses_persisted_request_after_restart(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    _persist_failed_request(store)
    bot = TelegramBot(state_store=store)
    release = asyncio.Event()

    async def execute(_job):
        await release.wait()
        return SimpleNamespace(media_items=[], from_cache=True)

    monkeypatch.setattr(bot, "_build_job_executor", lambda *_args: execute)
    status = _StatusMessage()
    query = _CallbackQuery("job:retry:failed-request", status)

    await bot.job_action_callback_handler(_CallbackUpdate(query), _Context())

    with store._lock:
        retry = store._conn.execute(
            """
            SELECT request_id, normalized_url, retry_of_request_id
            FROM request_events
            WHERE retry_of_request_id = 'failed-request'
            """
        ).fetchone()
    assert retry is not None
    assert retry["normalized_url"] == "https://x.com/example/status/123"
    assert query.answers == [("Retry started.", False)]
    markup = status.edits[-1][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == (
        f"job:cancel:{retry['request_id']}"
    )

    request_tasks = list(bot.active_request_tasks.values())
    bot.job_manager.cancel_request(retry["request_id"])
    release.set()
    await asyncio.wait_for(
        asyncio.gather(*request_tasks, return_exceptions=True), timeout=1
    )


@pytest.mark.asyncio
async def test_retry_action_rejects_non_retryable_failure(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _persist_failed_request(store, retryable=False)
    bot = TelegramBot(state_store=store)
    query = _CallbackQuery("job:retry:failed-request", _StatusMessage())

    await bot.job_action_callback_handler(_CallbackUpdate(query), _Context())

    with store._lock:
        request_count = store._conn.execute(
            "SELECT COUNT(*) AS count FROM request_events"
        ).fetchone()["count"]
    assert request_count == 1
    assert query.answers == [("This request cannot be retried safely.", True)]
