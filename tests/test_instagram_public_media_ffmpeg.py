import shutil
import subprocess

import pytest

from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo
from src.instagram_video_bot.services.instagram_client import InstagramClient
from src.instagram_video_bot.services.media_normalizer import normalize_instagram_media


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg tools are required",
)
def test_real_ffmpeg_webm_track_merge_normalizes_without_losing_audio(tmp_path):
    video_path = tmp_path / "video-only.webm"
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
            "libvpx-vp9",
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
    assert output_path.suffix == ".mp4"
    assert InstagramClient._public_output_has_av_streams(output_path)

    normalized = normalize_instagram_media(
        VideoInfo(
            file_path=output_path,
            title="representative public fallback",
            duration=0.5,
            media_items=[
                MediaItem(
                    file_path=output_path,
                    media_type="video",
                    duration=0.5,
                    width=160,
                    height=90,
                )
            ],
        )
    )

    assert normalized.file_path != output_path
    assert InstagramClient._public_output_has_av_streams(normalized.file_path)
