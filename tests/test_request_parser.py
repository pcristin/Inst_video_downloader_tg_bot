from src.instagram_video_bot.services.request_parser import RequestParser


def test_extract_supported_links_normalizes_and_limits():
    text = """
    https://ddinstagram.com/reel/abc123/?utm=1
    https://x.com/user/status/1901234567890123456?s=20
    https://www.youtube.com/shorts/abc123XYZ90?feature=share
    https://example.com/nope
    """

    links = RequestParser.extract_supported_links(text, limit=5)

    assert [link.provider for link in links] == ["instagram", "twitter", "youtube_shorts"]
    assert links[0].normalized_url == "https://www.instagram.com/reel/abc123/"
    assert links[1].normalized_url == "https://x.com/user/status/1901234567890123456"
    assert links[2].normalized_url == "https://www.youtube.com/shorts/abc123XYZ90"


def test_extract_supported_links_dedupes_same_normalized_url():
    text = """
    https://twitter.com/user/status/1901234567890123456
    https://x.com/user/status/1901234567890123456?s=20
    """

    links = RequestParser.extract_supported_links(text, limit=5)

    assert len(links) == 1
    assert links[0].normalized_url == "https://x.com/user/status/1901234567890123456"
