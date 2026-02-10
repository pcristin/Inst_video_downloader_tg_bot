import pytest

from src.instagram_video_bot.services.instagram_fast_extractor import (
    DownloadedMedia,
    InstagramFastExtractor,
    InstagramFastExtractorError,
)


class _Response:
    def __init__(self, url: str, text: str = ""):
        self.url = url
        self.text = text


def test_extract_post_via_oembed_and_mobile_info(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor()
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")

    def fake_request_json(method, url, headers, data=None):
        if "oembed" in url:
            return {"media_id": "123_999"}
        if "/media/123/info/" in url:
            return {
                "items": [
                    {
                        "caption": {"text": "caption-a"},
                        "video_duration": 12.5,
                        "video_versions": [
                            {"url": "https://cdn.example.com/v1.mp4", "width": 720, "height": 1280},
                            {"url": "https://cdn.example.com/v2.mp4", "width": 1080, "height": 1920},
                        ],
                    }
                ]
            }
        return {}

    def fake_download(shortcode, media_items, output_dir):
        assert shortcode == "abc123"
        assert len(media_items) == 1
        assert media_items[0].url == "https://cdn.example.com/v2.mp4"
        return [DownloadedMedia(file_path=out_file, media_type="video", duration=12.5)]

    monkeypatch.setattr(extractor, "_request_json", fake_request_json)
    monkeypatch.setattr(extractor, "_download_media_items", fake_download)

    result = extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    assert result.shortcode == "abc123"
    assert result.caption == "caption-a"
    assert len(result.media_items) == 1
    assert result.media_items[0].file_path == out_file


def test_parse_photo_only_mobile_item():
    extractor = InstagramFastExtractor()
    caption, media_items = extractor._parse_mobile_item(
        {
            "caption": {"text": "photo caption"},
            "image_versions2": {"candidates": [{"url": "https://cdn.example.com/photo.jpg"}]},
        }
    )

    assert caption == "photo caption"
    assert len(media_items) == 1
    assert media_items[0].media_type == "photo"
    assert media_items[0].url.endswith("photo.jpg")


def test_parse_carousel_mobile_item_preserves_order():
    extractor = InstagramFastExtractor()
    caption, media_items = extractor._parse_mobile_item(
        {
            "caption": {"text": "carousel caption"},
            "carousel_media": [
                {
                    "image_versions2": {"candidates": [{"url": "https://cdn.example.com/1.jpg"}]},
                },
                {
                    "video_duration": 4,
                    "video_versions": [
                        {"url": "https://cdn.example.com/2-low.mp4", "width": 480, "height": 640},
                        {"url": "https://cdn.example.com/2-high.mp4", "width": 1080, "height": 1920},
                    ],
                },
            ],
        }
    )

    assert caption == "carousel caption"
    assert [item.media_type for item in media_items] == ["photo", "video"]
    assert media_items[0].url.endswith("1.jpg")
    assert media_items[1].url.endswith("2-high.mp4")


def test_share_url_resolution_to_canonical_post(monkeypatch):
    extractor = InstagramFastExtractor()

    monkeypatch.setattr(
        extractor,
        "_request_raw",
        lambda **kwargs: _Response("https://www.instagram.com/p/XYZ123/"),
    )

    resolved = extractor.resolve_share_url("https://www.instagram.com/share/reel/abc/")
    assert resolved == "https://www.instagram.com/p/XYZ123/"


def test_extract_raises_when_all_fast_sources_fail(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor()

    monkeypatch.setattr(extractor, "_request_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(extractor, "_request_embed_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda *args, **kwargs: {})

    with pytest.raises(InstagramFastExtractorError, match="Failed to extract media"):
        extractor.extract_and_download("https://www.instagram.com/p/abc123/", tmp_path)
