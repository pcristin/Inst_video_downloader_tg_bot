from types import SimpleNamespace

from src.instagram_video_bot.services.media_metadata import probe_video_metadata


def test_probe_video_metadata_applies_sample_aspect_ratio_before_rotation(monkeypatch, tmp_path):
    video_file = tmp_path / "rotated.mp4"
    video_file.write_bytes(b"video")

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "{"
                '"streams": ['
                "{"
                '"width": 480,'
                '"height": 640,'
                '"duration": "3.5",'
                '"sample_aspect_ratio": "2:1",'
                '"side_data_list": [{"rotation": 90}]'
                "}"
                "]"
                "}"
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_metadata.subprocess.run",
        fake_run,
    )

    metadata = probe_video_metadata(video_file)

    assert metadata.duration == 3.5
    assert metadata.width == 640
    assert metadata.height == 960
