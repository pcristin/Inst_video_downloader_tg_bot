import pytest

from src.instagram_video_bot.services.twitter_downloader import TwitterDownloader


@pytest.mark.parametrize(
    "url",
    [
        "https://twitter.com/user/status/1901234567890123456",
        "https://x.com/user/status/1901234567890123456",
        "https://www.x.com/user/status/1901234567890123456?s=20",
    ],
)
def test_twitter_downloader_supports_status_urls(url):
    assert TwitterDownloader.is_supported_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://twitter.com/user",
        "https://x.com/home",
        "https://www.instagram.com/reel/abc123/",
    ],
)
def test_twitter_downloader_rejects_non_status_urls(url):
    assert not TwitterDownloader.is_supported_url(url)
