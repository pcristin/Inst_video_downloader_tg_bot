"""Local media validation, upload inputs, and post-staging cleanup."""

from __future__ import annotations

import logging
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .download_models import MediaItem, VideoDownloadError

logger = logging.getLogger(__name__)

CLOUD_BOT_API_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024


def effective_upload_limit_bytes(local_mode: bool, configured_limit: int) -> int:
    """Return the upload ceiling for the selected Telegram API endpoint."""
    if local_mode:
        return configured_limit
    return min(configured_limit, CLOUD_BOT_API_UPLOAD_LIMIT_BYTES)


def validate_media_path(file_path: Path, *, max_upload_bytes: int) -> None:
    """Require an existing, non-empty media file within the upload ceiling."""
    if not file_path.exists():
        raise VideoDownloadError(f"Media file not found at {file_path}")
    size = file_path.stat().st_size
    if size == 0:
        raise VideoDownloadError(f"Media file is empty: {file_path}")
    if size > max_upload_bytes:
        raise VideoDownloadError(
            "Media file is too large: "
            f"{size} bytes exceeds the {max_upload_bytes}-byte upload limit"
        )


def _grant_shared_group_access(file_path: Path, *, shared_root: Path) -> Path:
    """Make one in-root media path readable by the Local Bot API group."""
    resolved_file = file_path.resolve()
    resolved_root = shared_root.resolve()
    try:
        resolved_file.relative_to(resolved_root)
    except ValueError as exc:
        raise VideoDownloadError(
            f"Media file is outside the shared Telegram directory: {file_path}"
        ) from exc

    directory = resolved_file.parent
    while True:
        directory.chmod(
            stat.S_IMODE(directory.stat().st_mode) | stat.S_IRGRP | stat.S_IXGRP
        )
        if directory == resolved_root:
            break
        directory = directory.parent

    resolved_file.chmod(stat.S_IMODE(resolved_file.stat().st_mode) | stat.S_IRGRP)
    return resolved_file


@contextmanager
def media_input(
    file_path: Path,
    *,
    local_mode: bool,
    max_upload_bytes: int,
    shared_root: Path | None = None,
) -> Iterator[Path | BinaryIO]:
    """Yield a local path for Local Bot API or a fresh cloud upload stream."""
    validate_media_path(file_path, max_upload_bytes=max_upload_bytes)
    if local_mode:
        yield _grant_shared_group_access(
            file_path,
            shared_root=shared_root or file_path.parent,
        )
        return
    with file_path.open("rb") as media_file:
        yield media_file


def cleanup_large_staged_files(
    media_items: list[MediaItem], *, threshold_bytes: int
) -> list[Path]:
    """Delete large local files only after Telegram assigned reusable IDs."""
    removed: list[Path] = []
    for item in media_items:
        if not item.telegram_file_id or not item.file_path.exists():
            continue
        try:
            if item.file_path.stat().st_size <= threshold_bytes:
                continue
            item.file_path.unlink()
            removed.append(item.file_path)
        except OSError as exc:
            logger.warning("Failed to clean up staged media %s: %s", item.file_path, exc)
    return removed
