"""Normalize Instagram videos for Telegram's iOS playback pipeline."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any

from .download_models import MediaItem, VideoInfo

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class VideoProbe:
    """Media properties needed to choose and validate normalization."""

    video_codec: str
    pixel_format: str
    audio_codecs: tuple[str, ...]
    duration: float | None
    width: int | None
    height: int | None

    @property
    def is_ios_compatible(self) -> bool:
        """Return whether stream codecs can be preserved for Telegram iOS."""
        return (
            self.video_codec == "h264"
            and self.pixel_format == "yuv420p"
            and all(codec == "aac" for codec in self.audio_codecs)
        )


def normalize_instagram_media(video_info: VideoInfo) -> VideoInfo:
    """Return Instagram media with verified iOS-safe video paths when possible."""
    normalized_items: list[MediaItem] = []
    replacements: dict[Path, Path] = {}
    changed = False

    for item in video_info.media_items:
        if item.media_type != "video":
            normalized_items.append(item)
            continue

        normalized_item = _normalize_video_item(item)
        normalized_items.append(normalized_item)
        if normalized_item.file_path != item.file_path:
            replacements[item.file_path] = normalized_item.file_path
            changed = True

    if not changed:
        return video_info

    return replace(
        video_info,
        file_path=replacements.get(video_info.file_path, video_info.file_path),
        duration=(
            normalized_items[0].duration
            if len(normalized_items) == 1 and normalized_items[0].media_type == "video"
            else video_info.duration
        ),
        media_items=normalized_items,
    )


def _normalize_video_item(item: MediaItem) -> MediaItem:
    source = item.file_path
    candidate = source.with_name(f"{source.stem}.ios.mp4")
    started_at = perf_counter()
    outcome = "normalization_failed"
    reason = "unknown"

    try:
        source_probe = _probe_video(source)
        source_decodes = _decode_is_valid(source)
        remux = source_probe.is_ios_compatible and source_decodes
        outcome = "remuxed" if remux else "transcoded"
        reason = (
            "compatible_streams"
            if remux
            else _compatibility_reason(source_probe, source_decodes)
        )
        candidate.unlink(missing_ok=True)

        command = _remux_command(source) if remux else _transcode_command(source)
        if not _run_ffmpeg(command, candidate):
            raise RuntimeError("ffmpeg command failed")
        if not candidate.exists() or candidate.stat().st_size <= 0:
            raise RuntimeError("ffmpeg produced an empty output")

        candidate_probe = _probe_video(candidate)
        if not candidate_probe.is_ios_compatible:
            raise RuntimeError("normalized output is not iOS compatible")
        if not _decode_is_valid(candidate):
            raise RuntimeError("normalized output failed decode validation")

        logger.info(
            "Instagram video normalization completed",
            extra={
                "normalization_outcome": outcome,
                "normalization_reason": reason,
                "normalization_duration_ms": int((perf_counter() - started_at) * 1000),
                "input_size_bytes": source.stat().st_size,
                "output_size_bytes": candidate.stat().st_size,
            },
        )
        return replace(
            item,
            file_path=candidate,
            duration=candidate_probe.duration,
            width=candidate_probe.width,
            height=candidate_probe.height,
            telegram_file_id=None,
        )
    except Exception as error:
        candidate.unlink(missing_ok=True)
        logger.warning(
            "Instagram video normalization failed; using original media",
            extra={
                "normalization_outcome": "normalization_failed",
                "normalization_reason": reason,
                "normalization_duration_ms": int((perf_counter() - started_at) * 1000),
                "error_class": error.__class__.__name__,
            },
        )
        return item


def _probe_video(path: Path) -> VideoProbe:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,pix_fmt,width,height,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("ffprobe failed")

    payload = json.loads(result.stdout)
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        raise RuntimeError("ffprobe returned no streams")

    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise RuntimeError("media has no video stream")

    audio_codecs = tuple(
        str(stream.get("codec_name") or "").lower()
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    )
    format_data = payload.get("format") if isinstance(payload, dict) else None
    format_duration = (
        format_data.get("duration") if isinstance(format_data, dict) else None
    )
    duration = _optional_float(video_stream.get("duration")) or _optional_float(
        format_duration
    )

    return VideoProbe(
        video_codec=str(video_stream.get("codec_name") or "").lower(),
        pixel_format=str(video_stream.get("pix_fmt") or "").lower(),
        audio_codecs=audio_codecs,
        duration=duration,
        width=_optional_int(video_stream.get("width")),
        height=_optional_int(video_stream.get("height")),
    )


def _decode_is_valid(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _remux_command(source: Path) -> list[str]:
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
    ]


def _transcode_command(source: Path) -> list[str]:
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
    ]


def _run_ffmpeg(command: list[str], output_path: Path) -> bool:
    try:
        result = subprocess.run(
            [*command, str(output_path)],
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _compatibility_reason(probe: VideoProbe, source_decodes: bool) -> str:
    reasons: list[str] = []
    if probe.video_codec != "h264":
        reasons.append(f"video_codec_{probe.video_codec or 'unknown'}")
    if probe.pixel_format != "yuv420p":
        reasons.append(f"pixel_format_{probe.pixel_format or 'unknown'}")
    if any(codec != "aac" for codec in probe.audio_codecs):
        reasons.append("audio_codec_incompatible")
    if not source_decodes:
        reasons.append("source_decode_failed")
    return ",".join(reasons) or "transcode_required"


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
