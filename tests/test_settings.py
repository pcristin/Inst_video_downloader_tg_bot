import logging
from pathlib import Path

import pytest

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


def test_local_telegram_api_settings_have_safe_defaults():
    assert Settings.model_fields["TELEGRAM_LOCAL_MODE"].default is False
    assert (
        Settings.model_fields["TELEGRAM_BOT_API_BASE_URL"].default
        == "http://telegram-bot-api:8081/bot"
    )
    assert (
        Settings.model_fields["TELEGRAM_BOT_API_BASE_FILE_URL"].default
        == "http://telegram-bot-api:8081/file/bot"
    )
    assert Settings.model_fields["TELEGRAM_MAX_UPLOAD_BYTES"].default == 500 * 1024 * 1024
    assert (
        Settings.model_fields["TELEGRAM_LARGE_FILE_CACHE_THRESHOLD_BYTES"].default
        == 50 * 1024 * 1024
    )


def test_local_telegram_api_settings_parse_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_LOCAL_MODE", "true")
    monkeypatch.setenv("TELEGRAM_MAX_UPLOAD_BYTES", "123456")

    configured = Settings(
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
    )

    assert configured.TELEGRAM_LOCAL_MODE is True
    assert configured.TELEGRAM_MAX_UPLOAD_BYTES == 123456


def test_settings_does_not_eagerly_load_dotenv() -> None:
    settings_source = (
        Path(__file__).parents[1]
        / "src/instagram_video_bot/config/settings.py"
    ).read_text()

    assert "load_dotenv()" not in settings_source


def test_bot_token_loads_from_secret_file(monkeypatch, tmp_path):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN_FILE", raising=False)
    token_file = tmp_path / "telegram_bot_token"
    token_file.write_text("123456789:TEST_TOKEN_VALUE\n")

    configured = Settings(
        BOT_TOKEN_FILE=token_file,
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
    )

    assert configured.BOT_TOKEN == "123456789:TEST_TOKEN_VALUE"


@pytest.mark.parametrize("file_contents", ["", "   \n", "first\nsecond\n"])
def test_bot_token_file_requires_one_nonempty_line(
    monkeypatch,
    tmp_path,
    file_contents,
):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN_FILE", raising=False)
    token_file = tmp_path / "telegram_bot_token"
    token_file.write_text(file_contents)

    with pytest.raises(ValueError, match="BOT_TOKEN_FILE"):
        Settings(
            BOT_TOKEN_FILE=token_file,
            _env_file=None,
            TEMP_DIR=tmp_path / "temp",
            CACHE_DIR=tmp_path / "cache",
            STATE_DB_PATH=tmp_path / "state.db",
        )


def test_bot_token_file_must_be_readable(monkeypatch, tmp_path):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN_FILE", raising=False)
    with pytest.raises(ValueError, match="BOT_TOKEN_FILE"):
        Settings(
            BOT_TOKEN_FILE=tmp_path / "missing",
            _env_file=None,
            TEMP_DIR=tmp_path / "temp",
            CACHE_DIR=tmp_path / "cache",
            STATE_DB_PATH=tmp_path / "state.db",
        )


def test_bot_token_rejects_two_sources(monkeypatch, tmp_path):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN_FILE", raising=False)
    token_file = tmp_path / "telegram_bot_token"
    token_file.write_text("123456789:FILE_TOKEN\n")

    with pytest.raises(
        ValueError,
        match="only one of BOT_TOKEN and BOT_TOKEN_FILE",
    ):
        Settings(
            BOT_TOKEN="123456789:ENV_TOKEN",
            BOT_TOKEN_FILE=token_file,
            _env_file=None,
            TEMP_DIR=tmp_path / "temp",
            CACHE_DIR=tmp_path / "cache",
            STATE_DB_PATH=tmp_path / "state.db",
        )


def test_invalid_proxy_definition_is_not_logged(monkeypatch, tmp_path, caplog):
    raw_proxy = "SECRET_USER:SECRET_PASS:secret-proxy.example:not-a-port:extra"
    monkeypatch.delenv("PROXIES", raising=False)

    configured = Settings(
        PROXIES=raw_proxy,
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
    )

    with caplog.at_level(logging.WARNING):
        assert configured.get_proxy_list() == []

    assert raw_proxy not in caplog.text
    assert "SECRET_USER" not in caplog.text
    assert "SECRET_PASS" not in caplog.text
    assert "secret-proxy.example" not in caplog.text
    assert "Skipping invalid proxy definition" in caplog.text
