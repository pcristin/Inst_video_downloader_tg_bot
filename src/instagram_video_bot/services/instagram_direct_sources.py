"""Saved-session metadata and bounded preflight for Telegram URL delivery.

Only complete, Telegram-compatible packages leave this module. It never logs
source URLs, downloads media files, authenticates, or refreshes a session.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from .download_models import DownloadError, MediaItem, VideoInfo
from .instagram_client import InstagramAuthError, InstagramClient


def _ineligible() -> DownloadError:
    return DownloadError("direct_candidate_ineligible")


def _raise_provider_error(_client, error: Exception) -> None:
    """Prevent instagrapi private_request from resolving challenges implicitly."""
    raise error


def _trusted_url(value: object) -> str:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise _ineligible()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        valid = (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and any(
                host == domain or host.endswith("." + domain)
                for domain in ("cdninstagram.com", "fbcdn.net")
            )
        )
    except ValueError:
        raise _ineligible() from None
    if not valid:
        raise _ineligible()
    return value


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _ineligible()
    return remaining


def _probe_video(url: str, deadline: float) -> tuple[int, int, float]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-rw_timeout",
                "8000000",
                "-protocol_whitelist",
                "https,tls,tcp",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=min(12, _remaining(deadline)),
        )
        if result.returncode:
            raise _ineligible()
        probe = json.loads(result.stdout)
        streams = probe.get("streams", [])
        videos = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if (
            len(videos) != 1
            or not audio
            or any(stream.get("codec_name") != "aac" for stream in audio)
        ):
            raise _ineligible()
        video = videos[0]
        if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
            raise _ineligible()
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        duration = float(
            probe.get("format", {}).get("duration") or video.get("duration") or 0
        )
        if width <= 0 or height <= 0 or not math.isfinite(duration) or duration <= 0:
            raise _ineligible()
        return width, height, duration
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        # ffprobe exceptions can contain the full signed URL in their command.
        raise _ineligible() from None


def _head_source(url: str, media_type: str, deadline: float) -> None:
    remaining = _remaining(deadline)
    response = None
    try:
        response = requests.head(
            url,
            allow_redirects=False,
            timeout=(min(3, remaining / 2), min(5, remaining / 2)),
        )
        if response.status_code != 200:
            raise _ineligible()
        length = int(response.headers.get("Content-Length", "0"))
        mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        maximum, expected = (
            (5_000_000, "image/jpeg")
            if media_type == "photo"
            else (20_000_000, "video/mp4")
        )
        if not 0 < length <= maximum or mime != expected:
            raise _ineligible()
    except (requests.RequestException, TypeError, ValueError):
        raise _ineligible() from None
    finally:
        if response is not None:
            response.close()


def extract_direct_sources(account, url: str, output_dir: Path) -> VideoInfo:
    """Extract a complete remote candidate using only an existing saved session."""
    # Imported here because the downloader also calls this module from its worker.
    from .video_downloader import _SavedSessionInstagramClient

    deadline = time.monotonic() + 18
    session_file = Path(account.session_file)
    if not session_file.is_file():
        raise DownloadError("saved_session_unavailable")
    wrapper = _SavedSessionInstagramClient(
        username=account.username,
        password="",
        session_file=session_file,
        proxy=account.proxy,
        totp_secret=None,
    )
    client = wrapper.client
    client.handle_exception = _raise_provider_error
    try:
        # load_settings initializes the saved cookies/device without timeline or
        # login requests. Do not call wrapper.login() or its session validator.
        if not client.load_settings(session_file):
            raise DownloadError("saved_session_unavailable")
    except (OSError, ValueError, TypeError):
        raise DownloadError("saved_session_unavailable") from None
    try:
        pk = client.media_pk_from_url(url)
        response = client.private_request(f"media/{pk}/info/")
    except (PleaseWaitFewMinutes, FeedbackRequired) as error:
        raise InstagramAuthError("rate_limited") from error
    except (LoginRequired, ChallengeRequired) as error:
        raise InstagramAuthError("auth_challenge") from error
    except BadPassword as error:
        raise InstagramAuthError("invalid username or password") from error
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("items"), list)
        or not response["items"]
    ):
        raise DownloadError("direct_metadata_unavailable")
    raw = response["items"][0]
    if not isinstance(raw, dict):
        raise DownloadError("direct_metadata_unavailable")
    caption = raw.get("caption")
    title = (
        caption.get("text", "")
        if isinstance(caption, dict)
        else caption if isinstance(caption, str) else raw.get("caption_text", "")
    ) or ""
    raw_items = raw.get("carousel_media") if raw.get("media_type") == 8 else [raw]
    if not isinstance(raw_items, list) or not raw_items:
        raise _ineligible()
    items = []
    try:
        for index, raw_item in enumerate(raw_items):
            if raw_item.get("media_type") == 2:
                source = _trusted_url(InstagramClient._pick_video_url(raw_item))
                width, height, duration = _probe_video(source, deadline)
                media_type = "video"
            elif raw_item.get("media_type") == 1:
                source = _trusted_url(InstagramClient._pick_image_url(raw_item))
                candidates = raw_item.get("image_versions2", {}).get("candidates", [])
                selected = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.get("url") == source
                    ),
                    {},
                )
                width = int(
                    selected.get("width") or raw_item.get("original_width") or 0
                )
                height = int(
                    selected.get("height") or raw_item.get("original_height") or 0
                )
                if (
                    min(width, height) <= 0
                    or width + height > 10000
                    or max(width, height) / min(width, height) > 20
                ):
                    raise _ineligible()
                duration, media_type = None, "photo"
            else:
                raise _ineligible()
            items.append(
                MediaItem(
                    file_path=output_dir / ".direct_sources" / str(index),
                    remote_url=source,
                    media_type=media_type,
                    caption=str(title),
                    width=width,
                    height=height,
                    duration=duration,
                )
            )
    except (TypeError, ValueError, AttributeError, StopIteration):
        raise _ineligible() from None
    # Probe the whole package before HEAD/staging; a silent album member must
    # disqualify the entire package before anything can reach Telegram.
    for item in items:
        _head_source(item.remote_url, item.media_type, deadline)
    _remaining(deadline)
    return VideoInfo(
        file_path=items[0].file_path,
        title=str(title),
        description=str(title),
        duration=items[0].duration,
        media_items=items,
        primary_media_type=items[0].media_type,
        instagram_success_path="direct_url",
        instagram_metadata_reused=True,
    )
