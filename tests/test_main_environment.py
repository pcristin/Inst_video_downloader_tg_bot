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
