from src.instagram_video_bot.services.video_downloader import DownloadError, VideoDownloader


def test_temporary_provider_error_is_retriable():
    assert VideoDownloader._is_transient_download_error(DownloadError("temporary unavailable"))


def test_temporary_provider_error_classifies_as_transient_network():
    assert VideoDownloader._classify_download_error(DownloadError("temporary unavailable")) == "transient_network"
