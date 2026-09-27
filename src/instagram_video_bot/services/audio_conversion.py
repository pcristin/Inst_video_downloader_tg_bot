"""Bounded conversion of a downloaded video into an MP3 audio file."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from .subprocess_lifecycle import terminate_process_group, wait_for_process


class AudioConversionError(Exception):
    """The source has no usable audio or ffmpeg could not convert it."""


async def convert_video_to_mp3(
    source: Path, output: Path, *, timeout_seconds: float = 120
) -> None:
    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(output),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            start_new_session=True,
        )
        try:
            await wait_for_process(
                process,
                timeout_seconds=timeout_seconds,
                timeout_error=asyncio.TimeoutError("Audio conversion timed out"),
            )
            if (
                process.returncode != 0
                or not output.is_file()
                or output.stat().st_size == 0
            ):
                stderr_file.seek(0, os.SEEK_END)
                stderr_file.seek(max(0, stderr_file.tell() - 300))
                details = stderr_file.read(300)
                raise AudioConversionError(
                    (details or b"ffmpeg conversion failed").decode(errors="replace")
                )
        finally:
            await terminate_process_group(process)
