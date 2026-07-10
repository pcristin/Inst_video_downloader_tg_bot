import sys
import types

import pytest

from src.instagram_video_bot.services.instagram_client import (
    InstagramAuthError,
    InstagramClient,
    InstagramDownloadResult,
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

    result = client.download_media("https://www.instagram.com/p/photo/", tmp_path)

    assert result.file_paths == [photo_path]
    assert result.fallback_path == "photo"


def test_download_media_preserves_carousel_items_from_raw_payload(tmp_path):
    client = InstagramClient(username="u", password="p")
    client.client = _CarouselPostClient(tmp_path)

    result = client.download_media("https://www.instagram.com/p/album/", tmp_path)

    assert result.fallback_path == "album"
    assert [path.suffix for path in result.file_paths] == [".jpg", ".mp4"]


def test_download_media_returns_public_ytdlp_result_before_account_lookup(tmp_path):
    expected = InstagramDownloadResult(
        file_paths=[tmp_path / "public_1.mp4"],
        fallback_path="yt_dlp_public",
        metadata_reused=True,
    )
    client = InstagramClient(username="u", password="p")
    client._download_public_ytdlp_media = lambda _url, _output_dir: expected
    client._download_post_media = lambda *_args: pytest.fail(
        "account path should not run"
    )

    assert client.download_media("https://www.instagram.com/reel/example/", tmp_path) is expected


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

    class _Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    requested_urls = []

    def _get(url, timeout):
        requested_urls.append((url, timeout))
        return _Response(url.encode())

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YoutubeDL))
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get", _get
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
        ("https://cdn.example.com/video-high.mp4", 15.0),
        ("https://cdn.example.com/thumb-large.jpg", 15.0),
    ]
    assert captured["options"] == {
        "quiet": True,
        "skip_download": True,
        "ignoreerrors": True,
        "ignore_no_formats_error": True,
        "noplaylist": False,
    }
