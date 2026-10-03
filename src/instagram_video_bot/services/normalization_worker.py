"""Bounded, killable process boundary for Instagram media normalization."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import replace

from ..config.settings import settings
from .async_state import call_state
from .download_models import VideoInfo
from .subprocess_lifecycle import terminate_process_group, wait_for_process

logger = logging.getLogger(__name__)
_SECONDS_PER_VIDEO = 300
_SEMAPHORE_ATTRIBUTE = "_instagram_media_normalization_semaphore"


def worker_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.instagram_video_bot.services.normalization_worker",
    ]


def _shared_semaphore() -> asyncio.Semaphore:
    # The loop owns the semaphore, avoiding a global strong reference back to
    # closed loops after a contended semaphore has bound itself to its loop.
    loop = asyncio.get_running_loop()
    semaphore = getattr(loop, _SEMAPHORE_ATTRIBUTE, None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(
            max(1, getattr(settings, "INSTAGRAM_NORMALIZATION_CONCURRENCY", 1))
        )
        setattr(loop, _SEMAPHORE_ATTRIBUTE, semaphore)
    return semaphore


async def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    await terminate_process_group(process)
    # Keep capacity until the worker has actually been reaped.
    while process.poll() is None:
        await asyncio.sleep(0.02)


async def _finish_process(process: subprocess.Popen[bytes]) -> None:
    cleanup = asyncio.create_task(_kill_and_reap(process))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


def _workspace_inputs(info: VideoInfo, workspace: Path) -> VideoInfo:
    """Link source inputs beside exclusively owned normalizer outputs."""
    replacements = {}
    items = []
    for index, item in enumerate(info.media_items):
        directory = workspace / str(index)
        directory.mkdir()
        source = directory / item.file_path.name
        source.symlink_to(item.file_path.resolve())
        replacements[item.file_path] = source
        items.append(replace(item, file_path=source))
    return replace(
        info,
        file_path=replacements.get(info.file_path, info.file_path),
        media_items=items,
    )


def _publish_result(
    result: VideoInfo, original: VideoInfo, workspace: Path, published: list[Path]
) -> VideoInfo:
    """Publish only returned outputs; never replace a pre-existing source/output."""
    aliases = {
        workspace / str(index) / item.file_path.name: item.file_path
        for index, item in enumerate(original.media_items)
    }
    replacements = {}
    items = []
    for item in result.media_items:
        source = item.file_path
        if source in aliases:
            destination = aliases[source]
        elif source.is_relative_to(workspace):
            # Per-item workspace directories retain the original output location.
            index = int(source.relative_to(workspace).parts[0])
            parent = original.media_items[index].file_path.parent
            destination = parent / source.name
            try:
                with destination.open("xb"):
                    pass
            except FileExistsError:
                with tempfile.NamedTemporaryFile(
                    prefix=f"{source.stem}-",
                    suffix=source.suffix,
                    dir=parent,
                    delete=False,
                ) as output:
                    destination = Path(output.name)
            published.append(destination)
            shutil.move(str(source), str(destination))
        else:
            destination = source
        replacements[source] = destination
        items.append(replace(item, file_path=destination))
    return replace(
        result,
        file_path=replacements.get(result.file_path, result.file_path),
        media_items=items,
    )


async def normalize_media_isolated(info: VideoInfo) -> VideoInfo:
    """Normalize under shared capacity; reap the child before releasing its slot.

    A crashed or timed-out normalizer preserves the original downloaded media,
    matching the synchronous normalizer's best-effort behavior. Cancellation
    propagates only after the child and its FFmpeg process group are killed.
    """
    from .instagram_isolated_worker import decode_result, encode_result

    video_count = sum(item.media_type == "video" for item in info.media_items)
    if not video_count:
        return info
    async with _shared_semaphore():
        result_path: Path | None = None
        process: subprocess.Popen[bytes] | None = None
        workspace: Path | None = None
        published: list[Path] = []
        completed = False
        try:
            workspace = Path(
                tempfile.mkdtemp(
                    prefix="instagram-normalization-", dir=settings.TEMP_DIR
                )
            )
            with tempfile.NamedTemporaryFile(
                prefix="instagram-normalization-",
                suffix=".json",
                dir=settings.TEMP_DIR,
                delete=False,
            ) as result_file:
                result_path = Path(result_file.name)
            # A regular file used as stdin avoids blocking the event loop on a
            # full pipe for large albums. Media metadata includes remote URLs.
            with tempfile.TemporaryFile(dir=settings.TEMP_DIR) as input_file:
                input_file.write(
                    json.dumps(
                        {
                            "info": encode_result(info),
                            "_result_file": str(result_path),
                            "_workspace": str(workspace),
                        }
                    ).encode()
                )
                input_file.seek(0)
                process = subprocess.Popen(
                    worker_command(),
                    stdin=input_file,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            await wait_for_process(
                process,
                timeout_seconds=video_count * _SECONDS_PER_VIDEO,
                timeout_error=TimeoutError("Instagram normalization timed out"),
            )
            if process.returncode != 0:
                raise RuntimeError("Instagram normalization worker failed")
            # Stop descendants before publishing or deleting anything they own.
            await _finish_process(process)
            process = None
            result = decode_result(json.loads(result_path.read_text(encoding="utf-8")))
            if result is None:
                return info
            # Cross-filesystem publication may copy large outputs. Drain that
            # disk operation before cancellation can remove its owned files.
            result = await call_state(
                _publish_result, result, info, workspace, published
            )
            completed = True
            return result
        except Exception as error:
            logger.warning(
                "Isolated Instagram normalization failed; using original media",
                extra={"error_class": type(error).__name__},
            )
            return info
        finally:
            try:
                if process is not None:
                    await _finish_process(process)
            finally:
                if not completed:
                    for path in published:
                        path.unlink(missing_ok=True)
                if workspace is not None:
                    shutil.rmtree(workspace, ignore_errors=True)
                if result_path is not None:
                    result_path.unlink(missing_ok=True)


def main() -> None:
    from .instagram_isolated_worker import decode_result, encode_result
    from .media_normalizer import normalize_instagram_media

    payload = json.load(sys.stdin)
    info = decode_result(payload["info"])
    if info is None:
        raise ValueError("Missing normalization media")
    if "_workspace" in payload:
        info = _workspace_inputs(info, Path(payload["_workspace"]))
    result = normalize_instagram_media(info)
    Path(payload["_result_file"]).write_text(
        json.dumps(encode_result(result)), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
