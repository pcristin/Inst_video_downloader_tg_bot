import threading
import time

import pytest

from src.instagram_video_bot.services.instagram_fast_extractor import (
    DownloadedMedia,
    ExtractedMedia,
    InstagramFastExtractor,
    InstagramFastExtractorError,
)
from src.instagram_video_bot.services.instagram_auth_pool import (
    InstagramAuthContext,
    InstagramAuthPool,
)


class _Response:
    def __init__(self, url: str, text: str = ""):
        self.url = url
        self.text = text
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        return None


class _StreamResponse:
    def __init__(self, *, content_type: str = "video/mp4"):
        self.url = "https://scontent.cdninstagram.com/video.mp4"
        self.text = ""
        self.status_code = 200
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"video"


class _StatusResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.url = "https://i.instagram.com/api/v1/media/123/info/"
        self.text = text
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        raise RuntimeError(f"HTTP {self.status_code}")


class _StreamingStatusResponse:
    def __init__(self, status_code: int):
        self.url = "https://scontent.cdninstagram.com/video.mp4"
        self.status_code = status_code
        self.headers = {"content-type": "video/mp4"}

    @property
    def text(self):
        raise AssertionError("streamed media response body should not be buffered")

    def raise_for_status(self):
        raise RuntimeError(f"HTTP {self.status_code}")


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


def test_public_fast_success_does_not_use_auth_context(monkeypatch, tmp_path):
    context = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    extractor = InstagramFastExtractor(auth_pool=InstagramAuthPool([context]))
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")
    seen_headers = []

    def fake_request_json(method, url, headers, data=None, timeout=None):
        seen_headers.append(dict(headers))
        if "oembed" in url:
            return {"media_id": "123_999"}
        if "/media/123/info/" in url:
            return {
                "items": [
                    {
                        "caption": {"text": "public-caption"},
                        "video_versions": [
                            {"url": "https://cdn.example.com/v1.mp4", "width": 720, "height": 1280}
                        ],
                    }
                ]
            }
        return {}

    monkeypatch.setattr(extractor, "_request_json", fake_request_json)
    monkeypatch.setattr(
        extractor,
        "_download_media_items",
        lambda shortcode, media_items, output_dir, auth_context=None: [
            DownloadedMedia(file_path=out_file, media_type="video")
        ],
    )

    result = extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    assert result.success_path == "fast"
    assert all("Cookie" not in headers and "Authorization" not in headers for headers in seen_headers)


def test_default_constructor_loads_configured_auth_pool(monkeypatch):
    context = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_fast_extractor.load_configured_instagram_auth_pool",
        lambda: InstagramAuthPool([context]),
    )

    extractor = InstagramFastExtractor()

    assert extractor.auth_pool.get_contexts_for_attempt() == [context]


def test_default_constructor_reuses_configured_auth_pool_across_instances(
    monkeypatch, tmp_path
):
    auth_file = tmp_path / "instagram_auth.json"
    auth_file.write_text(
        """
        {
            "instagram": [
                "mid=abc; sessionid=session-a",
                "mid=def; sessionid=session-b",
                "mid=ghi; sessionid=session-c"
            ]
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_auth_pool.settings.IG_AUTH_COOKIES_FILE",
        auth_file,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_auth_pool.settings.IG_AUTH_MAX_CONTEXTS_PER_ATTEMPT",
        2,
    )

    first = InstagramFastExtractor()
    second = InstagramFastExtractor()

    assert first.auth_pool is second.auth_pool
    assert [context.context_id for context in first.auth_pool.get_contexts_for_attempt()] == [
        "cookie:0",
        "cookie:1",
    ]
    assert [context.context_id for context in second.auth_pool.get_contexts_for_attempt()] == [
        "cookie:2",
        "cookie:0",
    ]


def test_cookie_auth_retry_succeeds_after_public_sources_miss(monkeypatch, tmp_path):
    context = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    extractor = InstagramFastExtractor(auth_pool=InstagramAuthPool([context]))
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")
    seen_headers = []

    def fake_request_json(method, url, headers, data=None, timeout=None, auth_context=None):
        seen_headers.append(dict(headers))
        if headers.get("Cookie") != "mid=abc; sessionid=secret":
            return {}
        if "oembed" in url:
            return {"media_id": "123_999"}
        if "/media/123/info/" in url:
            return {
                "items": [
                    {
                        "caption": {"text": "auth-caption"},
                        "video_versions": [
                            {"url": "https://cdninstagram.example/video.mp4", "width": 720, "height": 1280}
                        ],
                    }
                ]
            }
        return {}

    monkeypatch.setattr(extractor, "_request_json", fake_request_json)
    monkeypatch.setattr(extractor, "_request_embed_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        extractor,
        "_download_media_items",
        lambda shortcode, media_items, output_dir, auth_context=None: [
            DownloadedMedia(file_path=out_file, media_type="video")
        ],
    )

    result = extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    assert result.success_path == "fast_auth"
    assert any(headers.get("Cookie") == "mid=abc; sessionid=secret" for headers in seen_headers)
    assert any(item.get("auth_kind") == "cookie" for item in result.endpoint_timings)
    assert "secret" not in str(result.endpoint_timings)


def test_auth_failure_moves_to_next_context_in_same_attempt(monkeypatch, tmp_path):
    bad = InstagramAuthContext("cookie:0", "cookie", "mid=bad; sessionid=secret-bad")
    good = InstagramAuthContext("cookie:1", "cookie", "mid=good; sessionid=secret-good")
    pool = InstagramAuthPool([bad, good], max_contexts_per_attempt=2)
    extractor = InstagramFastExtractor(auth_pool=pool)
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")
    seen_cookies = []

    def fake_request_json(method, url, headers, data=None, timeout=None, auth_context=None):
        cookie = headers.get("Cookie")
        seen_cookies.append(cookie)
        if cookie == "mid=bad; sessionid=secret-bad":
            extractor._mark_auth_cooldown_from_response(
                _StatusResponse(403, '{"message":"challenge_required"}'),
                auth_context,
            )
            raise InstagramFastExtractorError("auth_context_unusable")
        if cookie == "mid=good; sessionid=secret-good":
            if "oembed" in url:
                return {"media_id": "123_999"}
            if "/media/123/info/" in url:
                return {
                    "items": [
                        {
                            "caption": {"text": "auth-caption"},
                            "video_versions": [
                                {"url": "https://cdn.example.com/video.mp4", "width": 720, "height": 1280}
                            ],
                        }
                    ]
                }
        return {}

    monkeypatch.setattr(extractor, "_request_json", fake_request_json)
    monkeypatch.setattr(extractor, "_request_embed_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        extractor,
        "_download_media_items",
        lambda shortcode, media_items, output_dir, auth_context=None: [
            DownloadedMedia(file_path=out_file, media_type="video")
        ],
    )

    result = extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    assert result.success_path == "fast_auth"
    assert seen_cookies.count("mid=bad; sessionid=secret-bad") == 1
    assert "mid=good; sessionid=secret-good" in seen_cookies


def test_share_resolution_can_retry_with_auth_context(monkeypatch, tmp_path):
    context = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    extractor = InstagramFastExtractor(auth_pool=InstagramAuthPool([context]))
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")
    seen_headers = []

    def fake_request_raw(**kwargs):
        headers = dict(kwargs["headers"])
        seen_headers.append(headers)
        if headers.get("Cookie") == "mid=abc; sessionid=secret":
            return _Response("https://www.instagram.com/reel/abc123/")
        return None

    monkeypatch.setattr(extractor, "_request_raw", fake_request_raw)
    monkeypatch.setattr(
        extractor,
        "_extract_post",
        lambda shortcode, canonical_url: (
            "caption",
            [ExtractedMedia(url="https://cdn.example.com/video.mp4", media_type="video")],
        ),
    )
    monkeypatch.setattr(
        extractor,
        "_download_media_items",
        lambda shortcode, media_items, output_dir, auth_context=None: [
            DownloadedMedia(file_path=out_file, media_type="video")
        ],
    )

    result = extractor.extract_and_download("https://www.instagram.com/share/reel/share123/", tmp_path)

    assert result.success_path == "fast_auth"
    assert any(headers.get("Cookie") == "mid=abc; sessionid=secret" for headers in seen_headers)
    assert any(item["name"] == "share_resolve" and item.get("auth_kind") == "cookie" for item in result.endpoint_timings)


def test_share_resolution_cooldown_context_is_not_reused_for_post_extraction(monkeypatch, tmp_path):
    bad = InstagramAuthContext("cookie:0", "cookie", "mid=bad; sessionid=secret-bad")
    good = InstagramAuthContext("cookie:1", "cookie", "mid=good; sessionid=secret-good")
    pool = InstagramAuthPool([bad, good], max_contexts_per_attempt=2)
    extractor = InstagramFastExtractor(auth_pool=pool)
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")
    share_cookies = []
    post_cookies = []

    def fake_request_raw(**kwargs):
        cookie = kwargs["headers"].get("Cookie")
        share_cookies.append(cookie)
        if cookie == "mid=bad; sessionid=secret-bad":
            extractor._mark_auth_cooldown_from_response(
                _StatusResponse(403, '{"message":"challenge_required"}'),
                kwargs.get("auth_context"),
            )
            raise InstagramFastExtractorError("auth_context_unusable")
        if cookie == "mid=good; sessionid=secret-good":
            return _Response("https://www.instagram.com/reel/abc123/")
        return None

    def fake_request_json(method, url, headers, data=None, timeout=None, auth_context=None):
        cookie = headers.get("Cookie")
        post_cookies.append(cookie)
        if cookie == "mid=bad; sessionid=secret-bad":
            raise AssertionError("cooled share context reused for post extraction")
        if cookie == "mid=good; sessionid=secret-good":
            if "oembed" in url:
                return {"media_id": "123_999"}
            if "/media/123/info/" in url:
                return {
                    "items": [
                        {
                            "caption": {"text": "auth-caption"},
                            "video_versions": [
                                {"url": "https://cdn.example.com/video.mp4", "width": 720, "height": 1280}
                            ],
                        }
                    ]
                }
        return {}

    monkeypatch.setattr(extractor, "_request_raw", fake_request_raw)
    monkeypatch.setattr(extractor, "_request_json", fake_request_json)
    monkeypatch.setattr(extractor, "_request_embed_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        extractor,
        "_download_media_items",
        lambda shortcode, media_items, output_dir, auth_context=None: [
            DownloadedMedia(file_path=out_file, media_type="video")
        ],
    )

    result = extractor.extract_and_download("https://www.instagram.com/share/reel/share123/", tmp_path)

    assert result.success_path == "fast_auth"
    assert "mid=bad; sessionid=secret-bad" in share_cookies
    assert "mid=bad; sessionid=secret-bad" not in post_cookies
    assert "mid=good; sessionid=secret-good" in post_cookies


def test_share_resolution_reuses_successful_auth_context_for_post_extraction(monkeypatch, tmp_path):
    first = InstagramAuthContext("cookie:0", "cookie", "mid=first; sessionid=secret-first")
    share_good = InstagramAuthContext("cookie:1", "cookie", "mid=share; sessionid=secret-share")
    omitted_by_refresh = InstagramAuthContext("cookie:2", "cookie", "mid=other; sessionid=secret-other")
    pool = InstagramAuthPool(
        [first, share_good, omitted_by_refresh],
        max_contexts_per_attempt=2,
    )
    extractor = InstagramFastExtractor(auth_pool=pool)
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"video")
    post_cookies = []

    def fake_request_raw(**kwargs):
        cookie = kwargs["headers"].get("Cookie")
        if cookie == "mid=share; sessionid=secret-share":
            return _Response("https://www.instagram.com/reel/abc123/")
        return None

    def fake_request_json(method, url, headers, data=None, timeout=None, auth_context=None):
        cookie = headers.get("Cookie")
        post_cookies.append(cookie)
        if cookie != "mid=share; sessionid=secret-share":
            return {}
        if "oembed" in url:
            return {"media_id": "123_999"}
        if "/media/123/info/" in url:
            return {
                "items": [
                    {
                        "caption": {"text": "auth-caption"},
                        "video_versions": [
                            {"url": "https://cdn.example.com/video.mp4", "width": 720, "height": 1280}
                        ],
                    }
                ]
            }
        return {}

    monkeypatch.setattr(extractor, "_request_raw", fake_request_raw)
    monkeypatch.setattr(extractor, "_request_json", fake_request_json)
    monkeypatch.setattr(extractor, "_request_embed_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        extractor,
        "_download_media_items",
        lambda shortcode, media_items, output_dir, auth_context=None: [
            DownloadedMedia(file_path=out_file, media_type="video")
        ],
    )

    result = extractor.extract_and_download("https://www.instagram.com/share/reel/share123/", tmp_path)

    assert result.success_path == "fast_auth"
    auth_post_cookies = [cookie for cookie in post_cookies if cookie]
    assert auth_post_cookies[0] == "mid=share; sessionid=secret-share"


def test_auth_request_status_marks_only_that_context_on_cooldown(monkeypatch):
    now = [100.0]
    cookie = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    bearer = InstagramAuthContext("bearer:0", "bearer", "IGT:2:token")
    pool = InstagramAuthPool([cookie, bearer], cooldown_seconds=30, now_fn=lambda: now[0])
    extractor = InstagramFastExtractor(auth_pool=pool)

    class _Session:
        def request(self, **kwargs):
            return _StatusResponse(403, '{"message":"challenge_required"}')

    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_fast_extractor.requests.Session",
        lambda: _Session(),
    )

    with pytest.raises(InstagramFastExtractorError, match="auth_context_unusable"):
        extractor._request_raw(
            method="GET",
            url="https://i.instagram.com/api/v1/media/123/info/",
            headers=extractor._mobile_headers(cookie),
            auth_context=cookie,
        )

    assert pool.get_contexts_for_attempt() == [bearer]


def test_media_download_auth_headers_are_limited_to_allowlisted_https_hosts(monkeypatch, tmp_path):
    context = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    extractor = InstagramFastExtractor()
    captured = []

    def fake_request_raw(**kwargs):
        captured.append((kwargs["url"], dict(kwargs["headers"])))
        return _StreamResponse()

    monkeypatch.setattr(extractor, "_request_raw", fake_request_raw)

    extractor._download_one_media_item(
        "abc123",
        1,
        ExtractedMedia(
            url="https://scontent.cdninstagram.com/video.mp4",
            media_type="video",
        ),
        tmp_path,
        auth_context=context,
    )
    extractor._download_one_media_item(
        "abc123",
        2,
        ExtractedMedia(
            url="https://evil.example.com/video.mp4",
            media_type="video",
        ),
        tmp_path,
        auth_context=context,
    )

    assert captured[0][1].get("Cookie") == "mid=abc; sessionid=secret"
    assert "Cookie" not in captured[1][1]


def test_bearer_context_is_not_sent_to_media_downloads():
    context = InstagramAuthContext("bearer:0", "bearer", "IGT:2:token")
    extractor = InstagramFastExtractor()

    headers = extractor._download_headers(
        "https://scontent.cdninstagram.com/video.mp4",
        context,
    )

    assert "Authorization" not in headers


def test_media_stream_failures_do_not_buffer_body_or_cooldown_context(monkeypatch):
    context = InstagramAuthContext("cookie:0", "cookie", "mid=abc; sessionid=secret")
    pool = InstagramAuthPool([context])
    extractor = InstagramFastExtractor(auth_pool=pool)

    class _Session:
        def request(self, **kwargs):
            return _StreamingStatusResponse(403)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_fast_extractor.requests.Session",
        lambda: _Session(),
    )

    response = extractor._request_raw(
        method="GET",
        url="https://scontent.cdninstagram.com/video.mp4",
        headers=extractor._download_headers(
            "https://scontent.cdninstagram.com/video.mp4",
            context,
        ),
        stream=True,
        auth_context=context,
        classify_auth_failure=False,
    )

    assert response is None
    assert pool.get_contexts_for_attempt() == [context]


def test_bearer_context_adds_authorization_only_to_mobile_headers():
    context = InstagramAuthContext("bearer:0", "bearer", "IGT:2:token")
    extractor = InstagramFastExtractor()

    mobile_headers = extractor._mobile_headers(context)
    web_headers = extractor._web_headers(context)

    assert mobile_headers["Authorization"] == "Bearer IGT:2:token"
    assert "Authorization" not in web_headers


def test_bearer_context_is_not_cooled_down_when_web_headers_send_no_credentials(monkeypatch):
    context = InstagramAuthContext("bearer:0", "bearer", "IGT:2:token")
    pool = InstagramAuthPool([context])
    extractor = InstagramFastExtractor(auth_pool=pool)

    class _Session:
        def request(self, **kwargs):
            assert "Authorization" not in kwargs["headers"]
            assert "Cookie" not in kwargs["headers"]
            return _StatusResponse(403, '{"message":"login_required"}')

    extractor.session = _Session()
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_fast_extractor.requests.Session",
        lambda: (_ for _ in ()).throw(
            AssertionError("credential-free web request should use the shared session")
        ),
    )

    assert extractor._request_embed_data("abc123", context) == {}
    assert pool.get_contexts_for_attempt() == [context]
