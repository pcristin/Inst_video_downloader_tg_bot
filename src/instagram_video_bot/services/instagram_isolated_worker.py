"""Killable subprocess boundary for blocking Instagram downloads."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..config.settings import settings
from .download_models import AuthenticationError, DownloadError, MediaItem, VideoInfo
from .instagram_fast_extractor import InstagramFastExtractorError
from .subprocess_lifecycle import terminate_process_group, wait_for_process


def encode_result(result: VideoInfo | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return json.loads(json.dumps(asdict(result), default=str))


def decode_result(data: dict[str, Any] | None) -> VideoInfo | None:
    if data is None:
        return None
    data = dict(data)
    data["file_path"] = Path(data["file_path"])
    data["media_items"] = [
        MediaItem(**{**item, "file_path": Path(item["file_path"])})
        for item in data["media_items"]
    ]
    return VideoInfo(**data)


def decode_error(response: dict[str, Any]) -> Exception:
    error_type = response.get("error_type")
    message = str(response.get("message", "Instagram worker failed"))
    if error_type == "InstagramFastExtractorError":
        error = InstagramFastExtractorError(message)
        error.endpoint_timings = response.get("endpoint_timings", [])
        error.budget_exhausted = bool(response.get("budget_exhausted", False))
        return error
    if error_type == "AuthenticationError":
        return AuthenticationError(message)
    if error_type == "InstagramAuthError":
        from .instagram_client import InstagramAuthError

        return InstagramAuthError(message)
    return DownloadError(message)


def worker_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.instagram_video_bot.services.instagram_isolated_worker",
    ]


async def run_isolated_instagram_operation(
    payload: dict[str, Any], *, timeout_seconds: float
) -> VideoInfo | None:
    """Run one provider call and kill its entire process group on timeout."""
    from .video_downloader import InstagramProviderTimeoutError

    with tempfile.NamedTemporaryFile(
        prefix="instagram-worker-", suffix=".json", dir=settings.TEMP_DIR, delete=False
    ) as result_file:
        result_path = Path(result_file.name)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "IG_WORKER_RESULT_PATH": str(result_path)},
        )
        assert process.stdin is not None
        process.stdin.write(
            json.dumps({**payload, "_result_file": str(result_path)}).encode()
        )
        process.stdin.close()
        await wait_for_process(
            process,
            timeout_seconds=timeout_seconds,
            timeout_error=InstagramProviderTimeoutError(
                f"Instagram provider timed out after {timeout_seconds:g} seconds"
            ),
        )
        if process.returncode != 0:
            raise DownloadError("Instagram worker exited unexpectedly")
        try:
            response = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise DownloadError(
                "Instagram worker returned an invalid result"
            ) from error
    finally:
        if process is not None:
            await terminate_process_group(process)
        result_path.unlink(missing_ok=True)
    if response.get("ok"):
        return decode_result(response.get("result"))
    raise decode_error(response)


def execute_payload(payload: dict[str, Any]) -> VideoInfo | None:
    from .video_downloader import VideoDownloader

    downloader = VideoDownloader()
    action = payload["action"]
    url = str(payload["url"])
    output_dir = Path(payload["output_dir"])
    if action == "fast":
        return downloader.instagram_adapter.download_with_fast_method(url, output_dir)
    if action == "public":
        return downloader.instagram_adapter.download_with_public_ytdlp(url, output_dir)
    if action == "single":
        return downloader._download_with_single_account_sync(url, output_dir)
    if action == "leased":
        account_data = dict(payload["account"])
        if account_data.get("session_file"):
            account_data["session_file"] = Path(account_data["session_file"])
        return downloader._download_with_leased_account_sync(
            SimpleNamespace(**account_data), url, output_dir
        )
    raise ValueError("Unsupported Instagram worker action")


def main() -> None:
    from contextlib import redirect_stdout

    payload: dict[str, Any] = {}
    try:
        payload = json.loads(sys.stdin.buffer.read())
        with redirect_stdout(sys.stderr):
            result = execute_payload(payload)
        response = {"ok": True, "result": encode_result(result)}
    except Exception as error:
        response = {
            "ok": False,
            "error_type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, InstagramFastExtractorError):
            response.update(
                error_type="InstagramFastExtractorError",
                endpoint_timings=error.endpoint_timings,
                budget_exhausted=error.budget_exhausted,
            )
    result_file = os.environ.get("IG_WORKER_RESULT_PATH") or payload.get("_result_file")
    if not result_file:
        raise RuntimeError("Missing Instagram worker result path")
    result_path = Path(result_file)
    result_path.write_text(json.dumps(response), encoding="utf-8")


if __name__ == "__main__":
    main()
