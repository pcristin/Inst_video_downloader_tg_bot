"""Local media metadata probing helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaMetadata:
    """Video metadata relevant for Telegram delivery."""

    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


def probe_video_metadata(file_path: Path) -> MediaMetadata:
    """Probe video metadata with ffprobe, returning empty metadata on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration,sample_aspect_ratio:stream_side_data=rotation",
                "-of",
                "json",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("ffprobe is not installed; video metadata will be inferred by Telegram")
        return MediaMetadata()
    except Exception as error:
        logger.warning("Failed to probe video metadata for %s: %s", file_path, error)
        return MediaMetadata()

    if result.returncode != 0:
        logger.warning(
            "ffprobe failed for %s",
            file_path,
            extra={"stderr": result.stderr.strip()},
        )
        return MediaMetadata()

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("ffprobe returned invalid JSON for %s", file_path)
        return MediaMetadata()

    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not streams:
        return MediaMetadata()

    stream = streams[0]
    if not isinstance(stream, dict):
        return MediaMetadata()

    width = _safe_int(stream.get("width"))
    height = _safe_int(stream.get("height"))
    duration = _safe_float(stream.get("duration"))

    if _has_right_angle_rotation(stream) and width and height:
        width, height = height, width

    width, height = _apply_sample_aspect_ratio(
        width,
        height,
        str(stream.get("sample_aspect_ratio") or ""),
    )
    return MediaMetadata(duration=duration, width=width, height=height)


def _has_right_angle_rotation(stream: dict[str, Any]) -> bool:
    side_data = stream.get("side_data_list")
    if not isinstance(side_data, list):
        return False
    for item in side_data:
        if not isinstance(item, dict):
            continue
        rotation = _safe_int(item.get("rotation"))
        if rotation is not None and abs(rotation) % 180 == 90:
            return True
    return False


def _apply_sample_aspect_ratio(
    width: Optional[int],
    height: Optional[int],
    sample_aspect_ratio: str,
) -> tuple[Optional[int], Optional[int]]:
    if not width or not height or not sample_aspect_ratio or sample_aspect_ratio in {"1:1", "0:1"}:
        return width, height

    try:
        numerator_raw, denominator_raw = sample_aspect_ratio.split(":", 1)
        numerator = int(numerator_raw)
        denominator = int(denominator_raw)
    except (TypeError, ValueError):
        return width, height

    if numerator <= 0 or denominator <= 0:
        return width, height

    display_width = round(width * numerator / denominator)
    return max(1, display_width), height


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value in (None, "N/A"):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
