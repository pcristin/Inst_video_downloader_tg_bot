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


def test_normalization_rejects_output_that_loses_source_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-with-audio")
    info = _video_info(source)

    def fake_probe(path: Path) -> VideoProbe:
        if path == source:
            return _probe(video_codec="vp9", audio_codecs=("aac",))
        return _probe(video_codec="h264", audio_codecs=())

    def fake_run(_command: list[str], output_path: Path) -> bool:
        output_path.write_bytes(b"normalized-without-audio")
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

    result = normalize_instagram_media(info)

    assert result is info
    assert result.file_path == source
    assert not (tmp_path / "source.ios.mp4").exists()


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


def test_compatible_normalization_decodes_output_only(monkeypatch, tmp_path):
    from src.instagram_video_bot.services import media_normalizer as normalizer

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    decoded = []
    monkeypatch.setattr(normalizer, "_probe_video", lambda path: _probe())
    monkeypatch.setattr(
        normalizer, "_decode_is_valid", lambda path: decoded.append(path) or True
    )

    def run(command, output):
        output.write_bytes(b"valid")
        return True

    monkeypatch.setattr(normalizer, "_run_ffmpeg", run)
    result = normalize_instagram_media(_video_info(source))
    assert result.file_path != source
    assert len(decoded) == 1
    assert source not in decoded


def test_corrupt_remux_is_repaired_and_verified(monkeypatch, tmp_path):
    from src.instagram_video_bot.services import media_normalizer as normalizer

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    commands = []
    monkeypatch.setattr(normalizer, "_probe_video", lambda path: _probe())
    monkeypatch.setattr(
        normalizer, "_decode_is_valid", lambda path: path.read_bytes() == b"repaired"
    )

    def run(command, output):
        commands.append(command)
        output.write_bytes(b"repaired" if "libx264" in command else b"corrupt")
        return True

    monkeypatch.setattr(normalizer, "_run_ffmpeg", run)
    result = normalize_instagram_media(_video_info(source))
    assert result.file_path != source
    assert len(commands) == 2
    assert result.file_path.read_bytes() == b"repaired"


def test_failed_normalization_preserves_existing_atomic_output(monkeypatch, tmp_path):
    from src.instagram_video_bot.services import media_normalizer as normalizer

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    existing = tmp_path / "clip.ios.mp4"
    existing.write_bytes(b"previous-valid")
    monkeypatch.setattr(normalizer, "_probe_video", lambda path: _probe())
    monkeypatch.setattr(normalizer, "_decode_is_valid", lambda path: True)
    monkeypatch.setattr(normalizer, "_run_ffmpeg", lambda command, output: False)
    normalize_instagram_media(_video_info(source))
    assert existing.read_bytes() == b"previous-valid"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.ios.mp4", "clip.mp4"]


def test_normalization_subprocesses_share_one_deadline(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import media_normalizer as normalizer

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    clock = [0.0]
    timeouts = []
    commands = []
    monkeypatch.setattr(normalizer, "perf_counter", lambda: clock[0])

    def run(command, **kwargs):
        commands.append(command)
        timeouts.append(kwargs["timeout"])
        clock[0] += 20
        if command[0] == "ffprobe":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "h264",
                                "pix_fmt": "yuv420p",
                            },
                            {"codec_type": "audio", "codec_name": "aac"},
                        ]
                    }
                ),
            )
        if command[-1] != "-":
            Path(command[-1]).write_bytes(b"normalized")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(normalizer.subprocess, "run", run)
    result = normalize_instagram_media(_video_info(source))
    assert result.file_path != source
    assert timeouts == [300, 280, 260, 240]
    decode = commands[-1]
    assert "-xerror" in decode
    assert "0:a?" in decode
    assert "-threads" in decode


def test_deadline_exhaustion_stops_launching_subprocesses(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import media_normalizer as normalizer

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    clock = [0.0]
    commands = []
    monkeypatch.setattr(normalizer, "perf_counter", lambda: clock[0])

    def run(command, **kwargs):
        commands.append(command)
        clock[0] = 301
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "pix_fmt": "yuv420p",
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(normalizer.subprocess, "run", run)
    info = _video_info(source)
    assert normalize_instagram_media(info) is info
    assert len(commands) == 1
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("preset", ["fast", "veryfast", "medium"])
def test_transcode_command_uses_configured_preset_without_changing_quality(
    monkeypatch, preset
):
    from types import SimpleNamespace
    from src.instagram_video_bot.services import media_normalizer as normalizer

    monkeypatch.setattr(
        normalizer,
        "settings",
        SimpleNamespace(INSTAGRAM_NORMALIZATION_PRESET=preset),
        raising=False,
    )
    command = normalizer._transcode_command(Path("source.mp4"))

    assert command[command.index("-preset") + 1] == preset
    assert command[command.index("-crf") + 1] == "20"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
    assert "-vf" not in command


@pytest.mark.parametrize(
    "preset",
    [
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
        "placebo",
    ],
)
def test_normalization_preset_setting_accepts_supported_values(tmp_path, preset):
    from src.instagram_video_bot.config.settings import Settings

    settings = Settings(
        _env_file=None,
        BASE_DIR=tmp_path,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
        ACCOUNT_STATE_FILE=tmp_path / "accounts.json",
        INSTAGRAM_NORMALIZATION_PRESET=preset,
    )
    assert settings.INSTAGRAM_NORMALIZATION_PRESET == preset


def test_normalization_preset_setting_rejects_unknown_value(tmp_path):
    from pydantic import ValidationError
    from src.instagram_video_bot.config.settings import Settings

    with pytest.raises(ValidationError, match="INSTAGRAM_NORMALIZATION_PRESET"):
        Settings(
            _env_file=None,
            BASE_DIR=tmp_path,
            TEMP_DIR=tmp_path / "temp",
            CACHE_DIR=tmp_path / "cache",
            STATE_DB_PATH=tmp_path / "state.db",
            ACCOUNT_STATE_FILE=tmp_path / "accounts.json",
            INSTAGRAM_NORMALIZATION_PRESET="invalid",
        )


def test_normalization_preset_defaults_to_veryfast():
    from src.instagram_video_bot.config.settings import Settings

    assert Settings.model_fields["INSTAGRAM_NORMALIZATION_PRESET"].default == "veryfast"
