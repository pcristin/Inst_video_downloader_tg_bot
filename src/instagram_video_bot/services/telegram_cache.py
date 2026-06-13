"""Telegram result-cache conversion and cleanup helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .download_models import MediaItem, VideoInfo
from .state_store import CachedMediaEntry

logger = logging.getLogger(__name__)


def video_info_from_cache(cached: CachedMediaEntry) -> VideoInfo:
    """Convert a cached state-store entry back into a downloadable media package."""

    media_items = [
        MediaItem(
            file_path=Path(item["file_path"]),
            media_type=item["media_type"],
            caption=item.get("caption"),
            duration=item.get("duration"),
            width=item.get("width"),
            height=item.get("height"),
            telegram_file_id=item.get("telegram_file_id"),
        )
        for item in cached.media_items
    ]
    primary = media_items[0]
    return VideoInfo(
        file_path=primary.file_path,
        title=cached.title,
        description=cached.title,
        duration=primary.duration,
        media_items=media_items,
        primary_media_type=primary.media_type,
        from_cache=True,
    )


def purge_expired_cache_files(
    state_store: Any, *, result_cache_enabled: bool
) -> list[Path]:
    """Delete files for expired state-store cache rows and return attempted paths."""

    if not result_cache_enabled:
        return []
    expired_paths = state_store.purge_expired_results()
    for path in expired_paths:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to delete expired cache file %s", path)
    return expired_paths
