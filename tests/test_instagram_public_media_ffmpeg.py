import shutil
import subprocess

import pytest

from src.instagram_video_bot.services.instagram_client import InstagramClient


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg tools are required",
)
def test_real_ffmpeg_public_track_merge_contains_video_and_audio(tmp_path):
    video_path = tmp_path / "video-only.mp4"
    audio_path = tmp_path / "audio-only.m4a"
    output_path = tmp_path / "merged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=12:duration=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-vn",
            "-c:a",
            "aac",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    assert InstagramClient._merge_public_tracks(
        video_path,
        audio_path,
        output_path,
    )
    assert InstagramClient._public_output_has_av_streams(output_path)
