from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import NetworkError

from src.instagram_video_bot.services.download_models import MediaItem
from src.instagram_video_bot.services.telegram_media_stager import TelegramMediaStager


class _FlakyStorageBot:
    def __init__(self):
        self.video_calls = []
        self.stream_ids = []
        self.stream_payloads = []

    async def send_video(self, **kwargs):
        self.video_calls.append(kwargs)
        stream = kwargs["video"]
        self.stream_ids.append(id(stream))
        self.stream_payloads.append(stream.read())
        if len(self.video_calls) == 1:
            raise NetworkError("httpx.ReadError")
        return SimpleNamespace(video=SimpleNamespace(file_id="stored-video-id"))


@pytest.mark.asyncio
async def test_stager_retries_storage_video_with_fresh_stream(monkeypatch, tmp_path):
    async def no_sleep(_duration):
        return None

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_media_retry.asyncio.sleep", no_sleep
    )
    video_file = Path(tmp_path / "video.mp4")
    video_file.write_bytes(b"video")
    bot = _FlakyStorageBot()

    staged = await TelegramMediaStager(storage_chat_id=-1001).stage_media(
        bot, [MediaItem(file_path=video_file, media_type="video")]
    )

    assert staged[0].telegram_file_id == "stored-video-id"
    assert bot.stream_payloads == [b"video", b"video"]
    assert bot.stream_ids[0] != bot.stream_ids[1]
    assert all(call["chat_id"] == -1001 for call in bot.video_calls)


@pytest.mark.asyncio
async def test_stager_preserves_existing_file_id_without_upload(tmp_path):
    video_file = Path(tmp_path / "video.mp4")
    item = MediaItem(
        file_path=video_file,
        media_type="video",
        telegram_file_id="cached-video-id",
    )
    bot = _FlakyStorageBot()

    staged = await TelegramMediaStager(storage_chat_id=-1001).stage_media(bot, [item])

    assert staged == [item]
    assert bot.video_calls == []


def test_stager_only_enables_streaming_for_supported_video_suffixes(tmp_path):
    item = MediaItem(file_path=tmp_path / "video.webm", media_type="video")

    assert "supports_streaming" not in TelegramMediaStager._video_kwargs(item)


def test_storage_missing_file_id_raises_domain_error():
    from src.instagram_video_bot.services.download_models import VideoDownloadError

    with pytest.raises(VideoDownloadError):
        TelegramMediaStager._extract_file_id(SimpleNamespace(), "video")


@pytest.mark.asyncio
@pytest.mark.parametrize("request_count", [1, 2, 3])
async def test_staging_concurrent_ordered_and_globally_bounded(tmp_path, request_count):
    import asyncio

    active = peak = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    class Bot:
        async def send_video(self, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                both_started.set()
            try:
                await release.wait()
                return SimpleNamespace(
                    video=SimpleNamespace(file_id=Path(kwargs["video"].name).stem)
                )
            finally:
                active -= 1

    items = []
    for i in range(6):
        path = tmp_path / f"{i}.mp4"
        path.write_bytes(b"video")
        items.append(MediaItem(file_path=path, media_type="video"))
    bot = Bot()
    size = 6 // request_count
    tasks = [
        asyncio.create_task(
            TelegramMediaStager(-1001).stage_media(bot, items[i : i + size])
        )
        for i in range(0, 6, size)
    ]
    try:
        await asyncio.wait_for(both_started.wait(), 1)
        await asyncio.sleep(0)
        assert peak == 2
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert [x.telegram_file_id for result in results for x in result] == [
        str(i) for i in range(6)
    ]


@pytest.mark.asyncio
async def test_staging_partial_success_survives_failure_and_siblings_cancel(tmp_path):
    import asyncio
    from telegram.error import BadRequest

    saved = asyncio.Event()
    sibling_started = asyncio.Event()
    cancelled = []

    class Bot:
        async def send_video(self, **kwargs):
            name = Path(kwargs["video"].name).stem
            if name == "0":
                saved.set()
                return SimpleNamespace(video=SimpleNamespace(file_id="saved"))
            if name == "1":
                await saved.wait()
                await sibling_started.wait()
                raise BadRequest("failed")
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(name)
                raise

    items = []
    for i in range(3):
        path = tmp_path / f"{i}.mp4"
        path.write_bytes(b"video")
        items.append(MediaItem(file_path=path, media_type="video"))
    with pytest.raises(BadRequest):
        await asyncio.wait_for(TelegramMediaStager(-1001).stage_media(Bot(), items), 1)
    assert items[0].telegram_file_id == "saved"
    assert cancelled == ["2"]


@pytest.mark.asyncio
async def test_staging_cancellation_closes_all_active_uploads(tmp_path):
    import asyncio

    opened = []
    started = asyncio.Event()

    class Bot:
        async def send_video(self, **kwargs):
            opened.append(kwargs["video"])
            if len(opened) == 2:
                started.set()
            await asyncio.Event().wait()

    items = []
    for i in range(3):
        path = tmp_path / f"{i}.mp4"
        path.write_bytes(b"video")
        items.append(MediaItem(file_path=path, media_type="video"))
    task = asyncio.create_task(TelegramMediaStager(-1001).stage_media(Bot(), items))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(opened) == 2
    assert all(stream.closed for stream in opened)


@pytest.mark.asyncio
async def test_retry_after_pauses_other_requests_even_after_final_attempt(
    monkeypatch, tmp_path
):
    import asyncio
    import datetime
    from telegram.error import RetryAfter
    from src.instagram_video_bot.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_MEDIA_UPLOAD_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(settings, "TELEGRAM_MEDIA_UPLOAD_RETRY_BACKOFF_SECONDS", 0)
    times = []

    class Bot:
        async def send_video(self, **kwargs):
            times.append(asyncio.get_running_loop().time())
            if len(times) == 1:
                raise RetryAfter(datetime.timedelta(seconds=0.05))
            return SimpleNamespace(video=SimpleNamespace(file_id="saved"))

    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    bot = Bot()
    with pytest.raises(RetryAfter):
        await TelegramMediaStager(-1001).stage_media(
            bot, [MediaItem(file_path=path, media_type="video")]
        )
    await TelegramMediaStager(-1001).stage_media(
        bot, [MediaItem(file_path=path, media_type="video")]
    )
    assert times[1] - times[0] >= 0.045


@pytest.mark.asyncio
async def test_flood_recovery_staggers_waiters_then_restores_normal_rate():
    import asyncio
    from src.instagram_video_bot.services.telegram_media_stager import _UploadState

    state = _UploadState(asyncio.Semaphore(2))
    state.defer(-1001, 0.02)
    released = []

    async def wait():
        await state.wait(-1001)
        released.append(asyncio.get_running_loop().time())

    await asyncio.gather(wait(), wait())
    assert released[1] - released[0] >= 0.95
    assert -1001 not in state.chat_ready_at
    # A fresh batch has no historical one-second penalty.
    await asyncio.wait_for(asyncio.gather(wait(), wait()), 0.2)
    assert -1001 not in state.chat_locks
    assert -1001 not in state.chat_waiters


@pytest.mark.asyncio
async def test_cancelled_waiter_preserves_active_flood_deadline():
    import asyncio
    from src.instagram_video_bot.services.telegram_media_stager import _UploadState

    state = _UploadState(asyncio.Semaphore(2))
    state.defer(-1001, 30)
    deadline = state.chat_ready_at[-1001]
    task = asyncio.create_task(state.wait(-1001))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.chat_ready_at[-1001] == deadline


def test_closed_loop_upload_state_is_collectable_after_contention(monkeypatch):
    import asyncio
    import gc
    import weakref
    from src.instagram_video_bot.config.settings import settings
    from src.instagram_video_bot.services.telegram_media_stager import _upload_state

    monkeypatch.setattr(settings, "TELEGRAM_MEDIA_STAGE_CONCURRENCY", 1)
    bot = SimpleNamespace()

    async def contend():
        loop_ref = weakref.ref(asyncio.get_running_loop())
        state = _upload_state(bot)
        await state.semaphore.acquire()
        waiter = asyncio.create_task(state.semaphore.acquire())
        await asyncio.sleep(0)
        state.semaphore.release()
        await waiter
        state.semaphore.release()
        return loop_ref, weakref.ref(state)

    loop_ref, state_ref = asyncio.run(contend())
    gc.collect()
    assert loop_ref() is None
    assert state_ref() is None
