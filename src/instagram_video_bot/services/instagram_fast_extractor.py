"""Fast Instagram media extractor inspired by multi-endpoint web/mobile strategy.

This module intentionally reimplements endpoint usage patterns in clean-room form.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

_MEDIA_TYPE = Literal["video", "photo"]


class InstagramFastExtractorError(Exception):
    """Raised when the fast extractor cannot produce downloadable media."""


@dataclass
class ExtractedMedia:
    """Resolved direct media URL and metadata before local download."""

    url: str
    media_type: _MEDIA_TYPE
    duration: Optional[float] = None


@dataclass
class DownloadedMedia:
    """Locally downloaded media item."""

    file_path: Path
    media_type: _MEDIA_TYPE
    duration: Optional[float] = None


@dataclass
class FastExtractorDownloadResult:
    """Final result from fast extractor."""

    shortcode: str
    caption: str
    media_items: List[DownloadedMedia]


@dataclass
class ParsedInstagramURL:
    """Normalized URL parse result for supported Instagram routes."""

    canonical_url: str
    route: Literal["post", "share", "story"]
    shortcode: Optional[str] = None
    share_id: Optional[str] = None
    username: Optional[str] = None
    story_id: Optional[str] = None


class InstagramFastExtractor:
    """Fast extractor that tries multiple public Instagram endpoints."""

    ALT_DOMAINS = {"ddinstagram.com", "d.ddinstagram.com", "g.ddinstagram.com"}
    MOBILE_USER_AGENT = (
        "Instagram 275.0.0.27.98 Android (33/13; 280dpi; 720x1423; "
        "Xiaomi; Redmi 7; onclite; qcom; en_US; 458229237)"
    )
    WEB_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    _RE_STORY = re.compile(r"^/stories/(?P<username>[^/]+)/(?P<story_id>\d+)")
    _RE_SHARE = re.compile(r"^/share(?:/(?:p|reel))?/(?P<share_id>[^/?#]+)")
    _RE_POST = re.compile(r"^/(?:(?:p|reel|reels|tv)/(?P<shortcode>[^/?#]+)|[^/]+/(?:p|reel)/(?P<user_shortcode>[^/?#]+))")

    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout_connect: int = 10,
        timeout_read: int = 45,
    ) -> None:
        self.proxy = proxy
        self.timeout = (timeout_connect, timeout_read)
        self.session = requests.Session()

    def is_story_url(self, url: str) -> bool:
        """Return whether URL points to a story route."""
        try:
            parsed = self.parse_url(url)
            return parsed.route == "story"
        except InstagramFastExtractorError:
            return False

    def parse_url(self, url: str) -> ParsedInstagramURL:
        """Normalize and parse supported Instagram URL routes."""
        canonical = self._normalize_url(url)
        parsed = urlparse(canonical)
        path = parsed.path.rstrip("/")

        story_match = self._RE_STORY.match(path)
        if story_match:
            return ParsedInstagramURL(
                canonical_url=canonical,
                route="story",
                username=story_match.group("username"),
                story_id=story_match.group("story_id"),
            )

        share_match = self._RE_SHARE.match(path)
        if share_match:
            return ParsedInstagramURL(
                canonical_url=canonical,
                route="share",
                share_id=share_match.group("share_id"),
            )

        post_match = self._RE_POST.match(path)
        if post_match:
            shortcode = post_match.group("shortcode") or post_match.group("user_shortcode")
            return ParsedInstagramURL(
                canonical_url=canonical,
                route="post",
                shortcode=shortcode,
            )

        raise InstagramFastExtractorError("Unsupported Instagram URL route")

    def extract_and_download(self, url: str, output_dir: Path) -> FastExtractorDownloadResult:
        """Extract media links from URL and download all media files."""
        parsed = self.parse_url(url)
        if parsed.route == "story":
            raise InstagramFastExtractorError("story_unsupported_in_fast_path")

        if parsed.route == "share":
            resolved = self.resolve_share_url(parsed.canonical_url)
            parsed = self.parse_url(resolved)
            if parsed.route != "post" or not parsed.shortcode:
                raise InstagramFastExtractorError("Share URL did not resolve to a post")

        if not parsed.shortcode:
            raise InstagramFastExtractorError("Missing post shortcode")

        caption, media = self._extract_post(parsed.shortcode, parsed.canonical_url)
        if not media:
            raise InstagramFastExtractorError("No media items extracted")

        downloaded = self._download_media_items(
            shortcode=parsed.shortcode,
            media_items=media,
            output_dir=output_dir,
        )
        if not downloaded:
            raise InstagramFastExtractorError("No media files downloaded")

        return FastExtractorDownloadResult(
            shortcode=parsed.shortcode,
            caption=caption,
            media_items=downloaded,
        )

    def resolve_share_url(self, share_url: str) -> str:
        """Resolve /share URLs to canonical post/reel URLs."""
        response = self._request_raw(
            method="GET",
            url=share_url,
            headers={"User-Agent": "curl/7.88.1"},
            allow_redirects=True,
        )
        if response is None:
            raise InstagramFastExtractorError("Failed to resolve share URL")

        candidate = self._normalize_url(response.url)
        try:
            parsed = self.parse_url(candidate)
            if parsed.route == "post":
                return candidate
        except InstagramFastExtractorError:
            pass

        match = re.search(r"https://www\.instagram\.com/(?:p|reel|reels|tv)/([^/?#\"'<>]+)/?", response.text or "")
        if match:
            shortcode = match.group(1)
            return f"https://www.instagram.com/p/{shortcode}/"

        raise InstagramFastExtractorError("Could not resolve share URL")

    def _extract_post(self, shortcode: str, canonical_url: str) -> Tuple[str, List[ExtractedMedia]]:
        """Extract media for a post/reel/tv using endpoint fallback graph."""
        media_id = self._get_media_id(canonical_url)

        if media_id:
            mobile_item = self._request_mobile_media_info(media_id)
            caption, items = self._parse_mobile_item(mobile_item)
            if items:
                return caption, items

        embed_data = self._request_embed_data(shortcode)
        caption, items = self._parse_embed_or_graphql_data(embed_data)
        if items:
            return caption, items

        gql_data = self._request_graphql_data(shortcode)
        caption, items = self._parse_embed_or_graphql_data(gql_data)
        if items:
            return caption, items

        raise InstagramFastExtractorError("Failed to extract media from all fast endpoints")

    def _get_media_id(self, canonical_url: str) -> Optional[str]:
        """Resolve media id with mobile oEmbed endpoint."""
        oembed_url = "https://i.instagram.com/api/v1/oembed/?url=" + quote(canonical_url, safe=":/")
        data = self._request_json("GET", oembed_url, headers=self._mobile_headers())
        media_id = data.get("media_id") if isinstance(data, dict) else None
        if isinstance(media_id, str) and media_id:
            return media_id.split("_")[0]
        return None

    def _request_mobile_media_info(self, media_id: str) -> Dict[str, Any]:
        """Request mobile API media info payload."""
        url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
        data = self._request_json("GET", url, headers=self._mobile_headers())
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list) and items:
                item = items[0]
                if isinstance(item, dict):
                    return item
        return {}

    def _request_embed_data(self, shortcode: str) -> Dict[str, Any]:
        """Request embed page and parse context JSON."""
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
        response = self._request_raw("GET", embed_url, headers=self._web_headers())
        if response is None or not response.text:
            return {}

        payload = self._extract_context_json(response.text)
        if isinstance(payload, dict):
            return payload
        return {}

    def _request_graphql_data(self, shortcode: str) -> Dict[str, Any]:
        """Fallback to web GraphQL request for media extraction."""
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        post_response = self._request_raw("GET", post_url, headers=self._web_headers())
        if post_response is None or not post_response.text:
            return {}

        html = post_response.text
        app_id = self._extract_html_value(html, [r'"appId":"(\d+)"']) or "936619743392459"
        lsd_token = self._extract_html_value(html, [r'"LSD",\[\],\{"token":"(.*?)"', r'"token":"(.*?)","__bbox"'])
        csrf_token = self._extract_html_value(html, [r'"csrf_token":"(.*?)"'])

        headers = self._web_headers()
        headers.update(
            {
                "X-FB-Friendly-Name": "PolarisPostActionLoadPostQueryQuery",
                "X-IG-App-ID": app_id,
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )
        if lsd_token:
            headers["X-FB-LSD"] = lsd_token
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token

        payload = {
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "PolarisPostActionLoadPostQueryQuery",
            "variables": json.dumps(
                {
                    "shortcode": shortcode,
                    "fetch_tagged_user_count": None,
                    "hoisted_comment_id": None,
                    "hoisted_reply_id": None,
                }
            ),
            "server_timestamps": "true",
            "doc_id": "8845758582119845",
        }

        data = self._request_json(
            method="POST",
            url="https://www.instagram.com/graphql/query",
            headers=headers,
            data=payload,
        )
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return {}

    def _parse_mobile_item(self, item: Dict[str, Any]) -> Tuple[str, List[ExtractedMedia]]:
        """Parse mobile API payload into normalized media list."""
        if not item:
            return "", []

        caption_text = ""
        caption = item.get("caption")
        if isinstance(caption, dict):
            caption_text = str(caption.get("text") or "")

        carousel = item.get("carousel_media")
        if isinstance(carousel, list) and carousel:
            out: List[ExtractedMedia] = []
            for entry in carousel:
                parsed = self._parse_mobile_media_node(entry)
                if parsed:
                    out.append(parsed)
            return caption_text, out

        single = self._parse_mobile_media_node(item)
        if single:
            return caption_text, [single]
        return caption_text, []

    def _parse_mobile_media_node(self, node: Dict[str, Any]) -> Optional[ExtractedMedia]:
        """Parse a single mobile media node."""
        video_versions = node.get("video_versions")
        if isinstance(video_versions, list) and video_versions:
            best = self._pick_highest_resolution_video(video_versions)
            if best:
                return ExtractedMedia(
                    url=best,
                    media_type="video",
                    duration=self._safe_float(node.get("video_duration")),
                )

        image_versions = node.get("image_versions2")
        if isinstance(image_versions, dict):
            candidates = image_versions.get("candidates")
            if isinstance(candidates, list) and candidates:
                first = candidates[0]
                if isinstance(first, dict) and first.get("url"):
                    return ExtractedMedia(
                        url=str(first["url"]),
                        media_type="photo",
                        duration=None,
                    )

        return None

    def _parse_embed_or_graphql_data(self, data: Dict[str, Any]) -> Tuple[str, List[ExtractedMedia]]:
        """Parse fallback embed/graphql data into normalized media list."""
        if not data:
            return "", []

        node = (
            data.get("shortcode_media")
            or data.get("xdt_shortcode_media")
            or (data.get("graphql") or {}).get("shortcode_media")
            or ((data.get("gql_data") or {}).get("shortcode_media"))
            or ((data.get("gql_data") or {}).get("xdt_shortcode_media"))
        )
        if not isinstance(node, dict):
            return "", []

        caption_text = ""
        edge_caption = node.get("edge_media_to_caption")
        if isinstance(edge_caption, dict):
            edges = edge_caption.get("edges")
            if isinstance(edges, list) and edges:
                first = edges[0]
                if isinstance(first, dict):
                    nested = first.get("node")
                    if isinstance(nested, dict):
                        caption_text = str(nested.get("text") or "")

        sidecar = node.get("edge_sidecar_to_children")
        if isinstance(sidecar, dict):
            edges = sidecar.get("edges")
            if isinstance(edges, list) and edges:
                out: List[ExtractedMedia] = []
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    media_node = edge.get("node")
                    if not isinstance(media_node, dict):
                        continue
                    if media_node.get("is_video") and media_node.get("video_url"):
                        out.append(
                            ExtractedMedia(
                                url=str(media_node["video_url"]),
                                media_type="video",
                                duration=self._safe_float(media_node.get("video_duration")),
                            )
                        )
                    elif media_node.get("display_url"):
                        out.append(
                            ExtractedMedia(
                                url=str(media_node["display_url"]),
                                media_type="photo",
                                duration=None,
                            )
                        )
                if out:
                    return caption_text, out

        if node.get("video_url"):
            return (
                caption_text,
                [
                    ExtractedMedia(
                        url=str(node["video_url"]),
                        media_type="video",
                        duration=self._safe_float(node.get("video_duration")),
                    )
                ],
            )

        if node.get("display_url"):
            return (
                caption_text,
                [
                    ExtractedMedia(
                        url=str(node["display_url"]),
                        media_type="photo",
                    )
                ],
            )

        return caption_text, []

    def _download_media_items(
        self,
        shortcode: str,
        media_items: List[ExtractedMedia],
        output_dir: Path,
    ) -> List[DownloadedMedia]:
        """Download extracted media URLs to local files."""
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded: List[DownloadedMedia] = []

        for index, item in enumerate(media_items, start=1):
            extension = self._guess_extension(item)
            out_path = output_dir / f"instagram_{shortcode}_{index}.{extension}"

            response = self._request_raw(
                method="GET",
                url=item.url,
                headers=self._download_headers(),
                stream=True,
            )
            if response is None:
                raise InstagramFastExtractorError(f"Failed to download media item {index}")

            content_type = (response.headers.get("content-type") or "").lower()
            if item.media_type == "video" and content_type and "video" not in content_type:
                logger.warning("Fast media content-type mismatch for video: %s", content_type)
            if item.media_type == "photo" and content_type and "image" not in content_type:
                logger.warning("Fast media content-type mismatch for photo: %s", content_type)

            total_written = 0
            with open(out_path, "wb") as file_handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    file_handle.write(chunk)
                    total_written += len(chunk)

            if total_written <= 0:
                out_path.unlink(missing_ok=True)
                raise InstagramFastExtractorError(f"Downloaded empty media item {index}")

            downloaded.append(
                DownloadedMedia(
                    file_path=out_path,
                    media_type=item.media_type,
                    duration=item.duration,
                )
            )

        return downloaded

    def _normalize_url(self, url: str) -> str:
        """Normalize incoming URLs and alias ddinstagram hostnames."""
        cleaned = url.strip()
        if not cleaned:
            raise InstagramFastExtractorError("Empty URL")
        if not cleaned.startswith(("http://", "https://")):
            cleaned = "https://" + cleaned

        parsed = urlparse(cleaned)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if not host:
            raise InstagramFastExtractorError("Invalid URL host")

        if host in self.ALT_DOMAINS:
            host = "www.instagram.com"

        if host not in {"instagram.com", "www.instagram.com"}:
            raise InstagramFastExtractorError("Unsupported Instagram domain")

        path = parsed.path or "/"
        if not path.startswith("/"):
            path = "/" + path

        if path != "/" and not path.endswith("/"):
            path = path + "/"

        return f"https://www.instagram.com{path}"

    def _extract_context_json(self, html: str) -> Dict[str, Any]:
        """Extract context JSON blob from embed HTML."""
        patterns = [
            r'"contextJSON":"(.*?)"',
            r'"contextJSON":\s*"(.*?)"',
        ]

        for pattern in patterns:
            match = re.search(pattern, html)
            if not match:
                continue
            encoded = match.group(1)
            decoded = encoded.encode("utf-8").decode("unicode_escape")
            decoded = decoded.replace("\\/", "/")
            try:
                payload = json.loads(decoded)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue

        return {}

    @staticmethod
    def _extract_html_value(html: str, patterns: List[str]) -> Optional[str]:
        """Extract first regex capture from HTML using candidate patterns."""
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _pick_highest_resolution_video(video_versions: List[Dict[str, Any]]) -> Optional[str]:
        """Pick best video candidate by pixel area."""
        best_url: Optional[str] = None
        best_area = -1
        for item in video_versions:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            area = width * height
            if area > best_area:
                best_area = area
                best_url = str(item["url"])
        return best_url

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Convert value to float if possible."""
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _guess_extension(self, item: ExtractedMedia) -> str:
        """Guess local file extension for media item."""
        if item.media_type == "video":
            return "mp4"

        path = urlparse(item.url).path.lower()
        if path.endswith(".png"):
            return "png"
        if path.endswith(".webp"):
            return "webp"
        return "jpg"

    def _mobile_headers(self) -> Dict[str, str]:
        """Headers for mobile endpoint calls."""
        return {
            "User-Agent": self.MOBILE_USER_AGENT,
            "Accept-Language": "en-US",
            "X-IG-App-ID": "936619743392459",
            "X-FB-HTTP-Engine": "Liger",
            "X-FB-Client-IP": "True",
            "X-FB-Server-Cluster": "True",
            "Content-Length": "0",
        }

    def _web_headers(self) -> Dict[str, str]:
        """Headers for web endpoint calls."""
        return {
            "User-Agent": self.WEB_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
            "Sec-GPC": "1",
        }

    def _download_headers(self) -> Dict[str, str]:
        """Headers for media binary downloads."""
        return {
            "User-Agent": self.WEB_USER_AGENT,
            "Accept": "*/*",
            "Referer": "https://www.instagram.com/",
            "Origin": "https://www.instagram.com",
        }

    def _request_json(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run HTTP request and parse JSON with tolerant failure behavior."""
        response = self._request_raw(
            method=method,
            url=url,
            headers=headers,
            data=data,
        )
        if response is None:
            return {}

        try:
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            return {}
        except Exception:
            return {}

    def _request_raw(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> Optional[requests.Response]:
        """Run HTTP request through shared session and proxy settings."""
        proxies = None
        if self.proxy:
            proxies = {"http": self.proxy, "https": self.proxy}

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                data=data,
                timeout=self.timeout,
                stream=stream,
                allow_redirects=allow_redirects,
                proxies=proxies,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            logger.debug("Fast extractor request failed (%s %s): %s", method, url, exc)
            return None

    @staticmethod
    def random_token(length: int = 16) -> str:
        """Generate small random token (used by callers when needed)."""
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(random.choice(alphabet) for _ in range(length))
