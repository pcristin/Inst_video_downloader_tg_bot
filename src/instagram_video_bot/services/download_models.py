"""Shared media download models and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional


@dataclass
class MediaItem:
    """Represents one downloaded media file."""

    file_path: Path
    media_type: Literal["video", "photo"]
    caption: Optional[str] = None
    duration: Optional[float] = None


@dataclass
class VideoInfo:
    """Downloaded media package ready for Telegram delivery."""

    file_path: Path
    title: str
    duration: Optional[float] = None
    description: Optional[str] = None
    media_items: List[MediaItem] = field(default_factory=list)
    primary_media_type: Literal["video", "photo"] = "video"
    from_cache: bool = False


class VideoDownloadError(Exception):
    """Base exception for media download failures."""


class AuthenticationError(VideoDownloadError):
    """Raised when provider authentication fails."""


class DownloadError(VideoDownloadError):
    """Raised when media download fails."""
