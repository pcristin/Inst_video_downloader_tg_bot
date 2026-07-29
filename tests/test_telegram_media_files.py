from pathlib import Path

import pytest

from src.instagram_video_bot.services.download_models import (
    MediaItem,
    VideoDownloadError,
)
from src.instagram_video_bot.services.telegram_media_files import (
    cleanup_large_staged_files,
    effective_upload_limit_bytes,
    media_input,
)


def test_cloud_upload_limit_stays_at_50_mib():
    assert effective_upload_limit_bytes(False, 500 * 1024 * 1024) == 50 * 1024 * 1024


def test_local_upload_limit_uses_configured_500_mib():
    assert effective_upload_limit_bytes(True, 500 * 1024 * 1024) == 500 * 1024 * 1024


def test_media_input_returns_path_in_local_mode(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")

    with media_input(media_file, local_mode=True, max_upload_bytes=100) as value:
        assert value == media_file
        assert isinstance(value, Path)


def test_media_input_opens_binary_stream_in_cloud_mode(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")

    with media_input(media_file, local_mode=False, max_upload_bytes=100) as value:
        assert value.read() == b"video"

    assert value.closed is True


def test_media_input_rejects_file_above_limit(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"12345")

    with pytest.raises(VideoDownloadError, match="file is too large"):
        with media_input(media_file, local_mode=True, max_upload_bytes=4):
            pass


def test_cleanup_large_staged_files_requires_file_id_and_threshold(tmp_path):
    large_staged = tmp_path / "large.mp4"
    large_unstaged = tmp_path / "unstaged.mp4"
    small_staged = tmp_path / "small.mp4"
    large_staged.write_bytes(b"12345")
    large_unstaged.write_bytes(b"12345")
    small_staged.write_bytes(b"123")

    removed = cleanup_large_staged_files(
        [
            MediaItem(
                file_path=large_staged,
                media_type="video",
                telegram_file_id="large-id",
            ),
            MediaItem(file_path=large_unstaged, media_type="video"),
            MediaItem(
                file_path=small_staged,
                media_type="video",
                telegram_file_id="small-id",
            ),
        ],
        threshold_bytes=4,
    )

    assert removed == [large_staged]
    assert large_staged.exists() is False
    assert large_unstaged.exists() is True
    assert small_staged.exists() is True
