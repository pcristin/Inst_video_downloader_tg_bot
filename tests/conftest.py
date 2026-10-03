"""Opt-in fixture for unit tests that inject in-process providers."""

import pytest

from src.instagram_video_bot.config.settings import settings


@pytest.fixture(autouse=True)
def isolate_downloader_accounts(monkeypatch):
    """Require provider tests to supply accounts instead of reading deployment secrets."""
    from src.instagram_video_bot.services import video_downloader

    monkeypatch.setattr(video_downloader, "get_account_manager", lambda: None)


@pytest.fixture
def in_process_provider_for_unit_tests(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", False)
