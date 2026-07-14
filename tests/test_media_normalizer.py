import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo
from src.instagram_video_bot.services.media_normalizer import (
    VideoProbe,
    normalize_instagram_media,
)


def _video_info(path: Path) -> VideoInfo:
    return VideoInfo(
        file_path=path,
        title="test reel",
        duration=3.0,
        media_items=[
            MediaItem(
                file_path=path,
                media_type="video",
                duration=3.0,
                width=640,
                height=360,
            )
        ],
    )


def _probe(
    *,
    video_codec: str = "h264",
    pixel_format: str = "yuv420p",
    audio_codecs: tuple[str, ...] = ("aac",),
    duration: float = 3.0,
    width: int = 640,
    height: int = 360,
) -> VideoProbe:
    return VideoProbe(
        video_codec=video_codec,
        pixel_format=pixel_format,
        audio_codecs=audio_codecs,
        duration=duration,
        width=width,
        height=height,
    )


def test_compatible_video_is_remuxed_and_metadata_is_refreshed(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    commands: list[list[str]] = []

    def fake_probe(path: Path) -> VideoProbe:
        if path == source:
            return _probe()
        return _probe(duration=3.25, width=720, height=1280)

    def fake_run(command: list[str], output_path: Path) -> bool:
        commands.append(command)
        output_path.write_bytes(b"normalized")
        return True

    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._probe_video", fake_probe
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._decode_is_valid",
        lambda _path: True,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._run_ffmpeg", fake_run
    )

    result = normalize_instagram_media(_video_info(source))

    assert len(commands) == 1
    assert "copy" in commands[0]
    assert "libx264" not in commands[0]
    assert result.file_path.name == "source.ios.mp4"
    assert result.media_items[0].file_path == result.file_path
    assert result.media_items[0].duration == pytest.approx(3.25)
    assert result.media_items[0].width == 720
    assert result.media_items[0].height == 1280
    assert source.read_bytes() == b"source"


def test_incompatible_video_is_transcoded(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    commands: list[list[str]] = []

    def fake_probe(path: Path) -> VideoProbe:
        if path == source:
            return _probe(video_codec="vp9", pixel_format="yuv420p10le")
        return _probe()

    def fake_run(command: list[str], output_path: Path) -> bool:
        commands.append(command)
        output_path.write_bytes(b"normalized")
        return True

    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._probe_video", fake_probe
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._decode_is_valid",
        lambda _path: True,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._run_ffmpeg", fake_run
    )

    result = normalize_instagram_media(_video_info(source))

    assert len(commands) == 1
    assert "libx264" in commands[0]
    assert "yuv420p" in commands[0]
    assert result.file_path.name == "source.ios.mp4"


def test_normalization_failure_preserves_original_and_removes_candidate(
    monkeypatch, tmp_path
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    info = _video_info(source)

    def fake_run(_command: list[str], output_path: Path) -> bool:
        output_path.write_bytes(b"partial")
        return False

    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._probe_video",
        lambda _path: _probe(),
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._decode_is_valid",
        lambda _path: True,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._run_ffmpeg", fake_run
    )

    result = normalize_instagram_media(info)

    assert result is info
    assert result.file_path == source
    assert result.media_items[0].file_path == source
    assert not (tmp_path / "source.ios.mp4").exists()


def test_photo_only_result_bypasses_ffmpeg(monkeypatch, tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    info = VideoInfo(
        file_path=source,
        title="photo",
        media_items=[MediaItem(file_path=source, media_type="photo")],
        primary_media_type="photo",
    )

    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg must not run for photos"),
    )

    assert normalize_instagram_media(info) is info


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg tools are required",
)
@pytest.mark.parametrize(
    ("source_codec", "expected_outcome_codec"),
    [("libx264", "h264"), ("mpeg4", "h264")],
)
def test_real_ffmpeg_output_is_faststart_h264_yuv420p_and_decodable(
    tmp_path, source_codec, expected_outcome_codec
):
    source = tmp_path / f"source-{source_codec}.mp4"
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
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-c:v",
            source_codec,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    result = normalize_instagram_media(_video_info(source))
    output = result.file_path
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout)
    stream = payload["streams"][0]
    file_bytes = output.read_bytes()

    assert output != source
    assert stream["codec_name"] == expected_outcome_codec
    assert stream["pix_fmt"] == "yuv420p"
    assert file_bytes.find(b"moov") < file_bytes.find(b"mdat")
    assert (
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
