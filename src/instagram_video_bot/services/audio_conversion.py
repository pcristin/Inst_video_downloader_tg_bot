"""Bounded conversion of a downloaded video into an MP3 audio file."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
from pathlib import Path


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
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(0.1, timeout_seconds)
            while process.poll() is None:
                if loop.time() >= deadline:
                    raise asyncio.TimeoutError("Audio conversion timed out")
                await asyncio.sleep(min(0.05, deadline - loop.time()))
            if (
                process.returncode != 0
                or not output.is_file()
                or output.stat().st_size == 0
            ):
                stderr_file.seek(0)
                details = stderr_file.read()[-300:]
                raise AudioConversionError(
                    (details or b"ffmpeg conversion failed").decode(errors="replace")
                )
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
