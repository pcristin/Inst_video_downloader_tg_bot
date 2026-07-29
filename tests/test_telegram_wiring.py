from src.instagram_video_bot.services import telegram_wiring


def test_application_builder_uses_cloud_api_by_default(monkeypatch):
    monkeypatch.setattr(telegram_wiring.settings, "BOT_TOKEN", "123:test", raising=False)
    monkeypatch.setattr(
        telegram_wiring.settings, "TELEGRAM_LOCAL_MODE", False, raising=False
    )

    application = telegram_wiring._build_application_builder().build()

    assert application.bot.local_mode is False
    assert str(application.bot.base_url) == "https://api.telegram.org/bot123:test"


def test_application_builder_uses_local_api_urls(monkeypatch):
    monkeypatch.setattr(telegram_wiring.settings, "BOT_TOKEN", "123:test", raising=False)
    monkeypatch.setattr(
        telegram_wiring.settings, "TELEGRAM_LOCAL_MODE", True, raising=False
    )
    monkeypatch.setattr(
        telegram_wiring.settings,
        "TELEGRAM_BOT_API_BASE_URL",
        "http://local-api:8081/bot",
        raising=False,
    )
    monkeypatch.setattr(
        telegram_wiring.settings,
        "TELEGRAM_BOT_API_BASE_FILE_URL",
        "http://local-api:8081/file/bot",
        raising=False,
    )

    application = telegram_wiring._build_application_builder().build()

    assert application.bot.local_mode is True
    assert str(application.bot.base_url) == "http://local-api:8081/bot123:test"
    assert str(application.bot.base_file_url) == "http://local-api:8081/file/bot123:test"
