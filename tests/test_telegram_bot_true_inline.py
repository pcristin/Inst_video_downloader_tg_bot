from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import TelegramBot


class _FakeInlineQuery:
    def __init__(self, query: str, user_id: int = 1001):
        self.query = query
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, results, cache_time=0, is_personal=True, **kwargs):
        self.answers.append({"results": results, "cache_time": cache_time, "is_personal": is_personal, **kwargs})


class _FakeChosenInlineResult:
    def __init__(self, result_id: str, inline_message_id: str, user_id: int = 1001):
        self.result_id = result_id
        self.inline_message_id = inline_message_id
        self.from_user = SimpleNamespace(id=user_id)


class _FakeCallbackQuery:
    def __init__(self, data: str, inline_message_id: str, user_id: int = 1001):
        self.data = data
        self.inline_message_id = inline_message_id
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append({"text": text, **kwargs})


class _FakeUpdate:
    def __init__(self, *, inline_query=None, chosen_inline_result=None, callback_query=None, user_id=1001):
        self.inline_query = inline_query
        self.chosen_inline_result = chosen_inline_result
        self.callback_query = callback_query
        self.effective_user = SimpleNamespace(id=user_id, username="alice", full_name="Alice")


@pytest.mark.asyncio
async def test_paid_user_inline_query_returns_placeholder_with_keyboard(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.add_inline_whitelist_user(1001, added_by_user_id=42)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=SimpleNamespace()))

    result = query.answers[0]["results"][0]
    assert result.title == "Send media here"
    assert result.reply_markup is not None


@pytest.mark.asyncio
async def test_chosen_inline_result_attaches_inline_message_id(tmp_path):
    store = StateStore(tmp_path / "state.db")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=expires_at,
    )
    bot = TelegramBot(state_store=store)

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult("inline:s1", "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert store.get_inline_session("s1", user_id=1001)["inline_message_id"] == "inline-msg"


@pytest.mark.asyncio
async def test_expired_chosen_inline_result_does_not_attach_or_schedule(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=expires_at,
    )
    bot = TelegramBot(state_store=store)
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult("inline:s1", "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    session = store.get_inline_session("s1", user_id=1001)
    assert session["inline_message_id"] is None
    assert scheduled == []


@pytest.mark.asyncio
async def test_expired_inline_callback_answers_expired_without_attach_or_schedule(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=expires_at,
    )
    bot = TelegramBot(state_store=store)
    callback_query = _FakeCallbackQuery("inline:s1", "inline-msg")
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.inline_callback_handler(
        _FakeUpdate(callback_query=callback_query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    session = store.get_inline_session("s1", user_id=1001)
    assert session["inline_message_id"] is None
    assert callback_query.answers == [{"text": "This inline request expired."}]
    assert scheduled == []
