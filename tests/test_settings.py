from src.instagram_video_bot.config.settings import Settings


def test_empty_storage_chat_id_uses_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MEDIA_STORAGE_CHAT_ID", "")

    settings = Settings(
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state" / "bot.db",
    )

    assert settings.TELEGRAM_MEDIA_STORAGE_CHAT_ID is None
