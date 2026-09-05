"""Photo-aware Twitter extractor, run in the downloader's bounded subprocess."""

from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL, parse_options
from yt_dlp.extractor.twitter import TwitterIE
from yt_dlp.utils import DownloadError, ExtractorError, update_url_query


class TwitterMediaIE(TwitterIE):
    """Keep tweet photos while delegating video extraction to pinned yt-dlp."""

    IE_NAME = "twitter:media"

    def _extract_status(self, twid):
        # Upstream video extraction asks for the same metadata again.
        if getattr(self, "_status_id", None) != twid:
            self._status = super()._extract_status(twid)
            self._status_id = twid
        return self._status

    def _real_extract(self, url):
        twid = self._match_valid_url(url).group("id")
        status = self._extract_status(twid)
        # Syndication's extended_entities also includes quoted-tweet media.
        media = status.get("mediaDetails")
        if media is None:
            media = (status.get("extended_entities") or {}).get("media", [])
        if not any(item.get("type") == "photo" for item in media):
            return super()._real_extract(url)

        title = status.get("full_text") or status.get("text") or ""
        videos = iter(())
        if any(item.get("type") != "photo" for item in media):
            # Ignore /photo/N or /video/N selectors when downloading the whole post.
            result = super()._real_extract(f"https://x.com/i/status/{twid}")
            videos = iter(result["entries"] if "entries" in result else [result])
        entries = []
        for index, item in enumerate(media, start=1):
            if item.get("type") != "photo":
                entry = next(videos, None)
                if entry is None:
                    raise ExtractorError("Twitter/X returned incomplete video metadata")
                entries.append(entry)
                continue
            image_url = item.get("media_url_https") or item.get("media_url") or ""
            parsed = urlsplit(image_url)
            if parsed.scheme != "https" or parsed.hostname != "pbs.twimg.com":
                raise ExtractorError("Twitter/X returned an invalid photo URL")
            extension = parse_qs(parsed.query).get("format", [""])[0]
            extension = extension or parsed.path.rsplit(".", 1)[-1]
            if extension not in {"jpg", "jpeg", "png", "webp"}:
                raise ExtractorError("Twitter/X returned an unsupported photo format")
            entries.append(
                {
                    "id": f"{twid}-{index}",
                    "title": title,
                    "url": update_url_query(image_url, {"name": "orig"}),
                    "ext": extension,
                }
            )
        return self.playlist_result(entries, twid, title)


def main() -> int:
    options = parse_options()
    with YoutubeDL(options.ydl_opts, auto_init=False) as ydl:
        ydl.add_info_extractor(TwitterMediaIE(ydl))
        ydl.add_default_info_extractors()
        try:
            return ydl.download(options.urls)
        except DownloadError:
            return 1


if __name__ == "__main__":
    sys.exit(main())
