import pytest

from src.instagram_video_bot.services.instagram_client import InstagramAuthError, InstagramClient


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


def test_download_video_propagates_checkpoint_after_relogin_attempt(monkeypatch, tmp_path):
    client = InstagramClient(username="u", password="p")
    client.client = _CheckpointDownloadClient()
    client._download_with_ytdlp_first = lambda *_args: None
    client._relogin = lambda: True

    with pytest.raises(InstagramAuthError, match="Manual verification required"):
        client.download_video("https://www.instagram.com/reel/test/", tmp_path)
