import asyncio
import json
import sys

import pytest

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.instagram_client import InstagramClient
from src.instagram_video_bot.services.instagram_auth_pool import (
    load_configured_instagram_auth_pool,
)
from src.instagram_video_bot.services import instagram_isolated_worker as worker
from src.instagram_video_bot.services.video_downloader import (
    InstagramProviderTimeoutError,
)


def test_public_equal_resolution_prefers_smaller_muxed_source():
    source = InstagramClient._public_ytdlp_source(
        {
            "formats": [
                {
                    "url": "https://big",
                    "height": 1080,
                    "width": 1920,
                    "filesize": 100,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                },
                {
                    "url": "https://small",
                    "height": 1080,
                    "width": 1920,
                    "filesize": 50,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                },
                {
                    "url": "https://low",
                    "height": 720,
                    "width": 1280,
                    "filesize": 20,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                },
            ]
        }
    )
    assert source.visual_url == "https://small"


def test_auth_pool_refreshes_on_replacement(monkeypatch, tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"instagram": ["first"]}))
    monkeypatch.setattr(settings, "IG_AUTH_COOKIES_FILE", path)
    first = load_configured_instagram_auth_pool()
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps({"instagram": ["second"]}))
    replacement.replace(path)
    second = load_configured_instagram_auth_pool()
    assert second is not first
    assert second.get_contexts_for_attempt()[0].value == "second"
    assert second.health()["configured"] == 1


@pytest.mark.asyncio
async def test_public_metadata_has_short_killable_deadline(monkeypatch):
    monkeypatch.setattr(settings, "IG_PUBLIC_METADATA_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(
        worker,
        "worker_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    with pytest.raises(InstagramProviderTimeoutError, match="metadata"):
        await asyncio.wait_for(
            worker.run_isolated_instagram_operation(
                {"action": "public"}, timeout_seconds=60
            ),
            2,
        )


@pytest.mark.asyncio
async def test_metadata_completion_grants_separate_transfer_budget(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "IG_PUBLIC_METADATA_TIMEOUT_SECONDS", 0.2)
    script = (
        "import os,time,json; from pathlib import Path; "
        'Path(os.environ["IG_WORKER_PHASE_PATH"]).write_text("transfer"); '
        "time.sleep(0.3); "
        'Path(os.environ["IG_WORKER_RESULT_PATH"]).write_text(json.dumps({"ok":True,"result":None}))'
    )
    monkeypatch.setattr(
        worker, "worker_command", lambda: [sys.executable, "-c", script]
    )
    assert (
        await worker.run_isolated_instagram_operation(
            {"action": "public"}, timeout_seconds=1
        )
        is None
    )


@pytest.mark.asyncio
async def test_fast_circuit_shared_and_allows_only_one_recovery_probe(monkeypatch):
    from src.instagram_video_bot.services.video_downloader import VideoDownloader

    monkeypatch.setattr(settings, "IG_FAST_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(settings, "IG_FAST_CIRCUIT_COOLDOWN_SECONDS", 10)
    assert VideoDownloader._allow_fast_attempt()
    VideoDownloader._record_fast_outcome(False)
    assert VideoDownloader._allow_fast_attempt()
    VideoDownloader._record_fast_outcome(False)
    assert not VideoDownloader._allow_fast_attempt()
    VideoDownloader._fast_retry_at = 0
    assert VideoDownloader._allow_fast_attempt()
    assert not VideoDownloader._allow_fast_attempt()
    VideoDownloader._record_fast_outcome(True)
    assert VideoDownloader._allow_fast_attempt()


def test_auth_cooldowns_round_trip_without_secrets(tmp_path):
    from src.instagram_video_bot.services.instagram_auth_pool import InstagramAuthPool

    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"instagram": ["secret-cookie"]}))
    parent = InstagramAuthPool.from_file(path)
    child = InstagramAuthPool.from_file(path)
    child.mark_cooldown(child.get_contexts_for_attempt()[0], "http_401")
    state = child.export_cooldowns()
    assert "secret-cookie" not in json.dumps(state)
    parent.import_cooldowns(state)
    assert not parent.available
    assert parent.health()["cooling_down"] == 1


@pytest.mark.asyncio
async def test_instagram_slot_contends_on_global_execution_gate(monkeypatch):
    from src.instagram_video_bot.services.video_downloader import VideoDownloader
    from src.instagram_video_bot.services.execution_limits import (
        get_global_execution_semaphore,
    )

    monkeypatch.setattr(settings, "GLOBAL_MAX_CONCURRENT_JOBS", 1)
    entered = asyncio.Event()

    async def instagram_work():
        async with VideoDownloader._instagram_provider_slot():
            entered.set()

    async with get_global_execution_semaphore():
        task = asyncio.create_task(instagram_work())
        await asyncio.sleep(0)
        assert not entered.is_set()
    await asyncio.wait_for(task, 1)
    assert entered.is_set()


def test_auth_replacement_does_not_inherit_another_credentials_cooldown(tmp_path):
    from src.instagram_video_bot.services.instagram_auth_pool import InstagramAuthPool

    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"instagram": ["old-cookie"]}))
    old = InstagramAuthPool.from_file(path)
    old.mark_cooldown(old.get_contexts_for_attempt()[0], "http_401")
    path.write_text(json.dumps({"instagram": ["replacement-cookie"]}))
    fresh = InstagramAuthPool.from_file(path)
    fresh.import_cooldowns(old.export_cooldowns())
    assert fresh.available


@pytest.mark.asyncio
async def test_overall_acquisition_deadline_bounds_fallback(monkeypatch, tmp_path):
    from src.instagram_video_bot.services.video_downloader import VideoDownloader

    monkeypatch.setattr(settings, "INSTAGRAM_ACQUISITION_TIMEOUT_SECONDS", 0.1)

    async def stalled(self, url, output_dir):
        await asyncio.sleep(10)

    monkeypatch.setattr(VideoDownloader, "_acquire_instagram_media", stalled)
    with pytest.raises(InstagramProviderTimeoutError, match="acquisition"):
        await VideoDownloader().download_video(
            "https://www.instagram.com/reel/example/", tmp_path
        )


@pytest.mark.parametrize(
    "failure", ["capped_video", "invalid_entry", "transfer_false", "transfer_exception"]
)
def test_public_carousel_failure_is_atomic_and_preserves_existing_files(
    monkeypatch, tmp_path, failure
):
    import types

    photo = {"thumbnails": [{"url": "https://photo", "ext": "jpg"}]}
    video = {
        "formats": [
            {
                "url": "https://video",
                "height": 1080,
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
            }
        ]
    }
    entries = [photo, None if failure == "invalid_entry" else video]

    class FakeYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {"entries": entries}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(
        settings, "IG_PUBLIC_MAX_HEIGHT", 720 if failure == "capped_video" else 0
    )
    existing = tmp_path / "public_1.jpg"
    existing.write_bytes(b"existing original")

    def download(source, path):
        path.write_bytes(b"new bytes")
        if source.is_video:
            if failure == "transfer_exception":
                raise RuntimeError("failed transfer")
            if failure == "transfer_false":
                return False
        return True

    monkeypatch.setattr(InstagramClient, "_download_public_source", download)
    result = InstagramClient.download_public_ytdlp_media(
        "https://www.instagram.com/p/example/", tmp_path
    )
    assert result is None
    assert existing.read_bytes() == b"existing original"
    assert list(tmp_path.iterdir()) == [existing]


@pytest.mark.parametrize("declared_size", [None, "6"])
@pytest.mark.parametrize("source_limit, telegram_limit", [(5, 10), (10, 5)])
def test_public_stream_enforces_smaller_source_and_telegram_limits(
    monkeypatch, tmp_path, declared_size, source_limit, telegram_limit
):
    import types

    response = types.SimpleNamespace(
        headers={"Content-Length": declared_size} if declared_size else {},
        raise_for_status=lambda: None,
        iter_content=lambda **kwargs: iter([b"123", b"456"]),
        close=lambda: None,
    )
    monkeypatch.setattr(settings, "IG_PUBLIC_MAX_SOURCE_BYTES", source_limit)
    monkeypatch.setattr(settings, "TELEGRAM_MAX_UPLOAD_BYTES", telegram_limit)
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get",
        lambda *args, **kwargs: response,
    )
    path = tmp_path / "source.mp4"
    with pytest.raises(RuntimeError, match="5-byte"):
        InstagramClient._download_url_to_path("https://example.com/source.mp4", path)
    assert not path.exists()


def test_public_smaller_format_preference_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "IG_PUBLIC_PREFER_SMALLER_FORMATS", False)
    entry = {
        "formats": [
            {
                "url": "https://small",
                "height": 1080,
                "filesize": 50,
                "tbr": 100,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
            },
            {
                "url": "https://large",
                "height": 1080,
                "filesize": 100,
                "tbr": 200,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
            },
        ]
    }
    assert InstagramClient._public_ytdlp_source(entry).visual_url == "https://large"


@pytest.mark.parametrize(
    "vcodec,acodec", [("h264", "aac"), ("avc1.640028", "mp4a.40.2")]
)
def test_public_prefers_compatible_muxed_source_over_smaller_vp9(vcodec, acodec):
    entry = {
        "formats": [
            {
                "url": "https://vp9",
                "height": 1920,
                "width": 1080,
                "fps": 30,
                "filesize": 100,
                "vcodec": "vp9",
                "acodec": "aac",
                "ext": "mp4",
            },
            {
                "url": "https://h264",
                "height": 1920,
                "width": 1080,
                "fps": 30,
                "filesize": 200,
                "vcodec": vcodec,
                "acodec": acodec,
                "ext": "mp4",
            },
        ]
    }
    assert InstagramClient._public_ytdlp_source(entry).visual_url == "https://h264"


def test_public_compatible_separate_video_keeps_aac_audio():
    entry = {
        "formats": [
            {
                "url": "https://vp9",
                "height": 1920,
                "fps": 30,
                "filesize": 100,
                "vcodec": "vp9",
                "acodec": "aac",
                "ext": "mp4",
            },
            {
                "url": "https://h264",
                "height": 1920,
                "fps": 30,
                "filesize": 200,
                "vcodec": "avc1.640028",
                "acodec": "none",
                "ext": "mp4",
            },
            {
                "url": "https://opus",
                "vcodec": "none",
                "acodec": "opus",
                "abr": 160,
                "ext": "webm",
            },
            {
                "url": "https://aac",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 128,
                "ext": "m4a",
            },
        ]
    }
    source = InstagramClient._public_ytdlp_source(entry)
    assert source.visual_url == "https://h264"
    assert source.audio_url == "https://aac"


@pytest.mark.parametrize("compatible_height,compatible_fps", [(720, 60), (1080, 24)])
def test_public_compatibility_preference_does_not_reduce_resolution_or_fps(
    compatible_height, compatible_fps
):
    entry = {
        "formats": [
            {
                "url": "https://vp9",
                "height": 1080,
                "fps": 30,
                "filesize": 100,
                "vcodec": "vp9",
                "acodec": "aac",
                "ext": "mp4",
            },
            {
                "url": "https://h264",
                "height": compatible_height,
                "fps": compatible_fps,
                "filesize": 200,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
            },
        ]
    }
    assert InstagramClient._public_ytdlp_source(entry).visual_url == "https://vp9"


def test_public_compatible_format_preference_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "IG_PUBLIC_PREFER_COMPATIBLE_FORMATS", False)
    entry = {
        "formats": [
            {
                "url": "https://vp9",
                "height": 1080,
                "filesize": 100,
                "vcodec": "vp9",
                "acodec": "aac",
                "ext": "mp4",
            },
            {
                "url": "https://h264",
                "height": 1080,
                "filesize": 200,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
            },
        ]
    }
    assert InstagramClient._public_ytdlp_source(entry).visual_url == "https://vp9"
