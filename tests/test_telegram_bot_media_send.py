from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.telegram_bot import TelegramBot
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
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.message_id = 10
        self.replies = []

    async def reply_text(self, text: str):
        self.replies.append(text)
        return _FakeStatusMessage()


class _FakeUpdate:
    def __init__(self, text: str):
        self.message = _FakeMessage(text)
        self.effective_chat = SimpleNamespace(id=77)


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot
        self.error = None


@pytest.mark.asyncio
async def test_send_single_video_uses_send_video(tmp_path):
    telegram_bot = TelegramBot()
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/a/")

    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"video")

    info = VideoInfo(
        file_path=video_file,
        title="Video title",
        media_items=[MediaItem(file_path=video_file, media_type="video")],
        primary_media_type="video",
    )

    await telegram_bot._send_media(context, update, info)

    assert len(fake_bot.video_calls) == 1
    assert len(fake_bot.photo_calls) == 0
    assert len(fake_bot.group_calls) == 0


@pytest.mark.asyncio
async def test_send_single_photo_uses_send_photo(tmp_path):
    telegram_bot = TelegramBot()
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/p/a/")

    photo_file = tmp_path / "p.jpg"
    photo_file.write_bytes(b"photo")

    info = VideoInfo(
        file_path=photo_file,
        title="Photo title",
        media_items=[MediaItem(file_path=photo_file, media_type="photo")],
        primary_media_type="photo",
    )

    await telegram_bot._send_media(context, update, info)

    assert len(fake_bot.video_calls) == 0
    assert len(fake_bot.photo_calls) == 1
    assert len(fake_bot.group_calls) == 0


@pytest.mark.asyncio
async def test_send_carousel_uses_media_group(tmp_path):
    telegram_bot = TelegramBot()
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/p/a/")

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

    await telegram_bot._send_media(context, update, info)

    assert len(fake_bot.video_calls) == 0
    assert len(fake_bot.photo_calls) == 0
    assert len(fake_bot.group_calls) == 1
    assert len(fake_bot.group_calls[0]["media"]) == 2


@pytest.mark.asyncio
async def test_handle_message_cleans_up_temp_files(tmp_path):
    telegram_bot = TelegramBot()
    fake_bot = _FakeBot()
    context = _FakeContext(fake_bot)
    update = _FakeUpdate("https://www.instagram.com/reel/a/")

    media_file = tmp_path / "cleanup.mp4"
    media_file.write_bytes(b"video")

    async def fake_download_video(url: str, output_dir: Path):
        return VideoInfo(
            file_path=media_file,
            title="cleanup",
            media_items=[MediaItem(file_path=media_file, media_type="video")],
            primary_media_type="video",
        )

    telegram_bot.video_downloader.download_video = fake_download_video

    await telegram_bot.handle_message(update, context)

    assert not media_file.exists()
    assert len(fake_bot.video_calls) == 1


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
    ],
)
def test_instagram_url_pattern_accepts_expanded_routes(url):
    assert TelegramBot.INSTAGRAM_VIDEO_PATTERN.search(url)
