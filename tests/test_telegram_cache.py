from pathlib import Path

from src.instagram_video_bot.services.state_store import (CachedMediaEntry,
                                                          StateStore)
from src.instagram_video_bot.services.telegram_cache import (
    purge_expired_cache_files, video_info_from_cache)


def test_video_info_from_cache_preserves_media_metadata_and_file_ids(tmp_path):
    media_file = tmp_path / "cached.mp4"
    media_file.write_bytes(b"video")
    cached = CachedMediaEntry(
        title="Cached title",
        media_items=[
            {
                "file_path": str(media_file),
                "media_type": "video",
                "caption": "caption",
                "duration": 12.3,
                "width": 720,
                "height": 1280,
                "telegram_file_id": "tg-file-id",
            }
        ],
        created_at=None,
        expires_at=None,
    )

    info = video_info_from_cache(cached)

    assert info.file_path == media_file
    assert info.title == "Cached title"
    assert info.description == "Cached title"
    assert info.primary_media_type == "video"
    assert info.from_cache is True
    assert info.duration == 12.3
    assert info.media_items[0].telegram_file_id == "tg-file-id"
    assert info.media_items[0].width == 720
    assert info.media_items[0].height == 1280


def test_purge_expired_cache_files_skips_when_cache_disabled(tmp_path):
    store = StateStore(tmp_path / "state.db")
    media_file = tmp_path / "expired.mp4"
    media_file.write_bytes(b"video")

    deleted_paths = purge_expired_cache_files(store, result_cache_enabled=False)

    assert deleted_paths == []
    assert media_file.exists()


def test_purge_expired_cache_files_deletes_paths_returned_by_store(tmp_path):
    media_file = tmp_path / "expired.mp4"
    media_file.write_bytes(b"video")

    class _Store:
        def purge_expired_results(self):
            return [media_file, Path(tmp_path / "missing.mp4")]

    deleted_paths = purge_expired_cache_files(_Store(), result_cache_enabled=True)

    assert deleted_paths == [media_file, Path(tmp_path / "missing.mp4")]
    assert not media_file.exists()
