from types import SimpleNamespace

from src.instagram_video_bot.services.state_store import StateStore
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


def test_check_health_fails_when_state_db_has_stale_active_jobs(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    db_path = temp_dir / "state.db"
    store = StateStore(db_path)
    store.create_job(
        "stale-job",
        77,
        "https://www.instagram.com/reel/stale/",
        "instagram",
        "running",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE jobs SET created_at = '2026-01-01T00:00:00+00:00', started_at = '2026-01-01T00:00:00+00:00' WHERE job_id = 'stale-job'"
        )

    fake_settings = SimpleNamespace(
        TEMP_DIR=temp_dir,
        BASE_DIR=tmp_path,
        BOT_TOKEN="token",
        IG_USERNAME="username",
        IG_PASSWORD="password",
        STATE_DB_PATH=db_path,
        INSTAGRAM_PROVIDER_TIMEOUT_SECONDS=180,
    )
    monkeypatch.setattr(health_check, "settings", fake_settings)

    assert health_check.check_health() is False
