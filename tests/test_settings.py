from pathlib import Path

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


def test_account_state_file_can_be_configured(tmp_path):
    state_file = tmp_path / "state" / "accounts.json"
    configured = Settings(
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
        ACCOUNT_STATE_FILE=state_file,
    )

    assert configured.ACCOUNT_STATE_FILE == state_file
    assert state_file.parent.is_dir()


def test_account_state_file_defaults_to_repository_root(monkeypatch, tmp_path):
    monkeypatch.delenv("ACCOUNT_STATE_FILE", raising=False)
    configured = Settings(
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
    )

    repository_root = Path(__file__).resolve().parents[1]
    assert configured.ACCOUNT_STATE_FILE == repository_root / "accounts_state.json"
