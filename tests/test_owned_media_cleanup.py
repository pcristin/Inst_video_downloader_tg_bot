from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.telegram_media_sender import TelegramMediaSender
from src.instagram_video_bot.services.telegram_cache import purge_expired_cache_files


@pytest.mark.parametrize("prefix", ["public-", "auth-first-"])
def test_delivery_removes_empty_owned_directory(tmp_path, monkeypatch, prefix):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    job = tmp_path / "job"
    owned = job / f"{prefix}abc"
    owned.mkdir(parents=True)
    files = [owned / "1.jpg", owned / "2.jpg"]
    for file in files:
        file.write_bytes(b"x")
    TelegramMediaSender.cleanup_files([files[0]])
    assert owned.exists()
    TelegramMediaSender.cleanup_files([files[1]])
    assert not owned.exists()
    assert job.is_dir()
    assert tmp_path.is_dir()


def test_expired_cache_prunes_owned_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    owned = tmp_path / "telegram-restage-abc"
    owned.mkdir()
    media = owned / "v.mp4"
    media.write_bytes(b"x")
    store = SimpleNamespace(purge_expired_results=lambda: [media])
    assert purge_expired_cache_files(store, result_cache_enabled=True) == [media]
    assert not owned.exists()


def test_cleanup_preserves_unowned_and_external_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path / "managed")
    folder = tmp_path / "public-external"
    folder.mkdir()
    file = folder / "v.mp4"
    file.write_bytes(b"x")
    TelegramMediaSender.cleanup_files([file])
    assert folder.is_dir()
