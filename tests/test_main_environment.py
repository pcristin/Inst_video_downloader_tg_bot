import pytest

from src.instagram_video_bot import __main__ as main_module


def test_check_environment_requires_bot_token(monkeypatch):
    monkeypatch.setattr(main_module.settings, "BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(main_module.os.path, "exists", lambda _path: False)

    with pytest.raises(ValueError, match="BOT_TOKEN"):
        main_module.check_environment()


def test_check_environment_allows_missing_instagram_credentials(monkeypatch):
    monkeypatch.setattr(main_module.settings, "BOT_TOKEN", "test-bot-token", raising=False)
    monkeypatch.setattr(main_module.settings, "IG_USERNAME", "", raising=False)
    monkeypatch.setattr(main_module.settings, "IG_PASSWORD", "", raising=False)
    monkeypatch.setattr(main_module.os.path, "exists", lambda _path: False)

    main_module.check_environment()


def test_main_multi_account_startup_does_not_login_before_bot_run(monkeypatch, tmp_path):
    class FakeAccount:
        username = "acc1"

    class FakeManager:
        def __init__(self):
            self.current_account = None

        def reset_old_banned_accounts(self, hours):
            assert hours == 6

        def get_status(self):
            return {"total_accounts": 1, "available_accounts": 1}

        def get_next_account(self):
            return FakeAccount()

        def get_detailed_status(self):
            return "status"

        def rotate_account(self):
            raise AssertionError("startup should not force account login/rotation")

    events = []

    class FakeTelegramBot:
        def run(self):
            events.append("run")

    monkeypatch.setattr(main_module, "setup_logging", lambda: None)
    monkeypatch.setattr(main_module, "check_environment", lambda: None)
    monkeypatch.setattr(main_module.settings, "TEMP_DIR", tmp_path / "temp", raising=False)
    monkeypatch.setattr(
        "src.instagram_video_bot.utils.account_manager.get_account_manager",
        lambda: FakeManager(),
    )
    monkeypatch.setattr(main_module, "TelegramBot", FakeTelegramBot)

    main_module.main()

    assert events == ["run"]
