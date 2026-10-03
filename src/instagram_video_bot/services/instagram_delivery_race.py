"""Bounded Instagram preparation races; final delivery belongs to the shared job."""

from __future__ import annotations

import asyncio
import logging
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
                    after_cleanup(winner)
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


async def prepare_instagram_delivery(downloader, url, output_dir, bot, stager):
    """Prepare at most two candidates, sharing account/provider/upload limits."""
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

        async def stage(info):
            info.media_items = await stager.stage_media(bot, info.media_items)
            if not info.media_items or not all(
                i.telegram_file_id for i in info.media_items
            ):
                raise DownloadError("Incomplete staged candidate")
            return info

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
            return await stage(info)

        async def local():
            info = await local_downloader.download_video(url, dirs["local"])
            return await stage(info)

        def cleaned(winner):
            try:
                for name, directory in dirs.items():
                    if name != winner:
                        shutil.rmtree(directory, ignore_errors=True)
            finally:
                for account in accounts:
                    manager.release_account(account)
                admission.release()

        handed_off = True
        async with asyncio.timeout(settings.INSTAGRAM_ACQUISITION_TIMEOUT_SECONDS):
            winner, info = await first_success(
                {"direct": direct_or_public(), "local": local()},
                after_cleanup=cleaned,
            )
        chosen = direct if winner == "direct" else local_downloader
        downloader.last_provider_metrics = chosen.last_provider_metrics
        downloader.last_account_health_event = chosen.last_account_health_event
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
            downloader.last_provider_metrics = local_downloader.last_provider_metrics
            downloader.last_account_health_event = (
                local_downloader.last_account_health_event
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
