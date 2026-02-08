from src.instagram_video_bot.services.instagram_client import InstagramClient


class _FailingMediaAPIClient:
    def media_pk_from_url(self, _url: str) -> int:
        return 123456

    def media_info(self, _media_pk: int):
        raise Exception("media info failed")

    def media_info_v1(self, _media_pk: int):
        raise Exception("media info v1 failed")


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
