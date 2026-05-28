import asyncio
import datetime as dtm
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.download_models import ProviderExecutionMetrics
from src.instagram_video_bot.services.job_manager import SharedJob, RequestRecord
from src.instagram_video_bot.services.telegram_bot import RequestContext, TelegramBot
from src.instagram_video_bot.services.video_downloader import (
    DownloadError,
    MediaItem,
    VideoInfo,
)
from src.instagram_video_bot.utils.account_manager import AccountHealthEvent


class _FakeBot:
    def __init__(self, member_status: str = "member"):
        self.video_calls = []
        self.photo_calls = []
        self.group_calls = []
        self.message_calls = []
        self.member_status = member_status

    async def send_message(self, **kwargs):
        self.message_calls.append(kwargs)

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return SimpleNamespace(
            video=SimpleNamespace(file_id="tg-video-file-id"), photo=None
        )

    async def send_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return SimpleNamespace(
            video=None, photo=[SimpleNamespace(file_id="tg-photo-file-id")]
        )

    async def send_media_group(self, **kwargs):
        self.group_calls.append(kwargs)
        messages = []
        for item in kwargs["media"]:
            media_type = getattr(item, "type", "")
            if media_type == "video":
                messages.append(
                    SimpleNamespace(
                        video=SimpleNamespace(file_id="tg-group-video-id"), photo=None
                    )
                )
            else:
                messages.append(
                    SimpleNamespace(
                        video=None, photo=[SimpleNamespace(file_id="tg-group-photo-id")]
                    )
                )
        return messages

    async def get_chat_member(self, chat_id: int, user_id: int):
        return SimpleNamespace(status=self.member_status)


class _RejectStaleFileIdBot(_FakeBot):
    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        if kwargs["video"] == "stale-video-file-id":
            raise BadRequest("Wrong file identifier/HTTP URL specified")
        return SimpleNamespace(
            video=SimpleNamespace(file_id="fresh-video-file-id"), photo=None
        )


class _RejectStaleGroupFileIdBot(_FakeBot):
    async def send_media_group(self, **kwargs):
        self.group_calls.append(kwargs)
        if any(
            getattr(item, "media", None) == "stale-photo-file-id"
            for item in kwargs["media"]
        ):
            raise BadRequest("Wrong file identifier/HTTP URL specified")
        return [
            SimpleNamespace(
                video=None, photo=[SimpleNamespace(file_id="fresh-group-photo-id")]
            )
            for _ in kwargs["media"]
        ]


class _FakeStatusMessage:
    def __init__(self):
        self.texts = []
        self.deleted = False

    async def edit_text(self, text: str):
        self.texts.append(text)

    async def reply_text(self, text: str):
        self.texts.append(text)
        return self

    async def delete(self):
        self.deleted = True


class _FakeMessage:
    def __init__(self, text: str, message_id: int = 10, sender_chat=None):
        self.text = text
        self.message_id = message_id
        self.sender_chat = sender_chat
        self.replies = []
        self.status_messages = []

    async def reply_text(self, text: str):
        self.replies.append(text)
        status_message = _FakeStatusMessage()
        self.status_messages.append(status_message)
        return status_message


class _FakeUpdate:
    def __init__(
        self,
        text: str,
        *,
        chat_id: int = 77,
        user_id: int = 1001,
        username: str = "alice",
        full_name: str = "Alice",
        message_id: int = 10,
        chat_type: str = "private",
        language_code: str | None = "ru",
        effective_user_present: bool = True,
        sender_chat=None,
        message_present: bool = True,
    ):
        message = _FakeMessage(text, message_id=message_id, sender_chat=sender_chat)
        self.message = message if message_present else None
        self.effective_message = message
        self.effective_chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.effective_user = (
            SimpleNamespace(
                id=user_id,
                username=username,
                full_name=full_name,
                language_code=language_code,
            )
            if effective_user_present
            else None
        )


class _FakeContext:
    def __init__(self, bot, args=None):
        self.bot = bot
        self.error = None
        self.args = args or []


def _make_request_context(status_message: _FakeStatusMessage) -> RequestContext:
    return RequestContext(
        request_id="req-1",
        chat_id=77,
        user_id=1001,
        provider_label="Instagram",
        normalized_url="https://www.instagram.com/reel/a/",
        original_url="https://www.instagram.com/reel/a/",
        original_message_id=10,
        status_message=status_message,
        quiet_mode=False,
        joined_existing=False,
    )


@pytest.mark.asyncio
async def test_send_single_video_uses_send_video(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    request_context = _make_request_context(_FakeStatusMessage())

    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"video")

    info = VideoInfo(
        file_path=video_file,
        title="Video title",
        media_items=[MediaItem(file_path=video_file, media_type="video")],
        primary_media_type="video",
    )

    await telegram_bot._send_media(context, request_context, info)

    assert len(fake_bot.video_calls) == 1
    assert len(fake_bot.photo_calls) == 0
    assert len(fake_bot.group_calls) == 0


@pytest.mark.asyncio
async def test_send_single_video_passes_probe_metadata_to_telegram(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    request_context = _make_request_context(_FakeStatusMessage())

    video_file = tmp_path / "portrait.mp4"
    video_file.write_bytes(b"video")

    info = VideoInfo(
        file_path=video_file,
        title="Portrait reel",
        media_items=[
            MediaItem(
                file_path=video_file,
                media_type="video",
                duration=11.6,
                width=720,
                height=1280,
            )
        ],
        primary_media_type="video",
    )

    await telegram_bot._send_media(context, request_context, info)

    assert fake_bot.video_calls[0]["width"] == 720
    assert fake_bot.video_calls[0]["height"] == 1280
    assert fake_bot.video_calls[0]["duration"] == dtm.timedelta(seconds=12)
    assert fake_bot.video_calls[0]["supports_streaming"] is True


@pytest.mark.asyncio
async def test_send_single_photo_uses_send_photo(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    request_context = _make_request_context(_FakeStatusMessage())

    photo_file = tmp_path / "p.jpg"
    photo_file.write_bytes(b"photo")

    info = VideoInfo(
        file_path=photo_file,
        title="Photo title",
        media_items=[MediaItem(file_path=photo_file, media_type="photo")],
        primary_media_type="photo",
    )

    await telegram_bot._send_media(context, request_context, info)

    assert len(fake_bot.video_calls) == 0
    assert len(fake_bot.photo_calls) == 1
    assert len(fake_bot.group_calls) == 0


@pytest.mark.asyncio
async def test_send_carousel_uses_media_group(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    request_context = _make_request_context(_FakeStatusMessage())

    first = tmp_path / "1.jpg"
    second = tmp_path / "2.mp4"
    first.write_bytes(b"photo")
    second.write_bytes(b"video")

    info = VideoInfo(
        file_path=first,
        title="Album title",
        media_items=[
            MediaItem(file_path=first, media_type="photo"),
            MediaItem(file_path=second, media_type="video"),
        ],
        primary_media_type="photo",
    )

    await telegram_bot._send_media(context, request_context, info)

    assert len(fake_bot.video_calls) == 0
    assert len(fake_bot.photo_calls) == 0
    assert len(fake_bot.group_calls) == 1
    assert len(fake_bot.group_calls[0]["media"]) == 2


@pytest.mark.asyncio
async def test_send_large_carousel_respects_telegram_album_limit(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    request_context = _make_request_context(_FakeStatusMessage())

    media_items = []
    for index in range(11):
        media_file = tmp_path / f"{index}.jpg"
        media_file.write_bytes(b"photo")
        media_items.append(MediaItem(file_path=media_file, media_type="photo"))

    info = VideoInfo(
        file_path=media_items[0].file_path,
        title="Large album",
        media_items=media_items,
        primary_media_type="photo",
    )

    await telegram_bot._send_media(context, request_context, info)

    assert len(fake_bot.group_calls) == 1
    assert len(fake_bot.group_calls[0]["media"]) == 10
    assert len(fake_bot.photo_calls) == 1
    assert fake_bot.photo_calls[0]["caption"] is None


@pytest.mark.asyncio
async def test_send_media_group_video_includes_probe_metadata(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    request_context = _make_request_context(_FakeStatusMessage())

    first = tmp_path / "1.jpg"
    second = tmp_path / "2.mp4"
    first.write_bytes(b"photo")
    second.write_bytes(b"video")

    info = VideoInfo(
        file_path=first,
        title="Album title",
        media_items=[
            MediaItem(file_path=first, media_type="photo"),
            MediaItem(
                file_path=second,
                media_type="video",
                duration=4.2,
                width=1080,
                height=1920,
            ),
        ],
        primary_media_type="photo",
    )

    await telegram_bot._send_media(context, request_context, info)

    video_media = fake_bot.group_calls[0]["media"][1]
    assert video_media.width == 1080
    assert video_media.height == 1920
    assert video_media._duration == dtm.timedelta(seconds=4)
    assert video_media.supports_streaming is True


@pytest.mark.asyncio
async def test_handle_message_processes_request_in_background(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/a/")

    media_file = tmp_path / "cleanup.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(self, url: str, output_dir: Path):
        return VideoInfo(
            file_path=media_file,
            title="cleanup",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert not media_file.exists()
    assert len(fake_bot.video_calls) == 1
    assert update.message.replies == ["Принял Instagram. Скоро начну скачивать."]
    assert update.message.status_messages[0].texts == ["Instagram: скачиваю."]
    assert update.message.status_messages[0].deleted is True


@pytest.mark.asyncio
async def test_handle_message_uses_english_profile_language(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/a/", language_code="en")

    media_file = tmp_path / "english.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(self, url: str, output_dir: Path):
        return VideoInfo(
            file_path=media_file,
            title="english",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert update.message.replies == ["Got Instagram. I will start downloading soon."]
    assert update.message.status_messages[0].texts == ["Instagram: downloading."]


@pytest.mark.asyncio
async def test_handle_message_rejects_user_over_rate_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.USER_RATE_LIMIT_REQUESTS",
        1,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.USER_RATE_LIMIT_WINDOW_SECONDS",
        600,
    )

    async def fake_download_video(self, url: str, output_dir: Path):
        await asyncio.Future()

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    context = _FakeContext(_FakeBot())
    first_update = _FakeUpdate("https://x.com/example/status/1", user_id=1001)
    second_update = _FakeUpdate("https://x.com/example/status/2", user_id=1001)

    await telegram_bot.handle_message(first_update, context)
    await telegram_bot.handle_message(second_update, context)

    tasks = list(telegram_bot.active_request_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert first_update.message.replies == ["Принял Twitter/X. Скоро начну скачивать."]
    assert second_update.message.replies == [
        "Слишком много запросов. Попробуй снова примерно через 10 мин."
    ]


@pytest.mark.asyncio
async def test_legacy_redirect_mode_replies_without_queueing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BOT_LEGACY_REDIRECT_MODE", True)
    monkeypatch.setattr(settings, "BOT_MIGRATION_TARGET_USERNAME", "igclipbot")
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    monkeypatch.setattr(
        telegram_bot.job_manager,
        "submit",
        lambda **_kwargs: pytest.fail("submit called"),
    )
    update = _FakeUpdate("https://x.com/example/status/1", user_id=1001)
    context = _FakeContext(_FakeBot())

    await telegram_bot.handle_message(update, context)

    assert update.message.replies == [
        "Мы переехали в @igclipbot.\nОткрыть нового бота: https://t.me/igclipbot"
    ]


@pytest.mark.asyncio
async def test_handle_message_processes_sender_chat_text_link(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    sender_chat = SimpleNamespace(id=-1001234567890, title="Example Sender Chat")
    update = _FakeUpdate(
        "https://www.instagram.com/reel/ABC123xyz_9/?igsh=example",
        chat_id=-1001234567890,
        chat_type="supergroup",
        effective_user_present=False,
        sender_chat=sender_chat,
    )

    media_file = tmp_path / "sender-chat.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(self, url: str, output_dir: Path):
        return VideoInfo(
            file_path=media_file,
            title="sender chat",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert update.message.replies == ["Got Instagram. I will start downloading soon."]
    assert len(fake_bot.video_calls) == 1
    events = telegram_bot.state_store._conn.execute(
        "SELECT user_id, user_label, normalized_url FROM request_events ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert dict(events) == {
        "user_id": -1001234567890,
        "user_label": "Example Sender Chat",
        "normalized_url": "https://www.instagram.com/reel/ABC123xyz_9/",
    }


@pytest.mark.asyncio
async def test_handle_message_processes_effective_message_text_link(
    monkeypatch, tmp_path
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    sender_chat = SimpleNamespace(id=-1001234567890, title="Example Sender Chat")
    update = _FakeUpdate(
        "https://www.instagram.com/reel/ABC123xyz_9/?igsh=example",
        chat_id=-1001234567890,
        chat_type="supergroup",
        effective_user_present=False,
        sender_chat=sender_chat,
        message_present=False,
    )

    media_file = tmp_path / "effective-message.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(self, url: str, output_dir: Path):
        return VideoInfo(
            file_path=media_file,
            title="effective message",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert update.effective_message.replies == [
        "Got Instagram. I will start downloading soon."
    ]
    assert len(fake_bot.video_calls) == 1


@pytest.mark.asyncio
async def test_duplicate_suppressed_requests_send_media_only_once(
    monkeypatch, tmp_path
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    first_update = _FakeUpdate(
        "https://www.instagram.com/reel/a/",
        user_id=1001,
        username="alice",
        full_name="Alice",
        message_id=10,
    )
    second_update = _FakeUpdate(
        "https://www.instagram.com/reel/a/",
        user_id=1002,
        username="bob",
        full_name="Bob",
        message_id=11,
    )

    media_file = tmp_path / "shared.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(self, url: str, output_dir: Path):
        await asyncio.sleep(0)
        return VideoInfo(
            file_path=media_file,
            title="shared",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    await telegram_bot.handle_message(first_update, context)
    await telegram_bot.handle_message(second_update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert len(fake_bot.video_calls) == 1
    assert fake_bot.video_calls[0]["reply_to_message_id"] == 10
    assert first_update.message.status_messages[0].deleted is True
    assert second_update.message.status_messages[0].deleted is True
    assert second_update.message.replies == [
        "Instagram уже скачивается. Дождусь общего результата."
    ]
    assert second_update.message.status_messages[0].texts == []


@pytest.mark.asyncio
async def test_concurrent_downloads_keep_account_alerts_scoped_to_job(
    monkeypatch, tmp_path
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    alert_update = _FakeUpdate("https://www.instagram.com/reel/alert/", message_id=10)
    clean_update = _FakeUpdate("https://www.instagram.com/reel/clean/", message_id=11)
    alert_file = tmp_path / "alert.mp4"
    clean_file = tmp_path / "clean.mp4"
    alert_file.write_bytes(b"alert")
    clean_file.write_bytes(b"clean")
    downloader_instances = []

    class FakeDownloader:
        def __init__(self):
            self.last_account_health_event = None
            downloader_instances.append(self)

        async def download_video(self, url: str, output_dir: Path):
            await asyncio.sleep(0)
            if "alert" in url:
                self.last_account_health_event = SimpleNamespace(
                    should_alert_owner=True,
                    username="acc1",
                    reason="auth_challenge",
                    consecutive_failures=2,
                    threshold=2,
                    available_accounts=2,
                    total_accounts=13,
                    low_watermark=3,
                )
                media_file = alert_file
            else:
                media_file = clean_file
            return VideoInfo(
                file_path=media_file,
                title="scoped",
                media_items=[MediaItem(file_path=media_file, media_type="video")],
                primary_media_type="video",
            )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader
    )

    await telegram_bot.handle_message(alert_update, context)
    await telegram_bot.handle_message(clean_update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert len(downloader_instances) == 2
    assert len({id(instance) for instance in downloader_instances}) == 2
    assert (
        sum(
            1
            for instance in downloader_instances
            if getattr(instance.last_account_health_event, "should_alert_owner", False)
        )
        == 1
    )
    assert len(fake_bot.video_calls) == 2
    assert [
        call
        for call in fake_bot.message_calls
        if "Instagram account pool warning:" in call["text"]
    ] == [
        {
            "chat_id": 1001,
            "text": (
                "Instagram account pool warning:\n"
                "Usable accounts left: 2 of 13.\n"
                "Low-watermark threshold: 3.\n"
                "Last removed account: acc1.\n"
                "Reason: auth_challenge after 2 sequential failures."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_chaos_duplicate_request_uses_playful_russian_text(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    telegram_bot.state_store.update_group_settings(77, chaos_mode_enabled=True)
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    first_update = _FakeUpdate(
        "https://www.instagram.com/reel/a/",
        user_id=1001,
        username="alice",
        full_name="Alice",
        message_id=10,
    )
    second_update = _FakeUpdate(
        "https://www.instagram.com/reel/a/",
        user_id=1002,
        username="bob",
        full_name="Bob",
        message_id=11,
    )

    media_file = tmp_path / "shared-chaos.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(self, url: str, output_dir: Path):
        await asyncio.sleep(0)
        return VideoInfo(
            file_path=media_file,
            title="shared",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )

    await telegram_bot.handle_message(first_update, context)
    await telegram_bot.handle_message(second_update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert len(fake_bot.video_calls) == 1
    assert second_update.message.replies == [
        "Instagram уже в работе. Повтор засчитан, сидим рядом с таймером."
    ]


@pytest.mark.asyncio
async def test_cache_hit_is_delivered_and_counted(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/cached/")
    media_file = tmp_path / "cached.mp4"
    media_file.write_bytes(b"video")
    telegram_bot.state_store.save_cached_result(
        chat_id=77,
        normalized_url="https://www.instagram.com/reel/cached/",
        provider="instagram",
        title="cached",
        media_items=[
            {
                "file_path": str(media_file),
                "media_type": "video",
                "caption": None,
                "duration": None,
            }
        ],
        ttl_seconds=3600,
    )

    async def fail_download_video(self, url: str, output_dir: Path):
        raise AssertionError("cache hit should skip downloader")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fail_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert len(fake_bot.video_calls) == 1
    assert telegram_bot.state_store.get_public_status(77)["cache_hits"] == 1


@pytest.mark.asyncio
async def test_download_failure_uses_russian_error_text(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    context = _FakeContext(_FakeBot())
    update = _FakeUpdate("https://www.instagram.com/reel/fail/")

    async def fail_download_video(self, url: str, output_dir: Path):
        from src.instagram_video_bot.services.video_downloader import DownloadError

        raise DownloadError("rate limit")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fail_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert (
        update.message.status_messages[0].texts[-1]
        == "Достигнут лимит провайдера. Попробуй позже."
    )


@pytest.mark.asyncio
async def test_download_failure_sends_owner_alert_when_pool_is_low(
    monkeypatch, tmp_path
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/fail/")

    class FakeDownloader:
        def __init__(self):
            self.last_account_health_event = SimpleNamespace(
                should_alert_owner=True,
                username="acc1",
                reason="auth_challenge",
                consecutive_failures=2,
                threshold=2,
                available_accounts=2,
                total_accounts=13,
                low_watermark=3,
            )

        async def download_video(self, url: str, output_dir: Path):
            raise RuntimeError("download failed")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert any(
        call["chat_id"] == 1001 and "Instagram account pool warning:" in call["text"]
        for call in fake_bot.message_calls
    )


@pytest.mark.asyncio
async def test_download_success_sends_owner_alert_when_retry_quarantines_account(
    monkeypatch, tmp_path
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/success/")
    media_file = tmp_path / "success.mp4"
    media_file.write_bytes(b"video")
    downloader_instances = []

    class FakeDownloader:
        def __init__(self):
            self.attempted_accounts = []
            self.quarantined_accounts = []
            self.last_account_health_event = SimpleNamespace(
                should_alert_owner=True,
                username="acc1",
                reason="auth_challenge",
                consecutive_failures=2,
                threshold=2,
                available_accounts=2,
                total_accounts=13,
                low_watermark=3,
            )
            downloader_instances.append(self)

        async def download_video(self, url: str, output_dir: Path):
            self.attempted_accounts.extend(["acc1", "acc2"])
            self.quarantined_accounts.append("acc1")
            return VideoInfo(
                file_path=media_file,
                title="success",
                media_items=[MediaItem(file_path=media_file, media_type="video")],
                primary_media_type="video",
            )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert len(fake_bot.video_calls) == 1
    assert len(downloader_instances) == 1
    assert downloader_instances[0].attempted_accounts == ["acc1", "acc2"]
    assert downloader_instances[0].quarantined_accounts == ["acc1"]
    assert any(
        call["chat_id"] == 1001 and "Instagram account pool warning:" in call["text"]
        for call in fake_bot.message_calls
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/abc123/",
        "https://www.instagram.com/reel/abc123/",
        "https://www.instagram.com/reels/abc123/",
        "https://www.instagram.com/tv/abc123/",
        "https://www.instagram.com/share/reel/abc123/",
        "https://www.instagram.com/stories/someuser/1234567890123456789/",
        "https://ddinstagram.com/p/abc123/",
        "https://d.ddinstagram.com/p/abc123/",
        "https://g.ddinstagram.com/p/abc123/",
        "https://twitter.com/someuser/status/1901234567890123456",
        "https://x.com/someuser/status/1901234567890123456",
        "https://www.youtube.com/shorts/abc123XYZ90",
    ],
)
def test_supported_url_pattern_matches_supported_routes(url):
    assert TelegramBot.INSTAGRAM_VIDEO_PATTERN.search(url)


def test_build_caption_text_truncates_long_titles():
    title = "x" * 2000

    caption = TelegramBot._build_caption_text(title)

    assert len(caption) == TelegramBot.MAX_MEDIA_CAPTION_LENGTH
    assert caption.endswith("...")


@pytest.mark.asyncio
async def test_owner_low_account_pool_alert_is_english(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    event = AccountHealthEvent(
        username="acc1",
        reason="auth_challenge",
        consecutive_failures=2,
        threshold=2,
        threshold_reached=True,
        available_accounts=2,
        total_accounts=13,
        low_watermark=3,
        should_alert_owner=True,
    )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot._notify_owner_about_low_account_pool(context, event)

    assert fake_bot.message_calls == [
        {
            "chat_id": 1001,
            "text": (
                "Instagram account pool warning:\n"
                "Usable accounts left: 2 of 13.\n"
                "Low-watermark threshold: 3.\n"
                "Last removed account: acc1.\n"
                "Reason: auth_challenge after 2 sequential failures."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_owner_low_account_pool_alert_skips_without_owner(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    event = AccountHealthEvent(
        username="acc1",
        reason="auth_challenge",
        consecutive_failures=2,
        threshold=2,
        threshold_reached=True,
        available_accounts=2,
        total_accounts=13,
        low_watermark=3,
        should_alert_owner=True,
    )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", None
    )

    await telegram_bot._notify_owner_about_low_account_pool(context, event)

    assert fake_bot.message_calls == []


@pytest.mark.asyncio
async def test_shared_delivery_handoffs_to_another_requester_on_send_failure(
    monkeypatch, tmp_path
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    context = _FakeContext(_FakeBot())
    first_update = _FakeUpdate(
        "https://www.instagram.com/reel/a/",
        user_id=1001,
        username="alice",
        full_name="Alice",
        message_id=10,
    )
    second_update = _FakeUpdate(
        "https://www.instagram.com/reel/a/",
        user_id=1002,
        username="bob",
        full_name="Bob",
        message_id=11,
    )

    media_file = tmp_path / "handoff.mp4"
    media_file.write_bytes(b"video")
    delivered_message_ids = []

    async def fake_download_video(self, url: str, output_dir: Path):
        await asyncio.sleep(0)
        return VideoInfo(
            file_path=media_file,
            title="handoff",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    async def fake_send_media(self, context, request_context, video_info):
        if request_context.original_message_id == 10:
            raise RuntimeError("telegram send failed")
        delivered_message_ids.append(request_context.original_message_id)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fake_download_video,
    )
    monkeypatch.setattr(TelegramBot, "_send_media", fake_send_media)

    await telegram_bot.handle_message(first_update, context)
    await telegram_bot.handle_message(second_update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert delivered_message_ids == [11]
    assert first_update.message.status_messages[0].deleted is True
    assert second_update.message.status_messages[0].deleted is True
    assert first_update.message.status_messages[0].texts == ["Instagram: скачиваю."]
    assert second_update.message.status_messages[0].texts == []


@pytest.mark.asyncio
async def test_owner_only_quiet_command_requires_owner(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/quiet on")
    context = _FakeContext(_FakeBot(), args=["on"])

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 9999
    )

    await telegram_bot.quiet_command(update, context)

    assert update.message.replies[-1] == "Эта команда доступна только владельцу бота."


@pytest.mark.asyncio
async def test_owner_can_toggle_quiet_mode(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/quiet on")
    context = _FakeContext(_FakeBot(), args=["on"])

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot.quiet_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(
        update.effective_chat.id
    )
    assert settings_row["quiet_mode"] is True
    assert update.message.replies[-1] == "Тихий режим: включено."


@pytest.mark.asyncio
async def test_owner_can_toggle_chaos_mode_on_and_off(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    context = _FakeContext(_FakeBot(), args=["on"])
    update = _FakeUpdate("/chaos on", chat_type="group")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot.chaos_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(
        update.effective_chat.id
    )
    assert settings_row["chaos_mode_enabled"] is True
    assert (
        update.message.replies[-1]
        == "Режим хаоса включен. Теперь бот будет шуметь по делу."
    )

    context.args = ["off"]
    await telegram_bot.chaos_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(
        update.effective_chat.id
    )
    assert settings_row["chaos_mode_enabled"] is False
    assert (
        update.message.replies[-1]
        == "Режим хаоса выключен. Возвращаюсь к спокойному режиму."
    )


@pytest.mark.asyncio
async def test_chaos_status_reports_russian_state(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/chaos status")
    context = _FakeContext(_FakeBot(), args=["status"])

    await telegram_bot.chaos_command(update, context)

    assert update.message.replies[-1] == "Режим хаоса выключен для этого чата."


@pytest.mark.asyncio
async def test_chaos_command_rejects_non_admin_group_member(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/chaos on", chat_type="group")
    context = _FakeContext(_FakeBot(member_status="member"), args=["on"])

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 9999
    )

    await telegram_bot.chaos_command(update, context)

    assert (
        update.message.replies[-1]
        == "Режим хаоса могут переключать только админы чата или владелец бота."
    )


@pytest.mark.asyncio
async def test_help_formats_status_and_stats_are_russian(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/help")
    context = _FakeContext(_FakeBot())

    await telegram_bot.help_command(update, context)
    await telegram_bot.formats_command(update, context)
    await telegram_bot.status_command(update, context)
    await telegram_bot.stats_command(update, context)

    assert "Пришли ссылку" in update.message.replies[0]
    assert "Настройки владельца" not in update.message.replies[0]
    assert "/admin_status" not in update.message.replies[0]
    assert "Поддерживаемые ссылки" in update.message.replies[1]
    assert "Статус очереди" in update.message.replies[2]
    assert "Статистика чата" in update.message.replies[3]


@pytest.mark.asyncio
async def test_start_uses_profile_language_with_english_fallback(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    context = _FakeContext(_FakeBot())
    ru_update = _FakeUpdate("/start", user_id=1001, language_code="ru")
    en_update = _FakeUpdate("/start", user_id=1002, language_code="es")

    await telegram_bot.start_command(ru_update, context)
    await telegram_bot.start_command(en_update, context)

    assert "Привет" in ru_update.message.replies[-1]
    assert "Send me a link" in en_update.message.replies[-1]


@pytest.mark.asyncio
async def test_language_command_persists_user_override(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    set_update = _FakeUpdate("/language en", language_code="ru")
    start_update = _FakeUpdate("/start", language_code="ru")

    await telegram_bot.language_command(
        set_update, _FakeContext(_FakeBot(), args=["en"])
    )
    await telegram_bot.start_command(start_update, _FakeContext(_FakeBot()))

    assert set_update.message.replies[-1] == "Language set to English."
    assert "Send me a link" in start_update.message.replies[-1]


@pytest.mark.asyncio
async def test_language_command_rejects_unknown_language(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/language de", language_code="en")

    await telegram_bot.language_command(update, _FakeContext(_FakeBot(), args=["de"]))

    assert update.message.replies[-1] == "Usage: /language en|ru"


@pytest.mark.asyncio
async def test_admin_help_lists_owner_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 42
    )
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/admin_help", user_id=42)
    context = _FakeContext(_FakeBot())

    await telegram_bot.admin_help_command(update, context)

    reply = update.message.replies[-1]
    assert "Admin commands" in reply
    assert "/admin_status" in reply
    assert "/inline_refund <telegram_payment_charge_id> [user_id]" in reply
    assert "USER_RATE_LIMIT_REQUESTS" in reply
    assert "first 3 successful inline deliveries" in reply
    assert "30% subscription refund protection" in reply


@pytest.mark.asyncio
async def test_admin_help_rejects_non_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 42
    )
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/admin_help", user_id=99)
    context = _FakeContext(_FakeBot())

    await telegram_bot.admin_help_command(update, context)

    assert update.message.replies[-1] == "Эта команда доступна только владельцу бота."


@pytest.mark.asyncio
async def test_quiet_mode_skips_running_status_updates(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    status_message = _FakeStatusMessage()
    request_context = RequestContext(
        request_id="req-1",
        chat_id=77,
        user_id=1001,
        provider_label="Instagram",
        normalized_url="https://www.instagram.com/reel/a/",
        original_url="https://www.instagram.com/reel/a/",
        original_message_id=10,
        status_message=status_message,
        quiet_mode=True,
        joined_existing=False,
    )
    telegram_bot.request_contexts["req-1"] = request_context
    job = SharedJob(
        job_id="job-1",
        chat_id=77,
        submitter_user_id=1001,
        provider="instagram",
        provider_label="Instagram",
        original_url=request_context.original_url,
        normalized_url=request_context.normalized_url,
        state="running",
        requesters={
            "req-1": RequestRecord(
                request_id="req-1", chat_id=77, user_id=1001, user_label="alice"
            )
        },
    )

    await telegram_bot._on_job_state_change(job)

    assert status_message.texts == []


@pytest.mark.asyncio
async def test_joined_existing_request_skips_running_status_updates(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    status_message = _FakeStatusMessage()
    request_context = RequestContext(
        request_id="req-1",
        chat_id=77,
        user_id=1001,
        provider_label="Instagram",
        normalized_url="https://www.instagram.com/reel/a/",
        original_url="https://www.instagram.com/reel/a/",
        original_message_id=10,
        status_message=status_message,
        quiet_mode=False,
        joined_existing=True,
    )
    telegram_bot.request_contexts["req-1"] = request_context
    job = SharedJob(
        job_id="job-1",
        chat_id=77,
        submitter_user_id=1001,
        provider="instagram",
        provider_label="Instagram",
        original_url=request_context.original_url,
        normalized_url=request_context.normalized_url,
        state="running",
        requesters={
            "req-1": RequestRecord(
                request_id="req-1", chat_id=77, user_id=1001, user_label="alice"
            )
        },
    )

    await telegram_bot._on_job_state_change(job)

    assert status_message.texts == []


@pytest.mark.asyncio
async def test_owner_can_toggle_stats_mode(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/statsmode off")
    context = _FakeContext(_FakeBot(), args=["off"])

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot.statsmode_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(
        update.effective_chat.id
    )
    assert settings_row["stats_enabled"] is False
    assert update.message.replies[-1] == "Статистика: выключено."


@pytest.mark.asyncio
async def test_owner_can_override_chat_limit(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/chatlimit 4")
    context = _FakeContext(_FakeBot(), args=["4"])

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot.chatlimit_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(
        update.effective_chat.id
    )
    snapshot = telegram_bot.job_manager.get_snapshot(update.effective_chat.id)
    assert settings_row["chat_max_concurrent_jobs"] == 4
    assert snapshot["chat_limit"] == 4
    assert update.message.replies[-1] == "Лимит чата: 4."


@pytest.mark.asyncio
async def test_admin_status_reports_owner_settings(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    telegram_bot.state_store.update_group_settings(
        77,
        quiet_mode=True,
        duplicate_suppression=False,
        stats_enabled=False,
        chat_max_concurrent_jobs=5,
        user_max_active_jobs=2,
    )
    update = _FakeUpdate("/admin_status")
    context = _FakeContext(_FakeBot())

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot.admin_status_command(update, context)

    reply = update.message.replies[-1]
    assert "Тихий режим: включен" in reply
    assert "Защита от повторов: выключена" in reply
    assert "Статистика: выключена" in reply
    assert "Лимит чата: 5" in reply
    assert "Лимит на пользователя: 2" in reply


@pytest.mark.asyncio
async def test_admin_status_reports_stale_active_jobs(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/admin_status")
    context = _FakeContext(_FakeBot())
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )
    telegram_bot.state_store.create_job(
        "stale-job",
        77,
        "https://www.instagram.com/reel/stale/",
        "instagram",
        "running",
    )
    with telegram_bot.state_store._lock, telegram_bot.state_store._conn:
        telegram_bot.state_store._conn.execute(
            "UPDATE jobs SET created_at = '2026-01-01T00:00:00+00:00', started_at = '2026-01-01T00:00:00+00:00' WHERE job_id = 'stale-job'"
        )

    await telegram_bot.admin_status_command(update, context)

    assert "Зависших активных задач: 1" in update.message.replies[-1]


@pytest.mark.asyncio
async def test_cache_hit_records_performance_metrics(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/perf-cache/")
    media_file = tmp_path / "perf-cache.mp4"
    media_file.write_bytes(b"video")
    telegram_bot.state_store.save_cached_result(
        chat_id=77,
        normalized_url="https://www.instagram.com/reel/perf-cache/",
        provider="instagram",
        title="cached",
        media_items=[
            {
                "file_path": str(media_file),
                "media_type": "video",
                "caption": None,
                "duration": None,
            }
        ],
        ttl_seconds=3600,
    )

    async def fail_download_video(self, url: str, output_dir: Path):
        raise AssertionError("cache hit should skip downloader")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video",
        fail_download_video,
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    summary = telegram_bot.state_store.get_performance_summary(77, limit=50)
    assert summary["cache_hits"] == 1
    assert summary["avg_delivery_ms"] >= 0


@pytest.mark.asyncio
async def test_send_media_persists_and_reuses_telegram_file_id(tmp_path):
    store = StateStore(tmp_path / "state.db")
    telegram_bot = TelegramBot(state_store=store)
    context = _FakeContext(_FakeBot())
    request_context = _make_request_context(_FakeStatusMessage())

    media_file = tmp_path / "cached-file-id.mp4"
    media_file.write_bytes(b"video")
    store.save_cached_result(
        chat_id=request_context.chat_id,
        normalized_url=request_context.normalized_url,
        provider="instagram",
        title="cached file id",
        media_items=[
            {
                "file_path": str(media_file),
                "media_type": "video",
                "caption": None,
                "duration": None,
                "width": None,
                "height": None,
            }
        ],
        ttl_seconds=3600,
    )

    info = VideoInfo(
        file_path=media_file,
        title="cached file id",
        media_items=[MediaItem(file_path=media_file, media_type="video")],
        primary_media_type="video",
    )

    await telegram_bot._send_media(context, request_context, info)

    cached = store.get_cached_result(
        request_context.chat_id, request_context.normalized_url
    )
    assert cached is not None
    assert cached.media_items[0]["telegram_file_id"] == "tg-video-file-id"

    cached_info = telegram_bot._video_info_from_cache(cached)
    second_fake_bot = _FakeBot()
    await telegram_bot._send_media(
        _FakeContext(second_fake_bot), request_context, cached_info
    )

    assert second_fake_bot.video_calls[0]["video"] == "tg-video-file-id"


@pytest.mark.asyncio
async def test_send_media_falls_back_to_local_upload_when_cached_file_id_is_rejected(
    tmp_path,
):
    store = StateStore(tmp_path / "state.db")
    telegram_bot = TelegramBot(state_store=store)
    fake_bot = _RejectStaleFileIdBot()
    request_context = _make_request_context(_FakeStatusMessage())

    media_file = tmp_path / "stale-file-id.mp4"
    media_file.write_bytes(b"video")
    store.save_cached_result(
        chat_id=request_context.chat_id,
        normalized_url=request_context.normalized_url,
        provider="instagram",
        title="cached file id",
        media_items=[
            {
                "file_path": str(media_file),
                "media_type": "video",
                "caption": None,
                "duration": None,
                "width": None,
                "height": None,
                "telegram_file_id": "stale-video-file-id",
            }
        ],
        ttl_seconds=3600,
    )

    cached = store.get_cached_result(
        request_context.chat_id, request_context.normalized_url
    )
    assert cached is not None

    await telegram_bot._send_media(
        _FakeContext(fake_bot),
        request_context,
        telegram_bot._video_info_from_cache(cached),
    )

    assert len(fake_bot.video_calls) == 2
    assert fake_bot.video_calls[0]["video"] == "stale-video-file-id"
    assert not isinstance(fake_bot.video_calls[1]["video"], str)

    refreshed = store.get_cached_result(
        request_context.chat_id, request_context.normalized_url
    )
    assert refreshed is not None
    assert refreshed.media_items[0]["telegram_file_id"] == "fresh-video-file-id"


@pytest.mark.asyncio
async def test_send_media_group_falls_back_to_local_upload_when_cached_file_id_is_rejected(
    tmp_path,
):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _RejectStaleGroupFileIdBot()
    request_context = _make_request_context(_FakeStatusMessage())

    first = tmp_path / "stale-1.jpg"
    second = tmp_path / "stale-2.jpg"
    first.write_bytes(b"photo")
    second.write_bytes(b"photo")
    info = VideoInfo(
        file_path=first,
        title="cached album",
        media_items=[
            MediaItem(
                file_path=first,
                media_type="photo",
                telegram_file_id="stale-photo-file-id",
            ),
            MediaItem(
                file_path=second,
                media_type="photo",
                telegram_file_id="fresh-photo-file-id",
            ),
        ],
        primary_media_type="photo",
    )

    await telegram_bot._send_media(_FakeContext(fake_bot), request_context, info)

    assert len(fake_bot.group_calls) == 2
    assert [item.media for item in fake_bot.group_calls[0]["media"]] == [
        "stale-photo-file-id",
        "fresh-photo-file-id",
    ]
    assert all(
        not isinstance(item.media, str) for item in fake_bot.group_calls[1]["media"]
    )


@pytest.mark.asyncio
async def test_admin_status_includes_performance_summary(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/admin_status")
    context = _FakeContext(_FakeBot())
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )
    telegram_bot.state_store.start_job_metrics(
        job_id="job-1",
        chat_id=77,
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/a/",
    )
    telegram_bot.state_store.mark_job_metrics_started("job-1")
    telegram_bot.state_store.record_download_metrics(
        "job-1",
        download_duration_ms=500,
        instagram_fast_status="succeeded",
        instagram_fast_duration_ms=250,
        instagram_success_path="fast",
    )
    telegram_bot.state_store.record_delivery_metrics("job-1", delivery_duration_ms=200)
    telegram_bot.state_store.finalize_job_metrics("job-1", status="completed")

    await telegram_bot.admin_status_command(update, context)

    text = update.message.replies[-1]
    assert "Производительность:" in text
    assert "Instagram fast-path" in text
    assert "acc" not in text
    assert "proxy" not in text.lower()


@pytest.mark.asyncio
async def test_admin_global_status_reports_all_chats(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/admin_global_status", chat_id=77)
    context = _FakeContext(_FakeBot())
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )
    telegram_bot.state_store.create_job(
        "job-current",
        77,
        "https://www.instagram.com/reel/current/",
        "instagram",
        "failed",
    )
    telegram_bot.state_store.create_job(
        "job-other",
        88,
        "https://x.com/a/status/1",
        "twitter",
        "queued",
    )

    await telegram_bot.admin_global_status_command(update, context)

    text = update.message.replies[-1]
    assert "Глобальный админ-статус:" in text
    assert "Чатов с задачами: 2" in text
    assert "В очереди задач: 1" in text
    assert "Ошибочных задач: 1" in text
    assert "instagram:failed=1" in text
    assert "twitter:queued=1" in text


@pytest.mark.asyncio
async def test_admin_global_status_requires_owner(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/admin_global_status", user_id=2002)
    context = _FakeContext(_FakeBot())
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001
    )

    await telegram_bot.admin_global_status_command(update, context)

    assert update.message.replies[-1] == "Эта команда доступна только владельцу бота."


@pytest.mark.asyncio
async def test_download_failure_records_provider_failure_class(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("https://www.instagram.com/reel/no-accounts/")
    context = _FakeContext(_FakeBot())

    class FakeDownloader:
        def __init__(self):
            self.last_account_health_event = None
            self.last_provider_metrics = ProviderExecutionMetrics(
                provider="instagram",
                failure_class="no_instagram_accounts",
            )

        async def download_video(self, url: str, output_dir: Path):
            raise DownloadError("No Instagram accounts available")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader
    )

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    summary = telegram_bot.state_store.get_performance_summary(77, limit=50)
    assert "no_instagram_accounts" in summary["failure_classes"]
