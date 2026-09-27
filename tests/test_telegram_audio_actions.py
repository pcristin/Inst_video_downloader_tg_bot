from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_bot import TelegramBot


class AudioBot:
    def __init__(self):
        self.sent = []

    async def send_audio(self, **kwargs):
        self.sent.append(kwargs)


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
