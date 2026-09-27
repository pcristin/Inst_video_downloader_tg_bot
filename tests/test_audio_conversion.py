import asyncio
import shutil
import subprocess
import sys
import time

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
    marker = tmp_path / "finished"
    script = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('done')"
    real_popen = subprocess.Popen

    def fake_ffmpeg(command, **kwargs):
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.audio_conversion.subprocess.Popen",
        fake_ffmpeg,
    )
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await convert_video_to_mp3(
            tmp_path / "source.mp4", tmp_path / "output.mp3", timeout_seconds=0.1
        )

    assert time.monotonic() - started < 1.5
    await asyncio.sleep(2.1)
    assert not marker.exists()
