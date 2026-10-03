import asyncio, dataclasses, json, logging, shutil, time, importlib.metadata
from pathlib import Path
from types import SimpleNamespace

logging.disable(logging.CRITICAL)
from telegram import Bot
from telegram.request import HTTPXRequest
from telegram.error import RetryAfter
from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.video_downloader import VideoDownloader
from src.instagram_video_bot.services.instagram_delivery_race import drain_race_cleanup
from benchmark_support import (
    eligible_accounts,
    isolated_account_state,
    prepare_verified_race,
)
from src.instagram_video_bot.services.telegram_media_stager import TelegramMediaStager
from src.instagram_video_bot.services.telegram_media_sender import TelegramMediaSender
from src.instagram_video_bot.services.telegram.request_context import RequestContext
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.utils.account_manager import get_account_manager

SAMPLES = json.loads(Path("/samples.json").read_text())
ACTIVE_CALLS = []


async def timed_call(method, **kwargs):
    started = time.perf_counter()
    value = kwargs.get("video", kwargs.get("photo"))
    kind = (
        "remote"
        if isinstance(value, str) and value.startswith("https://")
        else (
            "local"
            if isinstance(value, str)
            and value.startswith("file:")
            or isinstance(value, Path)
            else "file_id_or_input"
        )
    )
    row = {"method": method.__name__, "input_kind": kind}
    try:
        result = await method(**kwargs)
        row["status"] = "ok"
        return result
    except BaseException as error:
        row["status"] = type(error).__name__
        if isinstance(error, RetryAfter):
            delay = error.retry_after
            row["retry_after_s"] = (
                delay.total_seconds()
                if hasattr(delay, "total_seconds")
                else float(delay)
            )
        raise
    finally:
        row["duration_s"] = round(time.perf_counter() - started, 3)
        ACTIVE_CALLS.append(row)


class MeasuredBot(Bot):
    async def send_video(self, **kwargs):
        return await timed_call(super().send_video, **kwargs)

    async def send_photo(self, **kwargs):
        return await timed_call(super().send_photo, **kwargs)

    async def send_media_group(self, **kwargs):
        return await timed_call(super().send_media_group, **kwargs)


async def batch():
    manager = get_account_manager()
    base = settings.TEMP_DIR
    eligible = eligible_accounts(manager)
    assert len(eligible) >= 2 * len(SAMPLES)
    pairs = [eligible[2 * i : 2 * i + 2] for i in range(len(SAMPLES))]
    chat = settings.TELEGRAM_MEDIA_STORAGE_CHAT_ID or settings.INLINE_STORAGE_CHAT_ID
    results = []
    settings.INSTAGRAM_DELIVERY_RACE_ENABLED = True
    for index, sample in enumerate(SAMPLES):
        if sample["sample"] != 7:
            continue
        for mode in (["current", "race"] if index % 2 == 0 else ["race", "current"]):
            pair = pairs[index]
            trial_accounts = pair if mode == "race" else pair[1:]
            with isolated_account_state(
                manager,
                trial_accounts,
                base / f"accounts-{sample['sample']}-{mode}.json",
            ):
                ACTIVE_CALLS.clear()
                out = {
                    "sample": sample["sample"],
                    "mode": mode,
                    "status": "running",
                    "instagrapi": importlib.metadata.version("instagrapi"),
                }
                print(json.dumps({"event": "started", **out}), flush=True)
                output = base / f"item-{sample['sample']}-{mode}"
                output.mkdir(exist_ok=True)
                downloader = VideoDownloader()
                start = time.perf_counter()
                ctx = RequestContext(
                    request_id=f"integrated-{sample['sample']}-{mode}",
                    chat_id=chat,
                    user_id=0,
                    provider_label="Instagram",
                    normalized_url=sample["url"],
                    original_url=sample["url"],
                    original_message_id=None,
                    status_message=None,
                    quiet_mode=True,
                    joined_existing=False,
                    received_monotonic=start,
                )
                try:
                    async with asyncio.timeout(150):
                        async with MeasuredBot(
                            settings.BOT_TOKEN,
                            base_url=settings.TELEGRAM_BOT_API_BASE_URL,
                            base_file_url=settings.TELEGRAM_BOT_API_BASE_FILE_URL,
                            local_mode=True,
                            request=HTTPXRequest(
                                connection_pool_size=8,
                                read_timeout=40,
                                media_write_timeout=40,
                            ),
                        ) as bot:
                            stager = TelegramMediaStager(chat)
                            if mode == "race":
                                info = await prepare_verified_race(
                                    downloader, sample["url"], output, bot, stager, out
                                )
                            else:
                                info = await downloader.download_video(
                                    sample["url"], output
                                )
                            info.media_items = await stager.stage_media(
                                bot, info.media_items
                            )
                            out["ready_s"] = round(time.perf_counter() - start, 3)
                            out["final_send_invocations"] = 1
                            await TelegramMediaSender(StateStore()).send_media(
                                SimpleNamespace(bot=bot),
                                ctx,
                                info,
                                fallback_to_local_on_rejected_file_id=False,
                            )
                            out.update(
                                status="delivered",
                                first_media_s=round(
                                    ctx.first_media_sent_monotonic - start, 3
                                ),
                                total_s=round(ctx.all_media_sent_monotonic - start, 3),
                                media_count=len(info.media_items),
                                media_types=[i.media_type for i in info.media_items],
                            )
                            # Keep shared HTTP client alive until cancelled storage requests finish.
                            await drain_race_cleanup()
                except Exception as exc:
                    out.update(
                        status="failed",
                        error_class=type(exc).__name__,
                        elapsed_s=round(time.perf_counter() - start, 3),
                    )
                finally:
                    await drain_race_cleanup()
                    out["drained_s"] = round(time.perf_counter() - start, 3)
                    out["telegram_calls"] = list(ACTIVE_CALLS)
                    metrics = dataclasses.asdict(downloader.last_provider_metrics)
                    metrics.pop("instagram_fast_endpoint_timings_json", None)
                    out["provider_metrics"] = metrics
                    out["remaining_background_cleanups"] = len(
                        getattr(
                            asyncio.get_running_loop(), "_instagram_race_cleanup", []
                        )
                    )
                    downloader.instagram_runtime.shutdown()
                results.append(out)
                (base / "instrumented-results.json").write_text(
                    json.dumps(results, indent=2)
                )
                print(json.dumps({"event": "finished", **out}), flush=True)
                shutil.rmtree(output, ignore_errors=True)
                await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(batch())
