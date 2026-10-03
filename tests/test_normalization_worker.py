"""Offline process-boundary tests for cancellable normalization."""

import asyncio
import importlib
import os
from pathlib import Path
import sys

import pytest

from src.instagram_video_bot.services.download_models import MediaItem, VideoInfo


_SLEEPING_WORKER = """
import json, os, sys, time
from pathlib import Path
payload = json.load(sys.stdin)
Path(payload["info"]["file_path"] + ".pid").write_text(str(os.getpid()))
time.sleep(60)
"""


@pytest.fixture
def worker(monkeypatch, tmp_path):
    module = importlib.import_module(
        "src.instagram_video_bot.services.normalization_worker"
    )
    monkeypatch.setattr(module.settings, "TEMP_DIR", tmp_path)
    return module


def info_at(tmp_path, name="clip"):
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(b"source")
    return VideoInfo(path, "Example", media_items=[MediaItem(path, "video")])


def stub_worker(worker, monkeypatch, code):
    monkeypatch.setattr(worker, "worker_command", lambda: [sys.executable, "-c", code])


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_worker_decodes_normalized_media_and_removes_ipc(
    worker, monkeypatch, tmp_path
):
    original = info_at(tmp_path)
    stub_worker(
        worker,
        monkeypatch,
        """
import json, sys
from pathlib import Path
payload = json.load(sys.stdin)
info = payload["info"]
output = str(Path(info["file_path"]).with_suffix(".ios.mp4"))
Path(output).write_bytes(b"normalized")
info["file_path"] = output
info["media_items"][0]["file_path"] = output
Path(payload["_result_file"]).write_text(json.dumps(info))
""",
    )
    result = await worker.normalize_media_isolated(original)
    assert result.file_path == tmp_path / "clip.ios.mp4"
    assert result.media_items[0].file_path == result.file_path
    assert result.title == "Example"
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["raise SystemExit(2)", "import sys; sys.stdin.read()"]
)
async def test_worker_failure_preserves_original(worker, monkeypatch, tmp_path, code):
    original = info_at(tmp_path)
    stub_worker(worker, monkeypatch, code)
    assert await worker.normalize_media_isolated(original) is original
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
async def test_cancel_keeps_shared_slot_until_process_reaped(
    worker, monkeypatch, tmp_path
):
    first_info, second_info = info_at(tmp_path, "first"), info_at(tmp_path, "second")
    stub_worker(
        worker,
        monkeypatch,
        _SLEEPING_WORKER,
    )
    real_terminate = worker.terminate_process_group
    cleanup_started, allow_cleanup = asyncio.Event(), asyncio.Event()

    async def held_cleanup(process):
        cleanup_started.set()
        await allow_cleanup.wait()
        await real_terminate(process)

    monkeypatch.setattr(worker, "terminate_process_group", held_cleanup)
    first = asyncio.create_task(worker.normalize_media_isolated(first_info))
    second = None
    try:
        await wait_until(lambda: first_info.file_path.with_suffix(".mp4.pid").exists())
        pid = int(first_info.file_path.with_suffix(".mp4.pid").read_text())
        first.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=5)
        second = asyncio.create_task(worker.normalize_media_isolated(second_info))
        first.cancel()  # Repeated cancellation cannot abandon child cleanup.
        await asyncio.sleep(0.08)
        assert not first.done()
        assert not second_info.file_path.with_suffix(".mp4.pid").exists()
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        await wait_until(lambda: second_info.file_path.with_suffix(".mp4.pid").exists())
    finally:
        allow_cleanup.set()
        for task in (first, second):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
async def test_timeout_kills_worker_and_returns_original(worker, monkeypatch, tmp_path):
    original = info_at(tmp_path)
    stub_worker(
        worker,
        monkeypatch,
        _SLEEPING_WORKER,
    )
    real_wait = worker.wait_for_process

    async def wait_after_startup(process, **kwargs):
        await wait_until(original.file_path.with_suffix(".mp4.pid").exists)
        await real_wait(process, **kwargs)

    monkeypatch.setattr(worker, "wait_for_process", wait_after_startup)
    monkeypatch.setattr(worker, "_SECONDS_PER_VIDEO", 0.2)
    assert await worker.normalize_media_isolated(original) is original
    pid = int(original.file_path.with_suffix(".mp4.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
async def test_real_worker_preserves_unprobeable_media(worker, tmp_path):
    original = info_at(tmp_path)
    result = await worker.normalize_media_isolated(original)
    assert result == original
    assert original.file_path.read_bytes() == b"source"
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
async def test_entrypoint_runs_normalizer_and_roundtrips_media(
    worker, monkeypatch, tmp_path
):
    """Exercise real worker entrypoint; replace only external FFmpeg work."""
    original = info_at(tmp_path)
    stub_worker(
        worker,
        monkeypatch,
        """
from dataclasses import replace
from src.instagram_video_bot.services import media_normalizer, normalization_worker

def normalize_item(item):
    output = item.file_path.with_suffix(".ios.mp4")
    output.write_bytes(b"normalized")
    return replace(item, file_path=output, duration=12.5, width=640, height=480)

media_normalizer._normalize_video_item = normalize_item
normalization_worker.main()
""",
    )
    result = await worker.normalize_media_isolated(original)
    assert result.file_path == tmp_path / "clip.ios.mp4"
    assert result.file_path.read_bytes() == b"normalized"
    assert result.duration == 12.5
    assert result.media_items[0].width == 640
    assert result.media_items[0].height == 480
    assert not list(tmp_path.glob("instagram-normalization-*"))


@pytest.mark.asyncio
async def test_cancellation_kills_normalizer_descendants(worker, monkeypatch, tmp_path):
    original = info_at(tmp_path)
    stub_worker(
        worker,
        monkeypatch,
        """
import json, subprocess, sys, time
from pathlib import Path
payload = json.load(sys.stdin)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(payload["info"]["file_path"] + ".pid").write_text(str(child.pid))
time.sleep(60)
""",
    )
    task = asyncio.create_task(worker.normalize_media_isolated(original))
    try:
        pid_path = original.file_path.with_suffix(".mp4.pid")
        await wait_until(pid_path.exists)
        pid = int(pid_path.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Grandchildren can remain zombies until the host init reaps them.
        status = Path(f"/proc/{pid}/stat")
        def is_gone_or_zombie():
            try:
                return status.read_text().rsplit(")", 1)[1].split()[0] == "Z"
            except FileNotFoundError:
                return True

        # Cleanup itself must guarantee this before releasing capacity.
        assert is_gone_or_zombie()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not list(tmp_path.glob("instagram-normalization-*"))
