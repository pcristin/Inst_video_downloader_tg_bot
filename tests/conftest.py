"""Keep unit tests on injected in-process providers by default."""

import pytest

from src.instagram_video_bot.config.settings import settings


@pytest.fixture
def in_process_provider_for_unit_tests(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_ISOLATED_WORKERS_ENABLED", False)
