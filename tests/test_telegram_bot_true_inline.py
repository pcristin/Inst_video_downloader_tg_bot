from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telegram import InputInvoiceMessageContent
from telegram.error import TelegramError

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo
from src.instagram_video_bot.services.inline_access import build_one_time_payload, build_subscription_payload
from src.instagram_video_bot.services.inline_delivery import InlineCachedMediaItem
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


class _FakePreCheckoutQuery:
    def __init__(
        self,
        *,
        invoice_payload: str,
        total_amount: int,
        user_id: int = 1001,
        currency: str = "XTR",
    ):
        self.invoice_payload = invoice_payload
        self.total_amount = total_amount
        self.currency = currency
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, **kwargs):
        self.answers.append(kwargs)


class _FakeUpdate:
    def __init__(
        self,
        *,
        inline_query=None,
        chosen_inline_result=None,
        callback_query=None,
        pre_checkout_query=None,
        message=None,
        user_id=1001,
    ):
        self.inline_query = inline_query
        self.chosen_inline_result = chosen_inline_result
        self.callback_query = callback_query
        self.pre_checkout_query = pre_checkout_query
        self.message = message
        self.effective_message = message
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


class _FailingRefundTelegramBot:
    async def refund_star_payment(self, *, user_id: int, telegram_payment_charge_id: str):
        raise TelegramError("refund failed")


class _FakeInvoiceLinkBot:
    def __init__(self):
        self.invoice_link_calls = []

    async def create_invoice_link(self, **kwargs):
        self.invoice_link_calls.append(kwargs)
        return "https://t.me/invoice-link"


class _FailingInvoiceLinkBot:
    async def create_invoice_link(self, **kwargs):
        raise TelegramError("invoice link failed")


@pytest.mark.asyncio
async def test_start_command_delegates_to_command_handlers(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate(message=_FakeMessage("/start"))
    context = SimpleNamespace(bot=_FakeTelegramBot())
    calls = []

    class FakeCommandHandlers:
        async def start_command(self, received_update, received_context):
            calls.append((received_update, received_context))

    telegram_bot.command_handlers = FakeCommandHandlers()

    await telegram_bot.start_command(update, context)

    assert calls == [(update, context)]


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
async def test_inline_query_without_storage_does_not_offer_paid_invoice(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", None)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=2)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert len(query.answers[0]["results"]) == 1
    result = query.answers[0]["results"][0]
    assert result.id == "inline-storage-missing"
    assert result.title == "Inline delivery is not configured"
    assert store.get_inline_one_time_payment_by_charge_id("tg-charge") is None


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
async def test_paid_subscription_chosen_inline_result_does_not_attach_or_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=2)
    for _ in range(settings.INLINE_FREE_SUCCESSFUL_DELIVERIES):
        store.record_inline_promo_success(1001)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=_FakeInvoiceLinkBot()))
    paid_result = query.answers[0]["results"][0]
    session_token = paid_result.id.split(":", 1)[1]

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult(paid_result.id, "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert store.get_inline_session(session_token, user_id=1001)["inline_message_id"] is None
    assert scheduled == []


@pytest.mark.asyncio
async def test_paid_one_time_chosen_inline_result_does_not_attach_or_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=2)
    for _ in range(settings.INLINE_FREE_SUCCESSFUL_DELIVERIES):
        store.record_inline_promo_success(1001)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=_FakeInvoiceLinkBot()))
    paid_result = query.answers[0]["results"][1]
    session_token = paid_result.id.split(":", 1)[1]

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult(paid_result.id, "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert store.get_inline_session(session_token, user_id=1001)["inline_message_id"] is None
    assert scheduled == []


@pytest.mark.asyncio
async def test_paid_subscription_inline_result_uses_subscription_invoice_link(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(subscription_stars=5)
    for _ in range(settings.INLINE_FREE_SUCCESSFUL_DELIVERIES):
        store.record_inline_promo_success(1001)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    fake_bot = _FakeInvoiceLinkBot()

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=fake_bot))

    result = query.answers[0]["results"][0]
    assert result.id.startswith("sub:")
    assert not isinstance(result.input_message_content, InputInvoiceMessageContent)
    assert fake_bot.invoice_link_calls[0]["subscription_period"] == settings.INLINE_SUBSCRIPTION_PERIOD_SECONDS
    assert fake_bot.invoice_link_calls[0]["currency"] == "XTR"
    assert fake_bot.invoice_link_calls[0]["prices"][0].amount == 5
    assert result.reply_markup.inline_keyboard[0][0].url == "https://t.me/invoice-link"


@pytest.mark.asyncio
async def test_paid_subscription_invoice_link_failure_returns_safe_inline_result(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    for _ in range(settings.INLINE_FREE_SUCCESSFUL_DELIVERIES):
        store.record_inline_promo_success(1001)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")

    await bot.inline_query_handler(
        _FakeUpdate(inline_query=query),
        SimpleNamespace(bot=_FailingInvoiceLinkBot()),
    )

    result = query.answers[0]["results"][0]
    session_count = store._conn.execute("SELECT COUNT(*) AS count FROM inline_sessions").fetchone()["count"]
    assert result.id == "inline-payment-unavailable"
    assert result.title == "Inline payments are temporarily unavailable"
    assert session_count == 0


@pytest.mark.asyncio
async def test_inline_query_allows_first_three_successful_inline_deliveries_free(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    for _ in range(settings.INLINE_FREE_SUCCESSFUL_DELIVERIES - 1):
        store.record_inline_promo_success(1001)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=SimpleNamespace()))

    result = query.answers[0]["results"][0]
    session_token = result.id.split(":", 1)[1]
    session = store.get_inline_session(session_token, user_id=1001)
    assert result.title == "Send media here"
    assert session["access_kind"] == "promo"


@pytest.mark.asyncio
async def test_inline_query_requires_payment_after_three_successful_free_deliveries(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    for _ in range(settings.INLINE_FREE_SUCCESSFUL_DELIVERIES):
        store.record_inline_promo_success(1001)
    bot = TelegramBot(state_store=store)
    query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    fake_bot = _FakeInvoiceLinkBot()

    await bot.inline_query_handler(_FakeUpdate(inline_query=query), SimpleNamespace(bot=fake_bot))

    result = query.answers[0]["results"][0]
    assert result.id.startswith("sub:")
    assert result.title.startswith("Subscribe for ")


@pytest.mark.asyncio
async def test_free_chosen_inline_result_still_schedules_delivery(monkeypatch, tmp_path):
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
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult("inline:s1", "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert store.get_inline_session("s1", user_id=1001)["inline_message_id"] == "inline-msg"
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_chosen_inline_result_over_user_rate_limit_edits_placeholder(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "USER_RATE_LIMIT_REQUESTS", 1)
    monkeypatch.setattr(settings, "USER_RATE_LIMIT_WINDOW_SECONDS", 600)
    store = StateStore(tmp_path / "state.db")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    for session_token in ("s1", "s2"):
        store.create_inline_session(
            session_token=session_token,
            user_id=1001,
            original_url=f"https://x.com/example/status/{session_token}",
            normalized_url=f"https://x.com/example/status/{session_token}",
            provider="twitter",
            provider_label="Twitter/X",
            expires_at=expires_at,
            access_kind="promo",
        )
    bot = TelegramBot(state_store=store)
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    edits = []

    async def edit_message_text(**kwargs):
        edits.append(kwargs)

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.asyncio.create_task", fake_create_task)

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult("inline:s1", "inline-msg-1")),
        SimpleNamespace(bot=SimpleNamespace(edit_message_text=edit_message_text)),
    )
    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult("inline:s2", "inline-msg-2")),
        SimpleNamespace(bot=SimpleNamespace(edit_message_text=edit_message_text)),
    )

    assert len(scheduled) == 1
    assert store.get_inline_session("s2", user_id=1001)["status"] == "failed"
    assert edits == [
        {
            "inline_message_id": "inline-msg-2",
            "text": "Слишком много запросов. Попробуй снова примерно через 10 мин.",
        }
    ]


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
async def test_inline_delivery_removes_download_files_after_storage_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.attach_inline_message("s1", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)
    output_dir = settings.CACHE_DIR / "inline" / "s1"

    class FakeDownloader:
        async def download_video(self, original_url, target_dir):
            media_file = target_dir / "video.mp4"
            media_file.write_bytes(b"video")
            return VideoInfo(
                file_path=media_file,
                title="Title",
                media_items=[MediaItem(file_path=media_file, media_type="video", caption="Caption")],
                primary_media_type="video",
            )

    async def fake_upload(*args, **kwargs):
        return InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")

    fake_telegram_bot = SimpleNamespace(edited_media=None)

    async def edit_message_media(**kwargs):
        fake_telegram_bot.edited_media = kwargs

    fake_telegram_bot.edit_message_media = edit_message_media
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage", fake_upload)

    await bot._deliver_inline_session(
        SimpleNamespace(bot=fake_telegram_bot),
        session_token="s1",
        one_time_payment_id=None,
    )

    assert store.get_inline_session("s1")["status"] == "delivered"
    assert fake_telegram_bot.edited_media["inline_message_id"] == "inline-msg"
    assert output_dir.exists() is False


@pytest.mark.asyncio
async def test_inline_delivery_caches_and_edits_video_portrait_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.attach_inline_message("s1", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)

    class FakeDownloader:
        async def download_video(self, original_url, target_dir):
            media_file = target_dir / "portrait.mp4"
            media_file.write_bytes(b"video")
            return VideoInfo(
                file_path=media_file,
                title="Title",
                media_items=[
                    MediaItem(
                        file_path=media_file,
                        media_type="video",
                        caption="Caption",
                        duration=11.6,
                        width=720,
                        height=1280,
                    )
                ],
                primary_media_type="video",
            )

    async def fake_upload(*args, **kwargs):
        return InlineCachedMediaItem(
            media_type="video",
            file_id="video-file-id",
            caption="Caption",
            duration=11.6,
            width=720,
            height=1280,
        )

    fake_telegram_bot = SimpleNamespace(edited_media=None)

    async def edit_message_media(**kwargs):
        fake_telegram_bot.edited_media = kwargs

    fake_telegram_bot.edit_message_media = edit_message_media
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage", fake_upload)

    await bot._deliver_inline_session(
        SimpleNamespace(bot=fake_telegram_bot),
        session_token="s1",
        one_time_payment_id=None,
    )

    cached = store.get_inline_cached_media("instagram:https://www.instagram.com/reel/abc/")
    assert cached["media_items"][0]["width"] == 720
    assert cached["media_items"][0]["height"] == 1280
    assert cached["media_items"][0]["duration"] == 11.6

    media = fake_telegram_bot.edited_media["media"]
    assert media.width == 720
    assert media.height == 1280
    assert media._duration == timedelta(seconds=12)


@pytest.mark.asyncio
async def test_successful_promo_inline_delivery_consumes_lifetime_free_credit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="promo-session",
        user_id=1001,
        original_url="https://x.com/example/status/1",
        normalized_url="https://x.com/example/status/1",
        provider="twitter",
        provider_label="Twitter/X",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        access_kind="promo",
    )
    store.attach_inline_message("promo-session", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)

    class FakeDownloader:
        async def download_video(self, original_url, target_dir):
            media_file = target_dir / "video.mp4"
            media_file.write_bytes(b"video")
            return VideoInfo(
                file_path=media_file,
                title="Title",
                media_items=[MediaItem(file_path=media_file, media_type="video", caption="Caption")],
                primary_media_type="video",
            )

    async def fake_upload(*args, **kwargs):
        return InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")

    async def edit_message_media(**kwargs):
        return None

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage", fake_upload)

    await bot._deliver_inline_session(
        SimpleNamespace(bot=SimpleNamespace(edit_message_media=edit_message_media)),
        session_token="promo-session",
        one_time_payment_id=None,
    )

    assert store.get_inline_promo_success_count(1001) == 1


@pytest.mark.asyncio
async def test_subscription_inline_delivery_records_success_event(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    started_at = datetime.now(timezone.utc) - timedelta(days=1)
    expires_at = datetime.now(timezone.utc) + timedelta(days=29)
    store = StateStore(tmp_path / "state.db")
    store.record_inline_subscription(
        user_id=1001,
        expires_at=expires_at,
        telegram_payment_charge_id="sub-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=100,
        started_at=started_at,
    )
    store.create_inline_session(
        session_token="sub-session",
        user_id=1001,
        original_url="https://x.com/example/status/1",
        normalized_url="https://x.com/example/status/1",
        provider="twitter",
        provider_label="Twitter/X",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        access_kind="subscription",
    )
    store.attach_inline_message("sub-session", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)

    class FakeDownloader:
        async def download_video(self, original_url, target_dir):
            media_file = target_dir / "video.mp4"
            media_file.write_bytes(b"video")
            return VideoInfo(
                file_path=media_file,
                title="Title",
                media_items=[MediaItem(file_path=media_file, media_type="video", caption="Caption")],
                primary_media_type="video",
            )

    async def fake_upload(*args, **kwargs):
        return InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")

    async def edit_message_media(**kwargs):
        return None

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage", fake_upload)

    await bot._deliver_inline_session(
        SimpleNamespace(bot=SimpleNamespace(edit_message_media=edit_message_media)),
        session_token="sub-session",
        one_time_payment_id=None,
    )

    stats = store.get_subscription_delivery_stats(
        user_id=1001,
        started_at=started_at,
        expires_at=expires_at,
    )
    assert stats["success"] == 1
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_subscription_inline_delivery_records_failure_event(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    started_at = datetime.now(timezone.utc) - timedelta(days=1)
    expires_at = datetime.now(timezone.utc) + timedelta(days=29)
    store = StateStore(tmp_path / "state.db")
    store.record_inline_subscription(
        user_id=1001,
        expires_at=expires_at,
        telegram_payment_charge_id="sub-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=100,
        started_at=started_at,
    )
    store.create_inline_session(
        session_token="sub-session",
        user_id=1001,
        original_url="https://x.com/example/status/1",
        normalized_url="https://x.com/example/status/1",
        provider="twitter",
        provider_label="Twitter/X",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        access_kind="subscription",
    )
    store.attach_inline_message("sub-session", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)

    class FailingDownloader:
        async def download_video(self, original_url, target_dir):
            raise RuntimeError("provider failed")

    edits = []

    async def edit_message_text(**kwargs):
        edits.append(kwargs)

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader", FailingDownloader)

    await bot._deliver_inline_session(
        SimpleNamespace(bot=SimpleNamespace(edit_message_text=edit_message_text)),
        session_token="sub-session",
        one_time_payment_id=None,
    )

    stats = store.get_subscription_delivery_stats(
        user_id=1001,
        started_at=started_at,
        expires_at=expires_at,
    )
    assert stats["success"] == 0
    assert stats["failed"] == 1
    assert edits[0]["text"] == "Inline delivery failed. If this was a one-time payment, it was refunded."


@pytest.mark.asyncio
async def test_expired_subscription_auto_refunds_when_failures_reach_threshold(tmp_path):
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(days=31)
    expires_at = now - timedelta(days=1)
    store = StateStore(tmp_path / "state.db")
    store.record_inline_subscription(
        user_id=1001,
        expires_at=expires_at,
        telegram_payment_charge_id="sub-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=100,
        started_at=started_at,
    )
    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s1",
        access_kind="subscription",
        status="success",
        occurred_at=started_at + timedelta(days=1),
    )
    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s2",
        access_kind="subscription",
        status="failed",
        occurred_at=started_at + timedelta(days=2),
    )
    bot = TelegramBot(state_store=store)
    fake_bot = _FakeTelegramBot()

    await bot._evaluate_expired_inline_subscription_refunds(SimpleNamespace(bot=fake_bot))

    subscription = store.get_inline_subscription(1001)
    assert fake_bot.refunds == [{"user_id": 1001, "telegram_payment_charge_id": "sub-charge"}]
    assert subscription["status"] == "auto_refunded"
    assert subscription["refund_reason"] == "failure_rate:0.50"


@pytest.mark.asyncio
async def test_expired_subscription_below_threshold_is_marked_checked(tmp_path):
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(days=31)
    expires_at = now - timedelta(days=1)
    store = StateStore(tmp_path / "state.db")
    store.record_inline_subscription(
        user_id=1001,
        expires_at=expires_at,
        telegram_payment_charge_id="sub-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=100,
        started_at=started_at,
    )
    store.record_inline_delivery_event(
        user_id=1001,
        session_token="s1",
        access_kind="subscription",
        status="success",
        occurred_at=started_at + timedelta(days=1),
    )
    bot = TelegramBot(state_store=store)
    fake_bot = _FakeTelegramBot()

    await bot._evaluate_expired_inline_subscription_refunds(SimpleNamespace(bot=fake_bot))

    subscription = store.get_inline_subscription(1001)
    assert fake_bot.refunds == []
    assert subscription["status"] == "completed"
    assert subscription["refund_reason"] == "failure_rate:0.00"


@pytest.mark.asyncio
async def test_uncached_inline_delivery_runs_inside_job_manager_limits(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.attach_inline_message("s1", inline_message_id="inline-msg")
    bot = TelegramBot(state_store=store)
    limit_calls = []

    @asynccontextmanager
    async def fake_bounded_execution(**kwargs):
        limit_calls.append(kwargs)
        yield

    class FakeDownloader:
        async def download_video(self, original_url, target_dir):
            assert limit_calls
            media_file = target_dir / "video.mp4"
            media_file.write_bytes(b"video")
            return VideoInfo(
                file_path=media_file,
                title="Title",
                media_items=[MediaItem(file_path=media_file, media_type="video", caption="Caption")],
                primary_media_type="video",
            )

    async def fake_upload(*args, **kwargs):
        return InlineCachedMediaItem(media_type="video", file_id="video-file-id", caption="Caption")

    async def edit_message_media(**kwargs):
        return None

    bot.job_manager.bounded_execution = fake_bounded_execution
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage", fake_upload)

    await bot._deliver_inline_session(
        SimpleNamespace(bot=SimpleNamespace(edit_message_media=edit_message_media)),
        session_token="s1",
        one_time_payment_id=None,
    )

    assert limit_calls == [
        {
            "chat_id": 1001,
            "user_id": 1001,
            "provider": "instagram",
            "provider_label": "Instagram",
        }
    ]


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
async def test_owner_forwarded_visible_user_message_does_not_whitelist(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    forwarded_user = SimpleNamespace(id=1001)
    message = _FakeMessage("please add this user", forward_from=forwarded_user)

    await bot.handle_message(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert bot.state_store.is_inline_whitelisted(1001) is False
    assert message.replies == []


@pytest.mark.asyncio
async def test_owner_forwarded_visible_user_media_does_not_whitelist(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    forwarded_user = SimpleNamespace(id=1001)
    message = _FakeMessage(None, forward_from=forwarded_user)

    await bot.handle_message(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert bot.state_store.is_inline_whitelisted(1001) is False
    assert message.replies == []


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
async def test_non_owner_inline_whitelist_does_not_change_state(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    message = _FakeMessage("/inline_whitelist add 1001")

    await bot.inline_whitelist_command(
        _FakeUpdate(message=message, user_id=99),
        SimpleNamespace(args=["add", "1001"]),
    )

    assert bot.state_store.is_inline_whitelisted(1001) is False


@pytest.mark.asyncio
async def test_non_owner_inline_price_does_not_change_state(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    message = _FakeMessage("/inline_price subscription 5")

    await bot.inline_price_command(
        _FakeUpdate(message=message, user_id=99),
        SimpleNamespace(args=["subscription", "5"]),
    )

    assert bot.state_store.get_inline_runtime_settings()["subscription_stars"] == settings.INLINE_SUBSCRIPTION_STARS


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
async def test_non_owner_inline_onetime_does_not_change_state(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    message = _FakeMessage("/inline_onetime on 5")

    await bot.inline_onetime_command(
        _FakeUpdate(message=message, user_id=99),
        SimpleNamespace(args=["on", "5"]),
    )

    runtime = bot.state_store.get_inline_runtime_settings()
    assert runtime["one_time_enabled"] == settings.INLINE_ONE_TIME_ENABLED
    assert runtime["one_time_stars"] == settings.INLINE_ONE_TIME_STARS


@pytest.mark.asyncio
async def test_owner_can_refund_known_inline_subscription(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    store.record_inline_subscription(
        user_id=1001,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        telegram_payment_charge_id="sub-charge",
        provider_payment_charge_id="provider-charge",
        total_amount=100,
    )
    bot = TelegramBot(state_store=store)
    fake_bot = _FakeTelegramBot()
    message = _FakeMessage("/inline_refund sub-charge")

    await bot.inline_refund_command(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(args=["sub-charge"], bot=fake_bot),
    )

    assert fake_bot.refunds == [{"user_id": 1001, "telegram_payment_charge_id": "sub-charge"}]
    assert store.get_inline_subscription(1001)["status"] == "refunded"
    assert message.replies == ["Inline refund sent for user 1001."]


@pytest.mark.asyncio
async def test_owner_can_refund_known_one_time_payment(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="once-charge",
        total_amount=5,
    )
    bot = TelegramBot(state_store=store)
    fake_bot = _FakeTelegramBot()
    message = _FakeMessage("/inline_refund once-charge")

    await bot.inline_refund_command(
        _FakeUpdate(message=message, user_id=42),
        SimpleNamespace(args=["once-charge"], bot=fake_bot),
    )

    payment = store.get_inline_one_time_payment(payment_id)
    assert fake_bot.refunds == [{"user_id": 1001, "telegram_payment_charge_id": "once-charge"}]
    assert payment["status"] == "refunded"
    assert payment["refund_reason"] == "owner_command"
    assert message.replies == ["Inline refund sent for user 1001."]


@pytest.mark.asyncio
async def test_non_owner_inline_refund_does_not_call_telegram(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_OWNER_USER_ID", 42)
    store = StateStore(tmp_path / "state.db")
    bot = TelegramBot(state_store=store)
    fake_bot = _FakeTelegramBot()
    message = _FakeMessage("/inline_refund sub-charge 1001")

    await bot.inline_refund_command(
        _FakeUpdate(message=message, user_id=99),
        SimpleNamespace(args=["sub-charge", "1001"], bot=fake_bot),
    )

    assert fake_bot.refunds == []


@pytest.mark.asyncio
async def test_pre_checkout_approves_valid_subscription(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(subscription_stars=5)
    bot = TelegramBot(state_store=store)
    query = _FakePreCheckoutQuery(
        invoice_payload=build_subscription_payload(user_id=1001, session_token="s1"),
        total_amount=5,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers == [{"ok": True}]


@pytest.mark.asyncio
async def test_pre_checkout_rejects_wrong_user(tmp_path):
    bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    query = _FakePreCheckoutQuery(
        invoice_payload=build_subscription_payload(user_id=1002, session_token="s1"),
        total_amount=settings.INLINE_SUBSCRIPTION_STARS,
        user_id=1001,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers[0]["ok"] is False


@pytest.mark.asyncio
async def test_pre_checkout_rejects_wrong_amount(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(subscription_stars=5)
    bot = TelegramBot(state_store=store)
    query = _FakePreCheckoutQuery(
        invoice_payload=build_subscription_payload(user_id=1001, session_token="s1"),
        total_amount=4,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers[0]["ok"] is False


@pytest.mark.asyncio
async def test_pre_checkout_rejects_disabled_one_time_payment(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=False, one_time_stars=5)
    bot = TelegramBot(state_store=store)
    query = _FakePreCheckoutQuery(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="s1"),
        total_amount=5,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers[0]["ok"] is False


@pytest.mark.asyncio
async def test_pre_checkout_approves_live_one_time_session(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=5)
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    bot = TelegramBot(state_store=store)
    query = _FakePreCheckoutQuery(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="s1"),
        total_amount=5,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers == [{"ok": True}]


@pytest.mark.asyncio
async def test_pre_checkout_rejects_expired_one_time_session(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=5)
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    bot = TelegramBot(state_store=store)
    query = _FakePreCheckoutQuery(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="s1"),
        total_amount=5,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers[0]["ok"] is False


@pytest.mark.asyncio
async def test_pre_checkout_rejects_missing_one_time_session(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=5)
    bot = TelegramBot(state_store=store)
    query = _FakePreCheckoutQuery(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="missing"),
        total_amount=5,
    )

    await bot.pre_checkout_handler(_FakeUpdate(pre_checkout_query=query), SimpleNamespace(bot=SimpleNamespace()))

    assert query.answers[0]["ok"] is False


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


@pytest.mark.asyncio
async def test_refund_api_failure_marks_one_time_payment_refund_failed(tmp_path):
    store = StateStore(tmp_path / "state.db")
    payment_id = store.record_inline_one_time_payment(
        user_id=1001,
        session_token="s1",
        telegram_payment_charge_id="tg-charge",
        total_amount=5,
    )
    bot = TelegramBot(state_store=store)

    await bot._refund_one_time_payment(
        SimpleNamespace(bot=_FailingRefundTelegramBot()),
        payment_id=payment_id,
        user_id=1001,
        reason="inline_message_missing",
    )

    payment = store.get_inline_one_time_payment(payment_id)
    assert payment["status"] == "refund_failed"
    assert payment["refund_reason"] == "inline_message_missing:TelegramError"


@pytest.mark.asyncio
async def test_duplicate_one_time_successful_payment_records_once_without_invoice_delivery(monkeypatch, tmp_path):
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
    deliveries = []

    async def fake_deliver(context, *, session_token, one_time_payment_id):
        deliveries.append({"session_token": session_token, "one_time_payment_id": one_time_payment_id})

    monkeypatch.setattr(bot, "_deliver_inline_session", fake_deliver)
    successful_payment = SimpleNamespace(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="s1"),
        currency="XTR",
        total_amount=5,
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
    )
    message = _FakeMessage(successful_payment=successful_payment)
    update = _FakeUpdate(message=message, user_id=1001)

    await bot.successful_payment_handler(update, SimpleNamespace(bot=SimpleNamespace()))
    await bot.successful_payment_handler(update, SimpleNamespace(bot=SimpleNamespace()))

    rows = store._conn.execute(
        "SELECT * FROM inline_one_time_payments WHERE telegram_payment_charge_id = ?",
        ("tg-charge",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "paid"
    assert len(deliveries) == 0


@pytest.mark.asyncio
async def test_one_time_success_before_inline_message_stays_paid_for_later_delivery(tmp_path):
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

    payment = store._conn.execute(
        "SELECT * FROM inline_one_time_payments WHERE telegram_payment_charge_id = ?",
        ("tg-charge",),
    ).fetchone()
    assert payment["status"] == "paid"
    assert payment["refund_reason"] is None
    assert fake_bot.refunds == []


@pytest.mark.asyncio
async def test_subscription_success_grants_later_inline_result_without_invoice_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(subscription_stars=5)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    store.create_inline_session(
        session_token="invoice-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=expires_at,
    )
    store.attach_inline_message("invoice-session", inline_message_id="invoice-msg")
    bot = TelegramBot(state_store=store)
    deliveries = []

    async def fake_deliver(context, *, session_token, one_time_payment_id):
        deliveries.append({"session_token": session_token, "one_time_payment_id": one_time_payment_id})

    monkeypatch.setattr(bot, "_deliver_inline_session", fake_deliver)
    successful_payment = SimpleNamespace(
        invoice_payload=build_subscription_payload(user_id=1001, session_token="invoice-session"),
        currency="XTR",
        total_amount=5,
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
        subscription_expiration_date=datetime.now(timezone.utc) + timedelta(days=30),
    )

    await bot.successful_payment_handler(
        _FakeUpdate(message=_FakeMessage(successful_payment=successful_payment), user_id=1001),
        SimpleNamespace(bot=SimpleNamespace()),
    )
    later_query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    await bot.inline_query_handler(
        _FakeUpdate(inline_query=later_query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert deliveries == []
    result = later_query.answers[0]["results"][0]
    assert result.id.startswith("inline:")
    assert result.title == "Send media here"


@pytest.mark.asyncio
async def test_one_time_success_grants_later_inline_result_without_invoice_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=5)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    store.create_inline_session(
        session_token="invoice-session",
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
        invoice_payload=build_one_time_payload(user_id=1001, session_token="invoice-session"),
        currency="XTR",
        total_amount=5,
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
    )

    await bot.successful_payment_handler(
        _FakeUpdate(message=_FakeMessage(successful_payment=successful_payment), user_id=1001),
        SimpleNamespace(bot=fake_bot),
    )
    later_query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    await bot.inline_query_handler(
        _FakeUpdate(inline_query=later_query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    payment = store.get_inline_one_time_payment_by_charge_id("tg-charge")
    assert payment["status"] == "paid"
    assert fake_bot.refunds == []
    result = later_query.answers[0]["results"][0]
    assert result.id.startswith("inline_once:")
    assert result.title == "Send media here"


@pytest.mark.asyncio
async def test_one_time_entitlement_is_claimed_for_later_inline_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=True, one_time_stars=5)
    invoice_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    store.create_inline_session(
        session_token="invoice-session",
        user_id=1001,
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://www.instagram.com/reel/abc/",
        provider="instagram",
        provider_label="Instagram",
        expires_at=invoice_expires_at,
    )
    bot = TelegramBot(state_store=store)
    successful_payment = SimpleNamespace(
        invoice_payload=build_one_time_payload(user_id=1001, session_token="invoice-session"),
        currency="XTR",
        total_amount=5,
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
    )
    await bot.successful_payment_handler(
        _FakeUpdate(message=_FakeMessage(successful_payment=successful_payment), user_id=1001),
        SimpleNamespace(bot=SimpleNamespace()),
    )
    later_query = _FakeInlineQuery("https://www.instagram.com/reel/abc/")
    await bot.inline_query_handler(
        _FakeUpdate(inline_query=later_query),
        SimpleNamespace(bot=SimpleNamespace()),
    )
    result = later_query.answers[0]["results"][0]
    scheduled = []

    def fake_schedule(context, *, session_token, one_time_payment_id):
        scheduled.append({"session_token": session_token, "one_time_payment_id": one_time_payment_id})

    monkeypatch.setattr(bot, "_schedule_inline_delivery", fake_schedule)

    await bot.chosen_inline_result_handler(
        _FakeUpdate(chosen_inline_result=_FakeChosenInlineResult(result.id, "inline-msg")),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    payment = store.get_inline_one_time_payment_by_charge_id("tg-charge")
    assert scheduled == [
        {
            "session_token": result.id.split(":", 1)[1],
            "one_time_payment_id": payment["payment_id"],
        }
    ]
    assert payment["request_id"] == f"inline:{result.id.split(':', 1)[1]}"


@pytest.mark.asyncio
async def test_subscription_success_records_after_price_changed_post_checkout(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(subscription_stars=9)
    bot = TelegramBot(state_store=store)
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    successful_payment = SimpleNamespace(
        invoice_payload=build_subscription_payload(user_id=1001, session_token="s1"),
        currency="XTR",
        total_amount=5,
        telegram_payment_charge_id="tg-charge",
        provider_payment_charge_id="provider-charge",
        subscription_expiration_date=expires_at,
    )
    message = _FakeMessage(successful_payment=successful_payment)

    await bot.successful_payment_handler(
        _FakeUpdate(message=message, user_id=1001),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    subscription = store.get_inline_subscription(1001)
    assert subscription["total_amount"] == 5
    assert subscription["telegram_payment_charge_id"] == "tg-charge"


@pytest.mark.asyncio
async def test_one_time_success_refunds_expired_session_after_disabled_post_checkout(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.update_inline_runtime_settings(one_time_enabled=False, one_time_stars=9)
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
