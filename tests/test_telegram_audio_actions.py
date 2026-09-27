import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import TimedOut

from src.instagram_video_bot.services.audio_conversion import AudioConversionError
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import TelegramBot


class AudioBot:
    def __init__(self):
        self.sent = []
        self.messages = []

    async def send_audio(self, **kwargs):
        self.sent.append(kwargs)

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class AudioQuery:
    def __init__(self, user_id=1001, language_code=None):
        self.data = "audio:req-1"
        self.from_user = SimpleNamespace(id=user_id, language_code=language_code)
        self.message = SimpleNamespace(message_id=42)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


def audio_update(query):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=77),
        effective_user=query.from_user,
    )


def saved_video(store, path: Path):
    url = "https://www.instagram.com/reel/a/"
    store.create_job("job-1", 77, url, "instagram", "completed")
    store.create_request(
        "req-1", "job-1", 77, 1001, "Alice", "instagram", url, "completed"
    )
    store.save_cached_result(
        77,
        url,
        "instagram",
        "Video",
        [{"file_path": str(path), "media_type": "video"}],
        3600,
    )


@pytest.mark.asyncio
async def test_audio_action_sends_converted_cached_video(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)
    telegram = AudioBot()
    query = AudioQuery()

    async def convert(source_path, output_path):
        assert source_path == source
        output_path.write_bytes(b"audio")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.convert_video_to_mp3",
        convert,
        raising=False,
    )
    await bot.audio_action_callback_handler(
        audio_update(query), SimpleNamespace(bot=telegram)
    )
    await bot._audio_action_tasks["req-1"]

    assert len(telegram.sent) == 1
    assert telegram.sent[0]["chat_id"] == 77
    assert telegram.sent[0]["reply_to_message_id"] == 42
    assert not Path(telegram.sent[0]["audio"].name).exists()


@pytest.mark.asyncio
async def test_audio_action_rejects_another_user(tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)
    telegram = AudioBot()
    query = AudioQuery(user_id=1002)

    await bot.audio_action_callback_handler(
        audio_update(query), SimpleNamespace(bot=telegram)
    )

    assert telegram.sent == []
    assert query.answers[-1] == "This action belongs to another request."


@pytest.mark.asyncio
async def test_audio_action_explains_expired_cache_in_russian(tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    source.unlink()
    bot = TelegramBot(state_store=store)
    query = AudioQuery(language_code="ru")

    await bot.audio_action_callback_handler(
        audio_update(query), SimpleNamespace(bot=AudioBot())
    )

    assert query.answers[-1] == "Аудио больше недоступно. Отправьте ссылку ещё раз."


@pytest.mark.asyncio
async def test_audio_action_rejects_another_click_while_preparing(tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)
    bot._active_audio_requests.add("req-1")
    telegram = AudioBot()
    query = AudioQuery()

    await bot.audio_action_callback_handler(
        audio_update(query), SimpleNamespace(bot=telegram)
    )

    assert query.answers[-1] == "Audio is already being prepared."
    assert telegram.sent == []


@pytest.mark.asyncio
async def test_audio_action_rate_limits_repeated_conversions(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)
    sources = []

    def deny_rate_limit(_user_id, *, source):
        sources.append(source)
        return {"allowed": False, "retry_after_seconds": 60}

    monkeypatch.setattr(bot, "_consume_user_rate_limit", deny_rate_limit)
    telegram = AudioBot()
    query = AudioQuery()

    await bot.audio_action_callback_handler(
        audio_update(query), SimpleNamespace(bot=telegram)
    )

    assert sources == ["audio"]
    assert telegram.sent == []
    assert "Too many requests" in query.answers[-1]


@pytest.mark.asyncio
async def test_audio_action_reports_conversion_failure(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)
    telegram = AudioBot()

    async def fail_conversion(_source, _output):
        raise AudioConversionError("no audio stream")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.convert_video_to_mp3",
        fail_conversion,
    )

    await bot.audio_action_callback_handler(
        audio_update(AudioQuery()), SimpleNamespace(bot=telegram)
    )
    await bot._audio_action_tasks["req-1"]

    assert telegram.sent == []
    assert (
        telegram.messages[0]["text"]
        == "Could not prepare audio. Please try the link again."
    )
    assert bot._active_audio_requests == set()


@pytest.mark.asyncio
async def test_audio_action_swallows_failed_fallback_message(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)

    async def fail_conversion(_source, _output):
        raise AudioConversionError("no audio stream")

    class UnreachableBot(AudioBot):
        async def send_message(self, **kwargs):
            raise TimedOut("network unavailable")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.convert_video_to_mp3",
        fail_conversion,
    )

    await bot.audio_action_callback_handler(
        audio_update(AudioQuery()), SimpleNamespace(bot=UnreachableBot())
    )
    await bot._audio_action_tasks["req-1"]

    assert bot._active_audio_requests == set()


@pytest.mark.asyncio
async def test_audio_conversion_runs_after_callback_returns(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.db")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    saved_video(store, source)
    bot = TelegramBot(state_store=store)
    telegram = AudioBot()
    started = asyncio.Event()
    release = asyncio.Event()

    async def convert(_source, output):
        started.set()
        await release.wait()
        output.write_bytes(b"audio")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.convert_video_to_mp3", convert
    )
    query = AudioQuery()

    await bot.audio_action_callback_handler(
        audio_update(query), SimpleNamespace(bot=telegram)
    )

    task = bot._audio_action_tasks["req-1"]
    assert not task.done()
    assert query.answers == ["Preparing audio…"]
    await asyncio.wait_for(started.wait(), timeout=1)
    assert telegram.sent == []
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert len(telegram.sent) == 1
    assert bot._active_audio_requests == set()
