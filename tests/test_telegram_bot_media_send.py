import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.job_manager import SharedJob, RequestRecord
from src.instagram_video_bot.services.telegram_bot import RequestContext, TelegramBot
from src.instagram_video_bot.services.video_downloader import MediaItem, VideoInfo


class _FakeBot:
    def __init__(self):
        self.video_calls = []
        self.photo_calls = []
        self.group_calls = []

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)

    async def send_photo(self, **kwargs):
        self.photo_calls.append(kwargs)

    async def send_media_group(self, **kwargs):
        self.group_calls.append(kwargs)


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
    def __init__(self, text: str, message_id: int = 10):
        self.text = text
        self.message_id = message_id
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
    ):
        self.message = _FakeMessage(text, message_id=message_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_user = SimpleNamespace(id=user_id, username=username, full_name=full_name)


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

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video", fake_download_video)

    await telegram_bot.handle_message(update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert not media_file.exists()
    assert len(fake_bot.video_calls) == 1
    assert update.message.replies == ["🕓 Instagram accepted. Starting shortly."]
    assert update.message.status_messages[0].texts == ["⬇️ Instagram downloading..."]
    assert update.message.status_messages[0].deleted is True


@pytest.mark.asyncio
async def test_duplicate_suppressed_requests_send_media_only_once(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    first_update = _FakeUpdate("https://www.instagram.com/reel/a/", user_id=1001, username="alice", full_name="Alice", message_id=10)
    second_update = _FakeUpdate("https://www.instagram.com/reel/a/", user_id=1002, username="bob", full_name="Bob", message_id=11)

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

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video", fake_download_video)

    await telegram_bot.handle_message(first_update, context)
    await telegram_bot.handle_message(second_update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert len(fake_bot.video_calls) == 1
    assert fake_bot.video_calls[0]["reply_to_message_id"] == 10
    assert first_update.message.status_messages[0].deleted is True
    assert second_update.message.status_messages[0].deleted is True
    assert second_update.message.replies == ["🔁 Instagram already in progress. Waiting for the shared result."]
    assert second_update.message.status_messages[0].texts == []


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
async def test_shared_delivery_handoffs_to_another_requester_on_send_failure(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    context = _FakeContext(_FakeBot())
    first_update = _FakeUpdate("https://www.instagram.com/reel/a/", user_id=1001, username="alice", full_name="Alice", message_id=10)
    second_update = _FakeUpdate("https://www.instagram.com/reel/a/", user_id=1002, username="bob", full_name="Bob", message_id=11)

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

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.RESULT_CACHE_ENABLED", False)
    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.VideoDownloader.download_video", fake_download_video)
    monkeypatch.setattr(TelegramBot, "_send_media", fake_send_media)

    await telegram_bot.handle_message(first_update, context)
    await telegram_bot.handle_message(second_update, context)
    await asyncio.gather(*telegram_bot.active_request_tasks.values())

    assert delivered_message_ids == [11]
    assert first_update.message.status_messages[0].deleted is True
    assert second_update.message.status_messages[0].deleted is True
    assert first_update.message.status_messages[0].texts == ["⬇️ Instagram downloading..."]
    assert second_update.message.status_messages[0].texts == []


@pytest.mark.asyncio
async def test_owner_only_quiet_command_requires_owner(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/quiet on")
    context = _FakeContext(_FakeBot(), args=["on"])

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 9999)

    await telegram_bot.quiet_command(update, context)

    assert update.message.replies[-1] == "This command is only available to the bot owner."


@pytest.mark.asyncio
async def test_owner_can_toggle_quiet_mode(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/quiet on")
    context = _FakeContext(_FakeBot(), args=["on"])

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001)

    await telegram_bot.quiet_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(update.effective_chat.id)
    assert settings_row["quiet_mode"] is True
    assert update.message.replies[-1] == "Quiet mode enabled."


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
        requesters={"req-1": RequestRecord(request_id="req-1", chat_id=77, user_id=1001, user_label="alice")},
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
        requesters={"req-1": RequestRecord(request_id="req-1", chat_id=77, user_id=1001, user_label="alice")},
    )

    await telegram_bot._on_job_state_change(job)

    assert status_message.texts == []


@pytest.mark.asyncio
async def test_owner_can_toggle_stats_mode(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/statsmode off")
    context = _FakeContext(_FakeBot(), args=["off"])

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001)

    await telegram_bot.statsmode_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(update.effective_chat.id)
    assert settings_row["stats_enabled"] is False
    assert update.message.replies[-1] == "Stats mode disabled."


@pytest.mark.asyncio
async def test_owner_can_override_chat_limit(monkeypatch, tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/chatlimit 4")
    context = _FakeContext(_FakeBot(), args=["4"])

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001)

    await telegram_bot.chatlimit_command(update, context)

    settings_row = telegram_bot.state_store.ensure_group_settings(update.effective_chat.id)
    snapshot = telegram_bot.job_manager.get_snapshot(update.effective_chat.id)
    assert settings_row["chat_max_concurrent_jobs"] == 4
    assert snapshot["chat_limit"] == 4
    assert update.message.replies[-1] == "Chat concurrency limit set to 4."


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

    monkeypatch.setattr("src.instagram_video_bot.services.telegram_bot.settings.BOT_OWNER_USER_ID", 1001)

    await telegram_bot.admin_status_command(update, context)

    reply = update.message.replies[-1]
    assert "Quiet mode: on" in reply
    assert "Duplicate suppression: off" in reply
    assert "Stats mode: off" in reply
    assert "Chat concurrency limit: 5" in reply
    assert "Per-user limit: 2" in reply
