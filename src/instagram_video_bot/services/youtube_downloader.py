"""YouTube Shorts media downloader powered by yt-dlp."""

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


class YouTubeDownloadError(Exception):
    """Raised when YouTube Shorts extraction fails."""


@dataclass
class YouTubeMediaItem:
    """Downloaded media item from a Shorts URL."""

    file_path: Path
    media_type: Literal["video", "photo"]


@dataclass
class YouTubeDownloadResult:
    """Successful YouTube Shorts download output."""

    title: str
    media_items: List[YouTubeMediaItem]


class YouTubeShortsDownloader:
    """Downloads public YouTube Shorts media with yt-dlp."""

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
        ".m4a",
    }
    SHORTS_URL_PATTERN = re.compile(
        r"https?://(?:www\.|m\.)?youtube\.com/shorts/(?P<video_id>[A-Za-z0-9_-]{6,})(?:[/?#][^\s]*)?$",
        re.IGNORECASE,
    )

    def __init__(self, timeout_seconds: int = 120, ytdlp_binary: str = "yt-dlp"):
        self.timeout_seconds = timeout_seconds
        self.ytdlp_binary = ytdlp_binary

    @classmethod
    def is_supported_url(cls, url: str) -> bool:
        """Return whether URL is a supported YouTube Shorts link."""
        return bool(cls.SHORTS_URL_PATTERN.search(url.strip()))

    async def download_media(self, url: str, output_dir: Path) -> YouTubeDownloadResult:
        """Download media from a public Shorts URL."""
        if not self.is_supported_url(url):
            raise YouTubeDownloadError("Unsupported YouTube Shorts URL")
        return await asyncio.to_thread(self._download_media_sync, url, output_dir)

    def _download_media_sync(self, url: str, output_dir: Path) -> YouTubeDownloadResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        video_id = self._extract_video_id(url)
        prefix = f"youtube_{video_id}_{int(time.time() * 1000)}"
        output_template = str(output_dir / f"{prefix}_%(autonumber)02d.%(ext)s")

        cmd = self._build_base_command()
        cmd.extend(
            [
                "--no-warnings",
                "--no-progress",
                "--restrict-filenames",
                "--no-part",
                "--merge-output-format",
                "mp4",
                "-o",
                output_template,
                url,
            ]
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise YouTubeDownloadError(
                f"YouTube Shorts download timed out after {self.timeout_seconds} seconds"
            ) from exc
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "Unknown yt-dlp error").strip()
            raise YouTubeDownloadError(f"YouTube Shorts download failed: {error_text}")

        file_paths = self._collect_files(output_dir, prefix)
        if not file_paths:
            raise YouTubeDownloadError("YouTube Shorts download produced no media files")

        media_items = [
            YouTubeMediaItem(file_path=path, media_type=self._infer_media_type(path))
            for path in file_paths
        ]
        return YouTubeDownloadResult(title=self._fetch_title(url), media_items=media_items)

    def _build_base_command(self) -> List[str]:
        if shutil.which(self.ytdlp_binary):
            return [self.ytdlp_binary]
        if importlib.util.find_spec("yt_dlp"):
            return [sys.executable, "-m", "yt_dlp"]
        raise YouTubeDownloadError("yt-dlp is not installed in this environment")

    def _fetch_title(self, url: str) -> str:
        cmd = self._build_base_command()
        cmd.extend(["--no-warnings", "--skip-download", "--print", "%(title)s", url])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        if result.returncode != 0:
            return ""
        title = (result.stdout or "").strip().splitlines()
        return title[0].strip() if title else ""

    @classmethod
    def _extract_video_id(cls, url: str) -> str:
        match = cls.SHORTS_URL_PATTERN.search(url.strip())
        if not match:
            return "unknown"
        return match.group("video_id")

    @staticmethod
    def _collect_files(output_dir: Path, prefix: str) -> Sequence[Path]:
        return [
            file_path
            for file_path in sorted(output_dir.glob(f"{prefix}_*"))
            if file_path.is_file()
            and file_path.suffix.lower() not in {".part", ".ytdl"}
            and file_path.suffix.lower() in YouTubeShortsDownloader.MEDIA_EXTENSIONS
            and file_path.stat().st_size > 0
        ]

    @staticmethod
    def _infer_media_type(file_path: Path) -> Literal["video", "photo"]:
        if file_path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4a"}:
            return "video"
        return "photo"
