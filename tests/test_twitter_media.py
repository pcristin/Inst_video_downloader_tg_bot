import pytest
from yt_dlp import YoutubeDL
from yt_dlp.extractor.twitter import TwitterIE


def test_photo_tweet_retains_all_original_images_in_order(monkeypatch):
    from src.instagram_video_bot.services.twitter_media import TwitterMediaIE

    status = {
        "id_str": "123",
        "text": "A photo post",
        "extended_entities": {
            "media": [
                {
                    "type": "photo",
                    "media_url_https": f"https://pbs.twimg.com/media/image{i}.jpg",
                }
                for i in range(4)
            ]
        },
    }
    monkeypatch.setattr(TwitterIE, "_extract_status", lambda self, twid: status)
    with YoutubeDL({"quiet": True}) as ydl:
        result = TwitterMediaIE(ydl).extract("https://x.com/example/status/123")
    assert result["title"] == "A photo post"
    assert [item["url"] for item in result["entries"]] == [
        f"https://pbs.twimg.com/media/image{i}.jpg?name=orig" for i in range(4)
    ]
    assert all(item["ext"] == "jpg" for item in result["entries"])


def test_video_tweet_uses_upstream_extractor_without_second_metadata_request(
    monkeypatch,
):
    from src.instagram_video_bot.services.twitter_media import TwitterMediaIE

    calls = []
    status = {"extended_entities": {"media": [{"type": "video"}]}}

    def fetch(self, twid):
        calls.append(twid)
        return status

    def extract(self, url):
        assert self._extract_status("123") is status
        return {
            "id": "123",
            "title": "Video",
            "url": "https://video.twimg.com/test.mp4",
        }

    monkeypatch.setattr(TwitterIE, "_extract_status", fetch)
    monkeypatch.setattr(TwitterIE, "_real_extract", extract)
    with YoutubeDL({"quiet": True}) as ydl:
        result = TwitterMediaIE(ydl).extract("https://x.com/example/status/123")
    assert result["title"] == "Video"
    assert calls == ["123"]


def test_mixed_tweet_keeps_photo_video_photo_order(monkeypatch):
    from src.instagram_video_bot.services.twitter_media import TwitterMediaIE

    photo = {
        "type": "photo",
        "media_url_https": "https://pbs.twimg.com/media/photo.png",
    }
    video = {
        "id_str": "456",
        "type": "video",
        "video_info": {
            "variants": [{"url": "https://video.twimg.com/video.mp4", "bitrate": 1000}]
        },
    }
    monkeypatch.setattr(
        TwitterIE,
        "_extract_status",
        lambda self, twid: {
            "text": "Mixed post",
            "extended_entities": {"media": [photo, video, photo]},
        },
    )
    with YoutubeDL({"quiet": True}) as ydl:
        result = TwitterMediaIE(ydl).extract("https://x.com/example/status/123/photo/1")
    entries = list(result["entries"])
    assert (
        entries[0]["url"]
        == entries[2]["url"]
        == "https://pbs.twimg.com/media/photo.png?name=orig"
    )
    assert entries[1]["formats"][0]["url"] == "https://video.twimg.com/video.mp4"


def test_syndication_does_not_append_unrelated_quoted_photos(monkeypatch):
    from src.instagram_video_bot.services.twitter_media import TwitterMediaIE

    own = {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/own.jpg"}
    quoted = {
        "type": "photo",
        "media_url_https": "https://pbs.twimg.com/media/quoted.jpg",
    }
    monkeypatch.setattr(
        TwitterIE,
        "_extract_status",
        lambda self, twid: {
            "mediaDetails": [own],
            "extended_entities": {"media": [own, quoted]},
        },
    )
    with YoutubeDL({"quiet": True}) as ydl:
        result = TwitterMediaIE(ydl).extract("https://x.com/example/status/123")
    assert len(result["entries"]) == 1
    assert "/own.jpg" in result["entries"][0]["url"]


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/a.jpg", "https://evil.example/a.jpg"]
)
def test_photo_metadata_rejects_non_twitter_media_hosts(monkeypatch, url):
    from src.instagram_video_bot.services.twitter_media import TwitterMediaIE
    from yt_dlp.utils import ExtractorError

    monkeypatch.setattr(
        TwitterIE,
        "_extract_status",
        lambda self, twid: {
            "extended_entities": {"media": [{"type": "photo", "media_url_https": url}]}
        },
    )
    with YoutubeDL({"quiet": True}) as ydl, pytest.raises(ExtractorError):
        TwitterMediaIE(ydl).extract("https://x.com/example/status/123")
