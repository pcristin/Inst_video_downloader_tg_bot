"""Opt-in fixture for unit tests that inject in-process providers."""

import pytest

from src.instagram_video_bot.config.settings import settings


@pytest.fixture
def in_process_provider_for_unit_tests(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", False)
