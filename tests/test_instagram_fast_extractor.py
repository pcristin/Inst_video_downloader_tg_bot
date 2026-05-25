import threading
import time

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

    def fake_request_json(method, url, headers, data=None, timeout=None):
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


def test_download_media_items_runs_carousel_downloads_concurrently(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor()
    active = 0
    max_active = 0
    lock = threading.Lock()

    class _StreamResponse:
        headers = {"content-type": "video/mp4"}

        def iter_content(self, chunk_size):
            yield b"video"

    def fake_request_raw(**kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _StreamResponse()

    monkeypatch.setattr(extractor, "_request_raw", fake_request_raw)
    media_items = [
        type("Item", (), {"url": f"https://cdn.example.com/{index}.mp4", "media_type": "video", "duration": None, "width": None, "height": None})()
        for index in range(4)
    ]

    downloaded = extractor._download_media_items("abc123", media_items, tmp_path)

    assert len(downloaded) == 4
    assert [item.file_path.name for item in downloaded] == [
        "instagram_abc123_1.mp4",
        "instagram_abc123_2.mp4",
        "instagram_abc123_3.mp4",
        "instagram_abc123_4.mp4",
    ]
    assert max_active > 1


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


def test_fast_extractor_clamps_zero_budget_to_positive_minimum():
    extractor = InstagramFastExtractor(total_budget_seconds=0.0)

    assert extractor.total_budget_seconds == 0.01


def test_fast_extractor_budget_stops_later_endpoint_attempts(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor(total_budget_seconds=0.01)
    calls = []

    def fake_get_media_id(_canonical_url):
        calls.append("media_id")
        time.sleep(0.02)
        return None

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("later endpoint should not run after budget is exhausted")

    monkeypatch.setattr(extractor, "_get_media_id", fake_get_media_id)
    monkeypatch.setattr(extractor, "_request_embed_data", fail_if_called)
    monkeypatch.setattr(extractor, "_request_graphql_data", fail_if_called)

    with pytest.raises(InstagramFastExtractorError, match="fast_path_budget_exhausted"):
        extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    assert calls == ["media_id"]
    assert extractor.last_budget_exhausted is True
    assert extractor.last_endpoint_timings[0]["name"] == "media_id"


def test_fast_extractor_records_endpoint_timings(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor(total_budget_seconds=5.0)

    monkeypatch.setattr(extractor, "_get_media_id", lambda _canonical_url: None)
    monkeypatch.setattr(extractor, "_request_embed_data", lambda _shortcode: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda _shortcode: {})

    with pytest.raises(InstagramFastExtractorError):
        extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    names = [item["name"] for item in extractor.last_endpoint_timings]
    assert names == ["media_id", "embed", "graphql"]
    assert all("duration_ms" in item for item in extractor.last_endpoint_timings)
    assert all(item["status"] in {"miss", "failed"} for item in extractor.last_endpoint_timings)


def test_fast_extractor_attempt_metrics_are_isolated_across_threads(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor(total_budget_seconds=5.0)
    barrier = threading.Barrier(2)
    errors = {}

    def fake_get_media_id(_canonical_url):
        barrier.wait(timeout=1)
        return None

    def fake_embed(shortcode):
        time.sleep(0.02 if shortcode == "one" else 0.01)
        return {}

    monkeypatch.setattr(extractor, "_get_media_id", fake_get_media_id)
    monkeypatch.setattr(extractor, "_request_embed_data", fake_embed)
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda _shortcode: {})

    def run(shortcode):
        with pytest.raises(InstagramFastExtractorError) as exc_info:
            extractor.extract_and_download(
                f"https://www.instagram.com/reel/{shortcode}/", tmp_path
            )
        errors[shortcode] = exc_info.value.endpoint_timings

    threads = [
        threading.Thread(target=run, args=("one",)),
        threading.Thread(target=run, args=("two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert set(errors) == {"one", "two"}
    for timings in errors.values():
        assert [item["name"] for item in timings] == ["media_id", "embed", "graphql"]
        assert len(timings) == 3
