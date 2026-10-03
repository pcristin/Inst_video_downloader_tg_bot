"""Normalization owns its scratch files without touching pre-existing media."""

import asyncio
import sys

import pytest

from src.instagram_video_bot.services import normalization_worker as worker
from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo

_NORMALIZE_FIRST = """
from dataclasses import replace
from src.instagram_video_bot.services import normalization_worker, media_normalizer

def normalize(info):
    first, second = info.media_items
    destination = first.file_path.with_suffix('.ios.mp4')
    destination.write_bytes(b'normalized')
    return replace(info, file_path=destination, media_items=[replace(first, file_path=destination, duration=12.5, width=640), second])

media_normalizer.normalize_instagram_media = normalize
normalization_worker.main()
"""


@pytest.fixture
def album(monkeypatch, tmp_path):
    monkeypatch.setattr(worker.settings, "TEMP_DIR", tmp_path)
    sources = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    for source in sources:
        source.write_bytes(b"original")
    (tmp_path / "first.ios.mp4").write_bytes(b"pre-existing output")
    (tmp_path / ".second.ios-existing.mp4").write_bytes(b"other worker scratch")
    return VideoInfo(
        sources[0], "Album", media_items=[MediaItem(p, "video") for p in sources]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["crash", "timeout", "cancel"])
async def test_failed_worker_removes_partial_and_completed_outputs(
    album, monkeypatch, tmp_path, failure
):
    marker = tmp_path / "ready"
    baseline = {p: p.read_bytes() for p in tmp_path.iterdir()}
    code = f"""
import os, time
from pathlib import Path
from src.instagram_video_bot.services import normalization_worker, media_normalizer

def normalize(info):
    first, second = info.media_items
    first.file_path.with_suffix('.ios.mp4').write_bytes(b'completed')
    second.file_path.with_name('.second.ios-partial.mp4').write_bytes(b'partial')
    Path({str(marker)!r}).write_text('ready')
    {'os._exit(2)' if failure == 'crash' else 'time.sleep(60)'}

media_normalizer.normalize_instagram_media = normalize
normalization_worker.main()
"""
    monkeypatch.setattr(worker, "worker_command", lambda: [sys.executable, "-c", code])
    real_wait = worker.wait_for_process

    async def wait_started(process, **kwargs):
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.01)
        if failure == "cancel":
            raise asyncio.CancelledError
        kwargs["timeout_seconds"] = 0.1
        await real_wait(process, **kwargs)

    monkeypatch.setattr(worker, "wait_for_process", wait_started)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await worker.normalize_media_isolated(album)
    else:
        assert await worker.normalize_media_isolated(album) is album
    marker.unlink()
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == baseline


@pytest.mark.asyncio
async def test_success_publishes_outputs_without_overwriting_existing_files(
    album, monkeypatch, tmp_path
):
    code = _NORMALIZE_FIRST
    monkeypatch.setattr(worker, "worker_command", lambda: [sys.executable, "-c", code])
    result = await worker.normalize_media_isolated(album)
    assert result.file_path == result.media_items[0].file_path
    assert result.file_path.read_bytes() == b"normalized"
    assert result.media_items[0].duration == 12.5
    assert result.media_items[0].width == 640
    assert result.media_items[1].file_path == album.media_items[1].file_path
    assert (tmp_path / "first.ios.mp4").read_bytes() == b"pre-existing output"
    assert all(item.file_path.read_bytes() == b"original" for item in album.media_items)
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
async def test_later_publish_failure_removes_only_new_outputs(
    album, monkeypatch, tmp_path
):
    baseline = {p: p.read_bytes() for p in tmp_path.iterdir()}
    code = """
from dataclasses import replace
from src.instagram_video_bot.services import normalization_worker, media_normalizer

def normalize(info):
    items = []
    for item in info.media_items:
        destination = item.file_path.with_suffix('.ios.mp4')
        destination.write_bytes(b'normalized')
        items.append(replace(item, file_path=destination))
    return replace(info, file_path=items[0].file_path, media_items=items)

media_normalizer.normalize_instagram_media = normalize
normalization_worker.main()
"""
    monkeypatch.setattr(worker, "worker_command", lambda: [sys.executable, "-c", code])
    real_move = worker.shutil.move
    moves = []

    def fail_second(source, destination):
        moves.append(destination)
        if len(moves) == 2:
            raise OSError("output disk failure")
        return real_move(source, destination)

    monkeypatch.setattr(worker.shutil, "move", fail_second)
    assert await worker.normalize_media_isolated(album) is album
    assert len(moves) == 2
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == baseline


@pytest.mark.asyncio
async def test_output_publication_runs_off_event_loop(album, monkeypatch):
    import threading

    code = _NORMALIZE_FIRST
    monkeypatch.setattr(worker, "worker_command", lambda: [sys.executable, "-c", code])
    real_move = worker.shutil.move
    loop_thread = threading.get_ident()
    publication_threads = []

    def record_move(source, destination):
        publication_threads.append(threading.get_ident())
        return real_move(source, destination)

    monkeypatch.setattr(worker.shutil, "move", record_move)
    result = await worker.normalize_media_isolated(album)
    assert result.file_path.read_bytes() == b"normalized"
    assert publication_threads and loop_thread not in publication_threads


@pytest.mark.asyncio
async def test_cancelled_publication_drains_before_removing_owned_files(
    album, monkeypatch, tmp_path
):
    import threading

    baseline = {p: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(
        worker, "worker_command", lambda: [sys.executable, "-c", _NORMALIZE_FIRST]
    )
    real_move = worker.shutil.move
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    destinations = []

    def held_move(source, destination):
        destinations.append(destination)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "test did not release publication"
        return real_move(source, destination)

    monkeypatch.setattr(worker.shutil, "move", held_move)
    task = asyncio.create_task(worker.normalize_media_isolated(album))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert list(tmp_path.glob("instagram-normalization-*"))
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert destinations
        assert {p: p.read_bytes() for p in tmp_path.iterdir()} == baseline
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
