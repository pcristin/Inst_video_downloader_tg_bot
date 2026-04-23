"""Supported link extraction and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal
from urllib.parse import urlparse


Provider = Literal["instagram", "twitter", "youtube_shorts"]


@dataclass(frozen=True)
class ParsedRequestLink:
    """Normalized supported link extracted from a message."""

    original_url: str
    normalized_url: str
    provider: Provider
    provider_label: str


class RequestParser:
    """Extract and normalize supported provider links from text."""

    URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
    INSTAGRAM_PATTERN = re.compile(
        r"^/(?:(?:p|reel|reels|tv|share(?:/(?:p|reel))?)/[^/?#]+|stories/[^/]+/\d+|[^/]+/(?:p|reel)/[^/?#]+)$",
        re.IGNORECASE,
    )
    TWITTER_STATUS_PATTERN = re.compile(
        r"^/[^/]+/status/\d+$",
        re.IGNORECASE,
    )
    YOUTUBE_SHORTS_PATTERN = re.compile(
        r"^/shorts/(?P<video_id>[A-Za-z0-9_-]{6,})$",
        re.IGNORECASE,
    )

    INSTAGRAM_HOSTS = {
        "instagram.com",
        "www.instagram.com",
        "ddinstagram.com",
        "d.ddinstagram.com",
        "g.ddinstagram.com",
    }
    TWITTER_HOSTS = {
        "twitter.com",
        "www.twitter.com",
        "x.com",
        "www.x.com",
        "m.twitter.com",
        "mobile.twitter.com",
    }
    YOUTUBE_HOSTS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
    }

    @classmethod
    def extract_supported_links(cls, text: str, limit: int) -> List[ParsedRequestLink]:
        """Return normalized supported links found in text."""
        seen: set[tuple[str, str]] = set()
        links: List[ParsedRequestLink] = []
        for match in cls.URL_PATTERN.finditer(text):
            candidate = cls._strip_url(match.group(0))
            parsed = cls._parse_supported_url(candidate)
            if not parsed:
                continue
            dedupe_key = (parsed.provider, parsed.normalized_url)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            links.append(parsed)
            if len(links) >= limit:
                break
        return links

    @staticmethod
    def _strip_url(url: str) -> str:
        return url.rstrip(".,!?)]}\"'")

    @classmethod
    def _parse_supported_url(cls, url: str) -> ParsedRequestLink | None:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").rstrip("/")
        if not path:
            return None

        if host in cls.INSTAGRAM_HOSTS and cls.INSTAGRAM_PATTERN.match(path):
            normalized = cls._normalize_instagram_url(path)
            return ParsedRequestLink(
                original_url=url,
                normalized_url=normalized,
                provider="instagram",
                provider_label="Instagram",
            )

        if host in cls.TWITTER_HOSTS and cls.TWITTER_STATUS_PATTERN.match(path):
            normalized = cls._normalize_twitter_url(path)
            return ParsedRequestLink(
                original_url=url,
                normalized_url=normalized,
                provider="twitter",
                provider_label="Twitter/X",
            )

        if host in cls.YOUTUBE_HOSTS:
            shorts_match = cls.YOUTUBE_SHORTS_PATTERN.match(path)
            if shorts_match:
                normalized = cls._normalize_youtube_shorts_url(shorts_match.group("video_id"))
                return ParsedRequestLink(
                    original_url=url,
                    normalized_url=normalized,
                    provider="youtube_shorts",
                    provider_label="YouTube Shorts",
                )

        return None

    @staticmethod
    def _normalize_instagram_url(path: str) -> str:
        normalized_path = path.rstrip("/")
        if normalized_path.startswith("/share/"):
            return f"https://www.instagram.com{normalized_path}/"
        if normalized_path.startswith("/stories/"):
            return f"https://www.instagram.com{normalized_path}/"
        return f"https://www.instagram.com{normalized_path}/"

    @staticmethod
    def _normalize_twitter_url(path: str) -> str:
        return f"https://x.com{path.rstrip('/')}"

    @staticmethod
    def _normalize_youtube_shorts_url(video_id: str) -> str:
        return f"https://www.youtube.com/shorts/{video_id}"
