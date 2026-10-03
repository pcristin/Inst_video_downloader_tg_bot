"""Bounded Instagram preparation races; final delivery belongs to the shared job."""

from __future__ import annotations

import asyncio
import logging
import inspect
import shutil
import tempfile
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from ..config.settings import settings
from .download_models import DownloadError
from . import video_downloader as providers

logger = logging.getLogger(__name__)


def _cleanup_tasks():
    loop = asyncio.get_running_loop()
    if not hasattr(loop, "_instagram_race_cleanup"):
        loop._instagram_race_cleanup = set()
    return loop._instagram_race_cleanup


async def drain_race_cleanup():
    """Wait for owned losing workers before application shutdown."""
    while tasks := list(_cleanup_tasks()):
        await asyncio.gather(
            *(asyncio.shield(t) for t in tasks), return_exceptions=True
        )


async def first_success(candidates, *, after_cleanup=None):
    """Return one ready candidate without making delivery await losing workers."""
    tasks = {asyncio.create_task(coro): name for name, coro in candidates.items()}
    pending = set(tasks)
    winner = None
    last_error = None
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            # Dict insertion order makes simultaneously ready candidates deterministic.
            for task in tasks:
                if task not in done:
                    continue
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as error:
                    last_error = error
                    continue
                winner = tasks[task]
                return winner, result
        raise DownloadError("No media preparation candidate succeeded") from last_error
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

        async def finish():
            started = perf_counter()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                if after_cleanup:
                    completion = after_cleanup(winner)
                    if inspect.isawaitable(completion):
                        await completion
                logger.info(
                    "Instagram race workers drained",
                    extra={
                        "race_winner": winner,
                        "cleanup_duration_ms": int((perf_counter() - started) * 1000),
                    },
                )

        cleanup = asyncio.create_task(finish())
        owned = _cleanup_tasks()
        owned.add(cleanup)

        def completed(task):
            owned.discard(task)
            if not task.cancelled():
                task.exception()

        cleanup.add_done_callback(completed)
        if winner is None:
            # On cancellation/failure the caller must not delete live workers' files.
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue


def race_available(url, stager):
    path = urlparse(url).path
    return bool(
        settings.INSTAGRAM_DELIVERY_RACE_ENABLED
        and settings.INSTAGRAM_AUTH_FIRST_ENABLED
        and settings.INSTAGRAM_ISOLATED_WORKERS_ENABLED
        and stager is not None
        and path.startswith(("/p/", "/reel/", "/reels/", "/tv/"))
        and providers.get_account_manager() is not None
    )


def _reserve_accounts():
    """Reserve both saved-session accounts before either candidate can run."""
    manager = providers.get_account_manager()
    if manager is None:
        return None
    excluded = {
        a.username
        for a in manager.get_available_accounts()
        if not a.session_file or not Path(a.session_file).is_file()
    }
    if manager.get_leasable_account_count(excluded_usernames=excluded) < 2:
        return None
    first = manager.acquire_account(excluded_usernames=excluded)
    if first is None:
        return None
    second = manager.acquire_account(excluded_usernames=excluded | {first.username})
    if second is None:
        manager.release_account(first)
        return None
    return manager, (first, second)


async def prepare_instagram_delivery(
    downloader,
    url,
    output_dir,
    bot,
    stager,
    *,
    on_account_health=None,
    on_staging_attempt=None,
):
    """Prepare at most two candidates, sharing account/provider/upload limits."""
    downloader.last_race_staging_duration_ms = 0
    downloader.last_race_download_duration_ms = None
    loop = asyncio.get_running_loop()
    if not hasattr(loop, "_instagram_race_admission"):
        loop._instagram_race_admission = asyncio.Semaphore(
            max(1, settings.INSTAGRAM_DELIVERY_RACE_MAX_ACTIVE)
        )
    admission = loop._instagram_race_admission
    if not race_available(url, stager) or admission.locked():
        return await downloader.download_video(url, output_dir)
    await admission.acquire()
    dirs = {}
    handed_off = False
    public_owned = asyncio.Event()
    reservation = None
    try:
        reservation = _reserve_accounts()
        if reservation is None:
            return await downloader.download_video(url, output_dir)
        manager, accounts = reservation
        direct = providers.VideoDownloader()
        local_downloader = providers.VideoDownloader()
        direct.last_provider_metrics.provider = "instagram"
        local_downloader.race_public_owned = public_owned
        local_downloader.race_reserved_account = accounts[1]
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ("direct", "local"):
            dirs[name] = Path(tempfile.mkdtemp(prefix=f"race-{name}-", dir=output_dir))

        staging_attempts = {}
        local_acquisition_ms = None

        async def stage(info, candidate):
            uploads = [item for item in info.media_items if not item.telegram_file_id]
            media_bytes = 0
            for item in uploads:
                if item.remote_url:
                    media_bytes = None
                    break
                try:
                    media_bytes += item.file_path.stat().st_size
                except OSError:
                    media_bytes = None
                    break
            started = perf_counter()
            attempt = dict(
                candidate=candidate,
                status="delivered",
                error_class=None,
                media_bytes=media_bytes,
                media_count=len(uploads),
            )
            try:
                info.media_items = await stager.stage_media(bot, info.media_items)
                if not info.media_items or not all(
                    i.telegram_file_id for i in info.media_items
                ):
                    raise DownloadError("Incomplete staged candidate")
                return info
            except BaseException as error:
                attempt["status"] = (
                    "cancelled"
                    if isinstance(error, asyncio.CancelledError)
                    else "failed"
                )
                attempt["error_class"] = type(error).__name__
                raise
            finally:
                attempt["duration_ms"] = max(
                    0, round((perf_counter() - started) * 1000)
                )
                if uploads:
                    staging_attempts[candidate] = attempt

        async def direct_or_public():
            try:
                async with asyncio.timeout(
                    settings.INSTAGRAM_AUTH_FIRST_TIMEOUT_SECONDS
                ):
                    info = await direct._download_with_account_leases(
                        url,
                        dirs["direct"],
                        None,
                        priority_only=True,
                        direct_sources=True,
                        reserved_account=accounts[0],
                    )
            except Exception as error:
                # Metadata was usable but its sources are unsuitable: keep the local
                # branch; another public download adds CPU and duplicate album uploads.
                if "direct_candidate_ineligible" in str(error):
                    raise
                if public_owned.is_set():
                    raise
                public_owned.set()
                # Only provider acquisition consumes this budget. Normalization
                # and Telegram staging have their own timeouts and capacity gates.
                async with asyncio.timeout(
                    max(0.1, settings.INSTAGRAM_ACQUISITION_TIMEOUT_SECONDS)
                ):
                    async with direct._instagram_provider_slot():
                        info = await direct._run_instagram_operation(
                            lambda: direct.instagram_adapter.download_with_public_ytdlp(
                                url, dirs["direct"]
                            ),
                            action="public",
                            url=url,
                            output_dir=dirs["direct"],
                        )
                if info is None:
                    raise DownloadError("Public media unavailable")
                info = await direct._normalize_instagram_result(info)
                direct.last_provider_metrics.instagram_success_path = "race_public"
                direct.last_provider_metrics.failure_class = None
            else:
                direct.last_provider_metrics.instagram_success_path = "race_direct"
            return await stage(info, "direct")

        async def local():
            nonlocal local_acquisition_ms
            started = perf_counter()
            try:
                info = await local_downloader.download_video(url, dirs["local"])
            finally:
                local_acquisition_ms = max(0, round((perf_counter() - started) * 1000))
            return await stage(info, "local")

        async def cleaned(winner):
            try:
                for name, directory in dirs.items():
                    if name != winner:
                        shutil.rmtree(directory, ignore_errors=True)
            finally:
                for account in accounts:
                    manager.release_account(account)
                admission.release()
            if on_staging_attempt is not None:
                for candidate, attempt in staging_attempts.items():
                    try:
                        await on_staging_attempt(
                            {
                                **attempt,
                                "stage": (
                                    "storage_upload"
                                    if candidate == winner
                                    else f"race_storage_upload_{candidate}"
                                ),
                            }
                        )
                    except Exception:
                        logger.exception("Failed to record race staging metrics")
            # Workers may publish a health event while draining after cancellation.
            # Notify only after leases and admission are free; this I/O remains
            # owned by race cleanup and cannot delay delivery of the winner.
            if on_account_health is not None:
                for candidate in (direct, local_downloader):
                    event = candidate.last_account_health_event
                    if event is not None:
                        try:
                            await on_account_health(event)
                        except Exception:
                            logger.exception("Failed to notify race account health")

        handed_off = True
        winner, info = await first_success(
            {"direct": direct_or_public(), "local": local()},
            after_cleanup=cleaned,
        )
        downloader.last_race_staging_duration_ms = staging_attempts.get(winner, {}).get(
            "duration_ms", 0
        )
        chosen = direct if winner == "direct" else local_downloader
        downloader.last_provider_metrics = chosen.last_provider_metrics
        downloader.last_account_health_event = (
            chosen.last_account_health_event if on_account_health is None else None
        )
        logger.info(
            "Instagram delivery candidate selected",
            extra={
                "race_winner": winner,
                "media_count": len(info.media_items),
            },
        )
        return info
    except BaseException:
        if handed_off:
            # Both candidates have drained before a failed race propagates.
            # Preserve the baseline provider diagnosis for request metrics.
            # Its acquisition duration excludes both upload work and loser cleanup.
            downloader.last_race_download_duration_ms = local_acquisition_ms
            downloader.last_provider_metrics = local_downloader.last_provider_metrics
            downloader.last_account_health_event = (
                local_downloader.last_account_health_event
                if on_account_health is None
                else None
            )
        raise
    finally:
        if not handed_off:
            for directory in dirs.values():
                shutil.rmtree(directory, ignore_errors=True)
            if reservation is not None:
                for account in reservation[1]:
                    reservation[0].release_account(account)
            admission.release()
