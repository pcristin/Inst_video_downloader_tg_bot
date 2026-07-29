import builtins
import logging
import sys
import types
from pathlib import Path

import pytest

from src.instagram_video_bot.services.instagram_client import (
    InstagramAuthError,
    InstagramClient,
    InstagramDownloadResult,
    PublicYtdlpSource,
)


class _FailingMediaAPIClient:
    def media_pk_from_url(self, _url: str) -> int:
        return 123456

    def media_info(self, _media_pk: int):
        raise Exception("media info failed")

    def media_info_v1(self, _media_pk: int):
        raise Exception("media info v1 failed")


class _CheckpointDownloadClient:
    user_agent = "test-agent"

    def media_pk_from_url(self, _url: str) -> int:
        return 123456

    def video_download(self, _media_pk: int, folder=None):
        raise Exception(
            "Manual verification required via Instagram UFAC web bloks checkpoint. "
            "Please resolve it in the Instagram app or web flow and then retry."
        )


class _StreamingResponse:
    def __init__(self, chunks, *, content_length=None):
        self._chunks = list(chunks)
        self.content = b"".join(self._chunks)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        yield from self._chunks

    def close(self):
        self.closed = True


class _MissingVideoDownloadClient:
    user_agent = "test-agent"

    def media_pk_from_url(self, _url: str) -> int:
        return 123456

    def video_download(self, _media_pk: int, folder=None):
        return None


class _PhotoPostClient:
    def __init__(self, photo_path):
        self.photo_path = photo_path

    def media_pk_from_url(self, _url: str) -> int:
        return 123456

    def private_request(self, _endpoint: str):
        return {"items": [{"media_type": 1}]}

    def photo_download(self, _media_pk: int, folder=None):
        return self.photo_path


class _CarouselPostClient:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def media_pk_from_url(self, _url: str) -> int:
        return 123456

    def private_request(self, _endpoint: str):
        return {
            "items": [
                {
                    "media_type": 8,
                    "carousel_media": [
                        {
                            "image_versions2": {
                                "candidates": [
                                    {"url": "https://cdn.example.com/one.jpg", "width": 720, "height": 720}
                                ]
                            }
                        },
                        {
                            "video_versions": [
                                {"url": "https://cdn.example.com/two.mp4", "width": 1080, "height": 1920}
                            ]
                        },
                    ],
                }
            ]
        }

    def album_download(self, _media_pk: int, folder=None):
        raise Exception("album validation failed")

    def photo_download_by_url(self, _url: str, filename: str, folder=None):
        path = self.output_dir / f"{filename}.jpg"
        path.write_bytes(b"photo")
        return path

    def video_download_by_url(self, _url: str, filename: str, folder=None):
        path = self.output_dir / f"{filename}.mp4"
        path.write_bytes(b"video")
        return path


def test_get_media_info_returns_minimal_fallback_when_all_lookups_fail():
    client = InstagramClient(username="u", password="p")
    client.client = _FailingMediaAPIClient()
    client._get_oembed_safe = lambda _url: None

    info = client.get_media_info("https://www.instagram.com/reel/test/")

    assert info is not None
    assert info["pk"] == 123456
    assert info["title"] == ""
    assert info["duration"] == 0
    assert info["user"] == "unknown"


def test_checkpoint_manual_verification_is_auth_challenge():
    error = Exception("Manual verification required via Instagram UFAC web bloks checkpoint")

    assert InstagramClient._classify_instagram_error(error) == "auth_challenge"


def test_content_restriction_with_403_is_not_auth_challenge():
    error = Exception("403 This content isn't available to everyone")

    assert InstagramClient._classify_instagram_error(error) == "content_restricted"


def test_download_video_propagates_checkpoint_after_relogin_attempt(monkeypatch, tmp_path):
    client = InstagramClient(username="u", password="p")
    client.client = _CheckpointDownloadClient()
    client._download_with_ytdlp_first = lambda *_args: None
    client._relogin = lambda: True

    with pytest.raises(InstagramAuthError, match="Manual verification required"):
        client.download_video("https://www.instagram.com/reel/test/", tmp_path)


def test_initial_ytdlp_403_does_not_stick_when_authenticated_download_returns_no_file(tmp_path):
    client = InstagramClient(username="u", password="p")
    client.client = _MissingVideoDownloadClient()

    def _failed_ytdlp(*_args):
        client._record_failure("ERROR: 403 Forbidden")
        return None

    client._download_with_ytdlp_first = _failed_ytdlp

    assert client.download_video("https://www.instagram.com/reel/test/", tmp_path) is None
    assert client.last_failure_class != "auth_challenge"


def test_download_media_uses_photo_download_for_photo_posts(tmp_path):
    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"photo")
    client = InstagramClient(username="u", password="p")
    client.client = _PhotoPostClient(photo_path)
    client._download_public_ytdlp_media = lambda *_args: None

    result = client.download_media("https://www.instagram.com/p/photo/", tmp_path)

    assert result.file_paths == [photo_path]
    assert result.fallback_path == "photo"


def test_download_media_preserves_carousel_items_from_raw_payload(tmp_path):
    client = InstagramClient(username="u", password="p")
    client.client = _CarouselPostClient(tmp_path)
    client._download_public_ytdlp_media = lambda *_args: None

    result = client.download_media("https://www.instagram.com/p/album/", tmp_path)

    assert result.fallback_path == "album"
    assert [path.suffix for path in result.file_paths] == [".jpg", ".mp4"]


def test_download_media_uses_account_path_after_public_recovery_moves_to_adapter(tmp_path):
    expected = InstagramDownloadResult(
        file_paths=[tmp_path / "account_1.mp4"],
        fallback_path="instagrapi_native",
        metadata_reused=True,
    )
    client = InstagramClient(username="u", password="p")
    client._download_public_ytdlp_media = lambda *_args: pytest.fail(
        "public recovery belongs to the adapter before account construction"
    )
    client._download_post_media = lambda *_args: expected

    assert client.download_media("https://www.instagram.com/reel/example/", tmp_path) is expected


def test_download_media_uses_account_path_when_public_ytdlp_is_unavailable(
    monkeypatch, tmp_path
):
    client = InstagramClient(username="u", password="p")
    expected = InstagramDownloadResult(
        file_paths=[tmp_path / "account.mp4"], fallback_path="instagrapi_native"
    )
    client._download_post_media = lambda *_args: expected
    original_import = builtins.__import__

    def _missing_ytdlp(name, *args, **kwargs):
        if name == "yt_dlp":
            raise ModuleNotFoundError("No module named 'yt_dlp'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _missing_ytdlp)

    assert client.download_media("https://www.instagram.com/p/example/", tmp_path) is expected


def test_public_ytdlp_source_pairs_highest_quality_video_with_best_audio():
    entry = {
        "formats": [
            {
                "url": "https://cdn.example/video-720.mp4",
                "ext": "mp4",
                "vcodec": "vp9",
                "acodec": "none",
                "width": 720,
                "height": 1280,
                "tbr": 900,
            },
            {
                "url": "https://cdn.example/video-1080.mp4",
                "ext": "mp4",
                "vcodec": "vp9",
                "acodec": "none",
                "width": 1080,
                "height": 1920,
                "tbr": 1500,
            },
            {
                "url": "https://cdn.example/audio-low.m4a",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "abr": 48,
            },
            {
                "url": "https://cdn.example/audio-high.m4a",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "abr": 96,
            },
        ]
    }

    assert InstagramClient._public_ytdlp_source(entry) == PublicYtdlpSource(
        visual_url="https://cdn.example/video-1080.mp4",
        extension="mp4",
        is_video=True,
        audio_url="https://cdn.example/audio-high.m4a",
        audio_extension="m4a",
    )


def test_public_video_with_missing_acodec_is_validated(monkeypatch, tmp_path):
    source = InstagramClient._public_ytdlp_source(
        {
            "formats": [
                {
                    "url": "https://cdn.example/video.mp4",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "height": 1080,
                }
            ]
        }
    )
    output = tmp_path / "public_1.mp4"
    probed_paths = []

    assert source is not None
    assert source.is_video is True
    assert source.audio_url is None

    monkeypatch.setattr(
        InstagramClient,
        "_download_url_to_path",
        staticmethod(lambda _url, path: path.write_bytes(b"combined-av")),
    )
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda path: probed_paths.append(path) or True),
    )

    assert InstagramClient._download_public_source(source, output) is True
    assert probed_paths == [output]
    assert output.read_bytes() == b"combined-av"


def test_public_video_claiming_combined_av_is_rejected_when_probe_has_no_audio(
    monkeypatch, tmp_path
):
    source = InstagramClient._public_ytdlp_source(
        {
            "formats": [
                {
                    "url": "https://cdn.example/video.mp4",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "height": 1080,
                }
            ]
        }
    )
    output = tmp_path / "public_1.mp4"

    assert source is not None
    assert source.is_video is True

    monkeypatch.setattr(
        InstagramClient,
        "_download_url_to_path",
        staticmethod(lambda _url, path: path.write_bytes(b"actually-video-only")),
    )
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: False),
    )

    assert InstagramClient._download_public_source(source, output) is False
    assert not output.exists()


def test_public_video_with_combined_av_is_accepted_after_probe(monkeypatch, tmp_path):
    source = InstagramClient._public_ytdlp_source(
        {
            "formats": [
                {
                    "url": "https://cdn.example/video.mp4",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "height": 1080,
                }
            ]
        }
    )
    output = tmp_path / "public_1.mp4"

    assert source is not None
    assert source.is_video is True

    monkeypatch.setattr(
        InstagramClient,
        "_download_url_to_path",
        staticmethod(lambda _url, path: path.write_bytes(b"combined-av")),
    )
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: True),
    )

    assert InstagramClient._download_public_source(source, output) is True
    assert output.read_bytes() == b"combined-av"


def test_public_thumbnail_bypasses_av_probe(monkeypatch, tmp_path):
    source = InstagramClient._public_ytdlp_source(
        {
            "thumbnails": [
                {
                    "url": "https://cdn.example/photo.jpg",
                    "ext": "jpg",
                    "width": 1080,
                    "height": 1080,
                }
            ]
        }
    )
    output = tmp_path / "public_1.jpg"

    assert source is not None
    assert source.is_video is False

    monkeypatch.setattr(
        InstagramClient,
        "_download_url_to_path",
        staticmethod(lambda _url, path: path.write_bytes(b"photo")),
    )
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: pytest.fail("photos must not be probed for A/V")),
    )

    assert InstagramClient._download_public_source(source, output) is True
    assert output.read_bytes() == b"photo"


def test_public_download_rejects_oversized_content_length_before_writing(
    monkeypatch, tmp_path
):
    response = _StreamingResponse([b"123456"], content_length=6)
    output = tmp_path / "oversized.mp4"
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.settings.TELEGRAM_MAX_UPLOAD_BYTES",
        5,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(RuntimeError, match="exceeds the 5-byte download limit"):
        InstagramClient._download_url_to_path("https://cdn.example/video.mp4", output)

    assert not output.exists()
    assert response.closed is True


def test_public_download_stops_chunked_response_at_limit_and_removes_partial(
    monkeypatch, tmp_path
):
    response = _StreamingResponse([b"123", b"456"])
    output = tmp_path / "chunked.mp4"
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.settings.TELEGRAM_MAX_UPLOAD_BYTES",
        5,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(RuntimeError, match="exceeds the 5-byte download limit"):
        InstagramClient._download_url_to_path("https://cdn.example/video.mp4", output)

    assert not output.exists()
    assert response.closed is True


def test_public_download_streams_response_below_limit(monkeypatch, tmp_path):
    response = _StreamingResponse([b"12", b"34"], content_length=4)
    output = tmp_path / "public.mp4"
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.settings.TELEGRAM_MAX_UPLOAD_BYTES",
        5,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get",
        lambda *_args, **_kwargs: response,
    )

    InstagramClient._download_url_to_path("https://cdn.example/video.mp4", output)

    assert output.read_bytes() == b"1234"
    assert response.closed is True


def test_public_ytdlp_media_downloads_video_and_thumbnail_entries(monkeypatch, tmp_path):
    captured = {}

    class _YoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "Public carousel",
                "entries": [
                    {
                        "formats": [
                            {
                                "url": "https://cdn.example.com/video-low.mp4",
                                "ext": "mp4",
                                "height": 480,
                            },
                            {
                                "url": "https://cdn.example.com/video-high.mp4",
                                "ext": "mp4",
                                "height": 1080,
                            },
                        ]
                    },
                    {
                        "thumbnails": [
                            {
                                "url": "https://cdn.example.com/thumb-small.jpg",
                                "width": 320,
                                "height": 320,
                            },
                            {
                                "url": "https://cdn.example.com/thumb-large.jpg",
                                "width": 1080,
                                "height": 1080,
                            },
                        ]
                    },
                ],
            }

    requested_urls = []

    def _get(url, timeout, stream=False):
        requested_urls.append((url, timeout, stream))
        return _StreamingResponse([url.encode()])

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YoutubeDL))
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get", _get
    )
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: True),
    )
    client = InstagramClient(username="u", password="p", proxy="http://private-proxy")

    result = client._download_public_ytdlp_media(
        "https://www.instagram.com/p/example/", tmp_path
    )

    assert result is not None
    assert result.fallback_path == "yt_dlp_public"
    assert result.metadata == {"title": "Public carousel"}
    assert result.metadata_reused is True
    assert [path.name for path in result.file_paths] == ["public_1.mp4", "public_2.jpg"]
    assert [path.read_bytes() for path in result.file_paths] == [
        b"https://cdn.example.com/video-high.mp4",
        b"https://cdn.example.com/thumb-large.jpg",
    ]
    assert requested_urls == [
        ("https://cdn.example.com/video-high.mp4", 15.0, True),
        ("https://cdn.example.com/thumb-large.jpg", 15.0, True),
    ]
    assert captured["options"] == {
        "quiet": True,
        "skip_download": True,
        "ignoreerrors": True,
        "ignore_no_formats_error": True,
        "noplaylist": False,
    }


def test_public_ytdlp_media_merges_separate_audio_before_returning(monkeypatch, tmp_path):
    class _YoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "Reel with separate tracks",
                "formats": [
                    {
                        "url": "https://cdn.example/video.webm",
                        "ext": "webm",
                        "vcodec": "vp9",
                        "acodec": "none",
                        "width": 1080,
                        "height": 1920,
                    },
                    {
                        "url": "https://cdn.example/audio.m4a",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "aac",
                        "abr": 96,
                    },
                ],
            }

    requested_urls = []
    ffmpeg_commands = []

    def _get(url, timeout, stream=False):
        requested_urls.append((url, timeout, stream))
        return _StreamingResponse([url.encode()])

    def _run(command, **_kwargs):
        ffmpeg_commands.append(command)
        Path(command[-1]).write_bytes(b"muxed-av")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YoutubeDL))
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get", _get
    )
    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: True),
        raising=False,
    )

    result = InstagramClient.download_public_ytdlp_media(
        "https://www.instagram.com/reel/example/", tmp_path
    )

    assert result is not None
    assert result.file_paths == [tmp_path / "public_1.mp4"]
    assert result.file_paths[0].read_bytes() == b"muxed-av"
    assert requested_urls == [
        ("https://cdn.example/video.webm", 15.0, True),
        ("https://cdn.example/audio.m4a", 15.0, True),
    ]
    assert ffmpeg_commands[0][0:2] == ["ffmpeg", "-v"]
    assert Path(ffmpeg_commands[0][5]).suffix == ".webm"
    assert Path(ffmpeg_commands[0][-1]).suffix == ".mp4"
    assert ffmpeg_commands[0][-9:-1] == [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
    ]
    assert list(tmp_path.glob(".*.video.*")) == []
    assert list(tmp_path.glob(".*.audio.*")) == []


def test_public_source_rejects_merged_file_without_audio(monkeypatch, tmp_path):
    source = PublicYtdlpSource(
        visual_url="https://cdn.example/video.mp4",
        extension="mp4",
        is_video=True,
        audio_url="https://cdn.example/audio.m4a",
        audio_extension="m4a",
    )
    output = tmp_path / "public_1.mp4"

    def _download(_url, path):
        path.write_bytes(b"track")

    def _merge(_video, _audio, path):
        path.write_bytes(b"video-only-output")
        return True

    monkeypatch.setattr(InstagramClient, "_download_url_to_path", staticmethod(_download))
    monkeypatch.setattr(InstagramClient, "_merge_public_tracks", staticmethod(_merge))
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: False),
    )

    assert InstagramClient._download_public_source(source, output) is False
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_public_ytdlp_media_rejects_video_only_source_without_audio(monkeypatch, tmp_path):
    class _YoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "Silent reel",
                "formats": [
                    {
                        "url": "https://cdn.example/video.mp4",
                        "ext": "mp4",
                        "vcodec": "vp9",
                        "acodec": "none",
                        "width": 1080,
                        "height": 1920,
                    }
                ],
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YoutubeDL))
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get",
        lambda *_args, **_kwargs: pytest.fail("silent source must not be downloaded"),
    )

    result = InstagramClient.download_public_ytdlp_media(
        "https://www.instagram.com/reel/silent/", tmp_path
    )

    assert result is None
    assert list(tmp_path.iterdir()) == []


def test_public_ytdlp_extraction_error_logs_only_fixed_structured_fields(
    monkeypatch, tmp_path, caplog
):
    media_url = "https://cdn.example/private/media.mp4"

    class _YoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            raise RuntimeError(f"extractor failed for {media_url}")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YoutubeDL))
    caplog.set_level(
        logging.INFO,
        logger="src.instagram_video_bot.services.instagram_client",
    )

    result = InstagramClient.download_public_ytdlp_media(
        "https://www.instagram.com/reel/example/", tmp_path
    )

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "Public yt-dlp extraction failed"
    ]
    assert result is None
    assert len(records) == 1
    assert records[0].error_class == "RuntimeError"
    assert media_url not in caplog.text
