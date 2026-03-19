"""Twitter/X media downloader powered by yt-dlp."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Sequence


class TwitterDownloadError(Exception):
    """Raised when Twitter/X media extraction fails."""


@dataclass
class TwitterMediaItem:
    """Downloaded media item from a tweet."""

    file_path: Path
    media_type: Literal["video", "photo"]


@dataclass
class TwitterDownloadResult:
    """Successful Twitter/X download output."""

    title: str
    media_items: List[TwitterMediaItem]


class TwitterDownloader:
    """Downloads public Twitter/X tweet media with yt-dlp."""

    MEDIA_EXTENSIONS = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
    }

    STATUS_URL_PATTERN = re.compile(
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/\s]+/status/(?P<status_id>\d+)(?:[/?#][^\s]*)?",
        re.IGNORECASE,
    )

    def __init__(self, timeout_seconds: int = 90, proxy: str | None = None, ytdlp_binary: str = "yt-dlp"):
        self.timeout_seconds = timeout_seconds
        self.proxy = proxy
        self.ytdlp_binary = ytdlp_binary

    @classmethod
    def is_supported_url(cls, url: str) -> bool:
        """Return whether URL is a supported Twitter/X status link."""
        return bool(cls.STATUS_URL_PATTERN.search(url.strip()))

    async def download_media(self, url: str, output_dir: Path) -> TwitterDownloadResult:
        """Download all available media for a public tweet."""
        if not self.is_supported_url(url):
            raise TwitterDownloadError("Unsupported Twitter/X URL")
        return await asyncio.to_thread(self._download_media_sync, url, output_dir)

    def _download_media_sync(self, url: str, output_dir: Path) -> TwitterDownloadResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        status_id = self._extract_status_id(url)
        prefix = f"twitter_{status_id}_{int(time.time() * 1000)}"
        output_template = str(output_dir / f"{prefix}_%(autonumber)02d.%(ext)s")

        cmd = self._build_base_command()
        cmd.extend(
            [
                "--no-warnings",
                "--no-progress",
                "--restrict-filenames",
                "--no-part",
                "-o",
                output_template,
                url,
            ]
        )
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "Unknown yt-dlp error").strip()
            raise TwitterDownloadError(f"Twitter/X download failed: {error_text}")

        file_paths = self._collect_files(output_dir, prefix)
        if not file_paths:
            raise TwitterDownloadError("Twitter/X download produced no media files")

        media_items = [
            TwitterMediaItem(file_path=path, media_type=self._infer_media_type(path))
            for path in file_paths
        ]
        return TwitterDownloadResult(title=self._fetch_title(url), media_items=media_items)

    def _build_base_command(self) -> List[str]:
        """Resolve yt-dlp CLI invocation."""
        if shutil.which(self.ytdlp_binary):
            return [self.ytdlp_binary]
        if importlib.util.find_spec("yt_dlp"):
            return [sys.executable, "-m", "yt_dlp"]
        raise TwitterDownloadError("yt-dlp is not installed in this environment")

    def _fetch_title(self, url: str) -> str:
        """Best-effort tweet title extraction for Telegram caption."""
        cmd = self._build_base_command()
        cmd.extend(["--no-warnings", "--skip-download", "--print", "%(title)s", url])
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        if result.returncode != 0:
            return ""
        title = (result.stdout or "").strip().splitlines()
        return title[0].strip() if title else ""

    @classmethod
    def _extract_status_id(cls, url: str) -> str:
        match = cls.STATUS_URL_PATTERN.search(url.strip())
        if not match:
            return "unknown"
        return match.group("status_id")

    @staticmethod
    def _collect_files(output_dir: Path, prefix: str) -> Sequence[Path]:
        files = [
            file_path
            for file_path in sorted(output_dir.glob(f"{prefix}_*"))
            if file_path.is_file()
            and file_path.suffix.lower() not in {".part", ".ytdl"}
            and file_path.suffix.lower() in TwitterDownloader.MEDIA_EXTENSIONS
            and file_path.stat().st_size > 0
        ]
        return files

    @staticmethod
    def _infer_media_type(file_path: Path) -> Literal["video", "photo"]:
        ext = file_path.suffix.lower()
        if ext in {".mp4", ".mov", ".mkv", ".webm"}:
            return "video"
        return "photo"
