from types import SimpleNamespace

from src.instagram_video_bot.utils import health_check


def test_check_health_uses_sessions_directory_instead_of_removed_cookies_file(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    fake_settings = SimpleNamespace(
        TEMP_DIR=temp_dir,
        BASE_DIR=tmp_path,
        BOT_TOKEN="token",
        IG_USERNAME="username",
        IG_PASSWORD="password",
    )
    monkeypatch.setattr(health_check, "settings", fake_settings)

    assert health_check.check_health() is True


def test_check_health_fails_when_sessions_directory_is_missing(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    fake_settings = SimpleNamespace(
        TEMP_DIR=temp_dir,
        BASE_DIR=tmp_path,
        BOT_TOKEN="token",
        IG_USERNAME="username",
        IG_PASSWORD="password",
    )
    monkeypatch.setattr(health_check, "settings", fake_settings)

    assert health_check.check_health() is False


def test_check_health_accepts_multi_account_mode_without_single_account_credentials(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (tmp_path / "accounts.txt").write_text("user|password|totp\n")

    fake_settings = SimpleNamespace(
        TEMP_DIR=temp_dir,
        BASE_DIR=tmp_path,
        BOT_TOKEN="token",
        IG_USERNAME="",
        IG_PASSWORD="",
    )
    monkeypatch.setattr(health_check, "settings", fake_settings)

    assert health_check.check_health() is True
