import threading

from src.instagram_video_bot.services.instagram_provider_runtime import (
    InstagramProviderRuntime,
)


def test_retire_cancels_queued_work_and_creates_fresh_generation():
    runtime = InstagramProviderRuntime()
    running_started = threading.Event()
    release_running = threading.Event()

    def block_worker():
        running_started.set()
        return release_running.wait(timeout=2)

    try:
        running = runtime.submit(
            block_worker,
            max_workers=1,
        )
        assert running_started.wait(timeout=1) is True
        queued = runtime.submit(lambda: "old", max_workers=1)

        assert runtime.retire(running.executor) is True
        assert queued.future.cancelled() is True

        fresh = runtime.submit(lambda: "fresh", max_workers=1)
        assert fresh.executor is not running.executor
        assert fresh.future.result(timeout=1) == "fresh"
    finally:
        release_running.set()
        runtime.shutdown()


def test_shutdown_is_idempotent_and_allows_a_new_generation():
    runtime = InstagramProviderRuntime()
    first = runtime.submit(lambda: "first", max_workers=1)
    assert first.future.result(timeout=1) == "first"

    runtime.shutdown()
    runtime.shutdown()

    second = runtime.submit(lambda: "second", max_workers=1)
    try:
        assert second.executor is not first.executor
        assert second.future.result(timeout=1) == "second"
    finally:
        runtime.shutdown()


def test_retire_ignores_an_executor_that_is_no_longer_active():
    runtime = InstagramProviderRuntime()
    first = runtime.submit(lambda: "first", max_workers=1)
    assert first.future.result(timeout=1) == "first"
    runtime.shutdown()
    second = runtime.submit(lambda: "second", max_workers=1)
    try:
        assert runtime.retire(first.executor) is False
        assert second.future.result(timeout=1) == "second"
    finally:
        runtime.shutdown()
