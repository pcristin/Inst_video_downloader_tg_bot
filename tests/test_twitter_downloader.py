import subprocess

import pytest

from src.instagram_video_bot.services import twitter_downloader
from src.instagram_video_bot.services.twitter_downloader import (
    TwitterDownloadError,
    TwitterDownloader,
    TwitterMediaItem,
)


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


def test_collect_files_filters_non_media_sidecars(tmp_path):
    prefix = "twitter_1900000000000000000_1234567890"
    (tmp_path / f"{prefix}_01.mp4").write_bytes(b"video")
    (tmp_path / f"{prefix}_02.jpg").write_bytes(b"photo")
    (tmp_path / f"{prefix}_03.info.json").write_text("{}")
    (tmp_path / f"{prefix}_04.vtt").write_text("subtitle")

    files = TwitterDownloader._collect_files(tmp_path, prefix)

    assert [path.suffix for path in files] == [".mp4", ".jpg"]


def test_download_media_converts_download_timeout_to_twitter_error(tmp_path, monkeypatch):
    downloader = TwitterDownloader(timeout_seconds=5)
    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=5)

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _raise_timeout)

    with pytest.raises(TwitterDownloadError, match="timed out"):
        downloader._download_media_sync(
            "https://twitter.com/user/status/1901234567890123456",
            tmp_path,
        )


def test_download_media_direct_success_does_not_use_proxy_or_fallback(tmp_path, monkeypatch):
    downloader = TwitterDownloader(timeout_seconds=5)
    commands = []
    media_file = tmp_path / "tweet.mp4"
    media_file.write_bytes(b"video")

    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        twitter_downloader,
        "settings",
        type("SettingsStub", (), {"get_proxy_list": staticmethod(lambda: ["http://proxy:8000"])})(),
        raising=False,
    )
    monkeypatch.setattr(
        TwitterDownloader,
        "_collect_files",
        staticmethod(lambda output_dir, prefix: [media_file]),
    )

    def _run(cmd, **kwargs):
        commands.append(cmd)
        if "--skip-download" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Tweet title\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _run)

    result = downloader._download_media_sync(
        "https://twitter.com/user/status/1901234567890123456",
        tmp_path,
    )

    assert result.title == "Tweet title"
    assert result.media_items == [TwitterMediaItem(file_path=media_file, media_type="video")]
    assert len([cmd for cmd in commands if "--skip-download" not in cmd]) == 1
    assert all("--proxy" not in cmd for cmd in commands)


def test_download_media_falls_back_to_first_configured_proxy_after_direct_failure(
    tmp_path, monkeypatch
):
    downloader = TwitterDownloader(timeout_seconds=5)
    commands = []
    proxy = "http://user:secret@proxy-a.example:8000"
    media_file = tmp_path / "tweet.mp4"
    media_file.write_bytes(b"video")

    monkeypatch.setattr(TwitterDownloader, "_proxy_rotation_index", 0, raising=False)
    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        twitter_downloader,
        "settings",
        type("SettingsStub", (), {"get_proxy_list": staticmethod(lambda: [proxy])})(),
        raising=False,
    )
    monkeypatch.setattr(
        TwitterDownloader,
        "_collect_files",
        staticmethod(lambda output_dir, prefix: [media_file]),
    )

    def _run(cmd, **kwargs):
        commands.append(cmd)
        if "--skip-download" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Tweet title\n", stderr="")
        if "--proxy" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="direct failed")

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _run)

    result = downloader._download_media_sync(
        "https://twitter.com/user/status/1901234567890123456",
        tmp_path,
    )

    download_commands = [cmd for cmd in commands if "--skip-download" not in cmd]
    assert result.title == "Tweet title"
    assert "--proxy" not in download_commands[0]
    assert download_commands[1][-2:] == ["--proxy", proxy]
    assert commands[-1][-2:] == ["--proxy", proxy]


def test_download_media_rotates_next_fallback_start_after_proxy_success(tmp_path, monkeypatch):
    downloader = TwitterDownloader(timeout_seconds=5)
    commands = []
    proxies = [
        "http://proxy-a.example:8000",
        "http://proxy-b.example:8000",
    ]
    media_file = tmp_path / "tweet.mp4"
    media_file.write_bytes(b"video")

    monkeypatch.setattr(TwitterDownloader, "_proxy_rotation_index", 0, raising=False)
    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        twitter_downloader,
        "settings",
        type("SettingsStub", (), {"get_proxy_list": staticmethod(lambda: proxies)})(),
        raising=False,
    )
    monkeypatch.setattr(
        TwitterDownloader,
        "_collect_files",
        staticmethod(lambda output_dir, prefix: [media_file]),
    )

    def _run(cmd, **kwargs):
        commands.append(cmd)
        if "--skip-download" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Tweet title\n", stderr="")
        if "--proxy" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="direct failed")

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _run)

    downloader._download_media_sync(
        "https://twitter.com/user/status/1901234567890123456",
        tmp_path,
    )
    downloader._download_media_sync(
        "https://twitter.com/user/status/1901234567890123456",
        tmp_path,
    )
    downloader._download_media_sync(
        "https://twitter.com/user/status/1901234567890123456",
        tmp_path,
    )

    download_commands = [cmd for cmd in commands if "--skip-download" not in cmd]
    assert "--proxy" not in download_commands[0]
    assert download_commands[1][-2:] == ["--proxy", proxies[0]]
    assert "--proxy" not in download_commands[2]
    assert download_commands[3][-2:] == ["--proxy", proxies[1]]
    assert "--proxy" not in download_commands[4]
    assert download_commands[5][-2:] == ["--proxy", proxies[0]]


def test_download_media_all_attempts_fail_without_exposing_proxy_credentials(
    tmp_path, monkeypatch
):
    downloader = TwitterDownloader(timeout_seconds=5)
    proxy = "http://user:secret@proxy-a.example:8000"

    monkeypatch.setattr(TwitterDownloader, "_proxy_rotation_index", 0, raising=False)
    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        twitter_downloader,
        "settings",
        type("SettingsStub", (), {"get_proxy_list": staticmethod(lambda: [proxy])})(),
        raising=False,
    )

    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr=f"auth failed for {proxy}",
        )

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _run)

    with pytest.raises(TwitterDownloadError) as exc_info:
        downloader._download_media_sync(
            "https://twitter.com/user/status/1901234567890123456",
            tmp_path,
        )

    message = str(exc_info.value)
    assert "Twitter/X download failed after 2 attempts" in message
    assert "secret" not in message
    assert proxy not in message


def test_download_media_explicit_proxy_preserves_single_proxy_path(tmp_path, monkeypatch):
    explicit_proxy = "http://user:secret@explicit.example:8000"
    downloader = TwitterDownloader(timeout_seconds=5, proxy=explicit_proxy)
    commands = []
    media_file = tmp_path / "tweet.mp4"
    media_file.write_bytes(b"video")

    monkeypatch.setattr(downloader, "_build_base_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        twitter_downloader,
        "settings",
        type(
            "SettingsStub",
            (),
            {"get_proxy_list": staticmethod(lambda: ["http://fallback.example:8000"])},
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        TwitterDownloader,
        "_collect_files",
        staticmethod(lambda output_dir, prefix: [media_file]),
    )

    def _run(cmd, **kwargs):
        commands.append(cmd)
        if "--skip-download" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Tweet title\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(twitter_downloader.subprocess, "run", _run)

    downloader._download_media_sync(
        "https://twitter.com/user/status/1901234567890123456",
        tmp_path,
    )

    download_commands = [cmd for cmd in commands if "--skip-download" not in cmd]
    assert len(download_commands) == 1
    assert download_commands[0][-2:] == ["--proxy", explicit_proxy]
    assert commands[-1][-2:] == ["--proxy", explicit_proxy]
