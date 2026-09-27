import asyncio
import shutil
import signal
import subprocess
import sys

import pytest

from src.instagram_video_bot.services.audio_conversion import (
    AudioConversionError,
    convert_video_to_mp3,
)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
@pytest.mark.asyncio
async def test_converts_video_with_audio_to_mp3(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )

    await convert_video_to_mp3(source, output)

    assert output.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
@pytest.mark.asyncio
async def test_video_without_audio_reports_conversion_failure(tmp_path):
    source = tmp_path / "silent.mp4"
    output = tmp_path / "output.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:d=1",
            "-c:v",
            "mpeg4",
            str(source),
        ],
        check=True,
    )

    with pytest.raises(AudioConversionError):
        await convert_video_to_mp3(source, output)


@pytest.mark.asyncio
async def test_audio_conversion_kills_timed_out_ffmpeg(monkeypatch, tmp_path):
    script = "import time; time.sleep(60)"
    real_popen = subprocess.Popen
    processes = []

    def fake_ffmpeg(command, **kwargs):
        process = real_popen([sys.executable, "-c", script], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        "src.instagram_video_bot.services.audio_conversion.subprocess.Popen",
        fake_ffmpeg,
    )
    with pytest.raises(asyncio.TimeoutError):
        await convert_video_to_mp3(
            tmp_path / "source.mp4", tmp_path / "output.mp3", timeout_seconds=0.1
        )

    assert len(processes) == 1
    assert processes[0].poll() == -signal.SIGKILL
