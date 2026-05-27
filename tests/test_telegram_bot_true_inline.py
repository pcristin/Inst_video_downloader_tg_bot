from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.inline_access import build_one_time_payload
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
    def __init__(
        self,
        *,
        inline_query=None,
        chosen_inline_result=None,
        callback_query=None,
        message=None,
        user_id=1001,
    ):
        self.inline_query = inline_query
        self.chosen_inline_result = chosen_inline_result
        self.callback_query = callback_query
        self.message = message
        self.effective_chat = getattr(message, "chat", SimpleNamespace(id=1, type="private")) if message else None
        self.effective_user = SimpleNamespace(id=user_id, username="alice", full_name="Alice")


class _FakeMessage:
    def __init__(
        self,
        text: str = "",
        *,
        chat_id: int = 1,
        message_id: int = 10,
        forward_from=None,
        forward_origin=None,
        successful_payment=None,
    ):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.message_id = message_id
        self.forward_from = forward_from
        self.forward_origin = forward_origin
        self.successful_payment = successful_payment
        self.replies = []

    async def reply_text(self, text: str):
        self.replies.append(text)
        return SimpleNamespace(edit_text=lambda _text: None, delete=lambda: None)


class _FakeTelegramBot:
    def __init__(self):
        self.refunds = []

    async def refund_star_payment(self, *, user_id: int, telegram_payment_charge_id: str):
        self.refunds.append(
            {"user_id": user_id, "telegram_payment_charge_id": telegram_payment_charge_id}
        )


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


@pytest.mark.asyncio
async def test_missing_inline_storage_marks_session_failed_and_refunds(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
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
    store.attach_inline_message("s1", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)
    refund_calls = []

    async def fake_refund(context, *, payment_id, user_id, reason):
        refund_calls.append({"payment_id": payment_id, "user_id": user_id, "reason": reason})

    bot._refund_one_time_payment = fake_refund
    fake_telegram_bot = SimpleNamespace(edit_message_text=lambda **kwargs: None)

    async def edit_message_text(**kwargs):
        fake_telegram_bot.edited_text = kwargs

    fake_telegram_bot.edit_message_text = edit_message_text

    await bot._deliver_inline_session(
        SimpleNamespace(bot=fake_telegram_bot),
        session_token="s1",
        one_time_payment_id="payment-1",
    )

    assert store.get_inline_session("s1")["status"] == "failed"
    assert refund_calls == [{"payment_id": "payment-1", "user_id": 1001, "reason": "inline_storage_missing"}]
    assert fake_telegram_bot.edited_text == {
        "inline_message_id": "inline-msg",
        "text": "Inline delivery is not configured. Set INLINE_STORAGE_CHAT_ID.",
    }


@pytest.mark.asyncio
async def test_chosen_and_callback_paths_schedule_inline_delivery_once(monkeypatch, tmp_path):
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
    callback_query = _FakeCallbackQuery("inline:s1", "inline-msg")
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult("inline:s1", "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )
    await bot.inline_callback_handler(
        _FakeUpdate(callback_query=callback_query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert len(scheduled) == 1
    assert callback_query.answers == [{"text": "Already preparing media."}]
    session = store.get_inline_session("s1", user_id=1001)
    assert session["inline_message_id"] == "inline-msg"


@pytest.mark.asyncio
async def test_owner_can_whitelist_user_by_id(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    message = _FakeMessage("/inline_whitelist add 1001")

    await bot.inline_whitelist_command(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(args=["add", "1001"]),
    )

    assert bot.state_store.is_inline_whitelisted(1001) is True


@pytest.mark.asyncio
async def test_owner_can_whitelist_forwarded_visible_user(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    forwarded_user = SimpleNamespace(id=1001)
    message = _FakeMessage("please add this user", forward_from=forwarded_user)

    await bot.handle_message(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert bot.state_store.is_inline_whitelisted(1001) is True


@pytest.mark.asyncio
async def test_owner_can_change_subscription_price(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    message = _FakeMessage("/inline_price subscription 5")

    await bot.inline_price_command(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(args=["subscription", "5"]),
    )

    assert bot.state_store.get_inline_runtime_settings()["subscription_stars"] == 5


@pytest.mark.asyncio
async def test_owner_can_enable_one_time_payment(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    message = _FakeMessage("/inline_onetime on 5")

    await bot.inline_onetime_command(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(args=["on", "5"]),
    )

    runtime = bot.state_store.get_inline_runtime_settings()
    assert runtime["one_time_enabled"] is True
    assert runtime["one_time_stars"] == 5


@pytest.mark.asyncio
async def test_one_time_failure_refunds_payment(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=5)
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
    fake_bot = _FakeTelegramBot()
    successful_payment = SimpleNamespace(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="s1"),
        currency="XTR",
        total_amount=5,
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
    )
    message = _FakeMessage(successful_payment=successful_payment)

    await bot.successful_payment_handler(
        _FakeUpdate(message=message, user_id=1001),
        SimpleNamespace(bot=fake_bot),
    )

    assert fake_bot.refunds == [{"user_id": 1001, "telegram_payment_charge_id": "tg-charge"}]
