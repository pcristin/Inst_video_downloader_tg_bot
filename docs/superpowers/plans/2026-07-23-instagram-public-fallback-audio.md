# Instagram Public Fallback Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve available audio when the public Instagram yt-dlp fallback exposes separate DASH video and audio tracks, and prevent old silent inline cache entries from being reused.

**Architecture:** Represent yt-dlp source selection as a typed visual-plus-optional-audio value. Download and stream-copy separate tracks into one validated MP4 before the existing iOS normalizer runs; return no public result on merge or validation failure so the account path remains the fallback. Add normalizer audio-loss defense and version only the Instagram inline-cache namespace.

**Tech Stack:** Python 3.11, pytest, requests, yt-dlp metadata, ffmpeg/ffprobe, python-telegram-bot, SQLite-backed `StateStore`.

## Global Constraints

- Preserve the highest-quality video candidate selected by dimensions, filesize, and bitrate.
- Do not re-encode during public acquisition; use ffmpeg stream copy and leave iOS video encoding to `media_normalizer.py`.
- A separately advertised audio track is mandatory: merge or validation failure must return no public video entry.
- Photo, carousel, timeout, and authenticated account-fallback behavior must remain unchanged.
- Do not change database schemas or delete historical cache rows.
- Never log media source URLs, Telegram file IDs, cookies, proxies, or credentials.
- Use test-driven development: every production behavior change must be preceded by a focused test that fails for the expected reason.

---

## File Structure

- Modify `src/instagram_video_bot/services/instagram_client.py`: typed yt-dlp source selection, track downloads, ffmpeg merge, ffprobe validation, cleanup, and fallback result behavior.
- Modify `tests/test_instagram_client_metadata.py`: pure selector tests and mocked public recovery/merge failure tests.
- Modify `src/instagram_video_bot/services/media_normalizer.py`: reject normalization output that loses source audio.
- Modify `tests/test_media_normalizer.py`: regression coverage for source-audio preservation.
- Modify `src/instagram_video_bot/services/telegram_bot.py`: version Instagram inline media cache keys.
- Modify `tests/test_telegram_bot_true_inline.py`: cache-key behavior and old-cache bypass coverage.
- Create `tests/test_instagram_public_media_ffmpeg.py`: deterministic real-ffmpeg merge verification, skipped only when ffmpeg or ffprobe is unavailable.

---

### Task 1: Select the Best Video with Its Separate Audio Track

**Files:**
- Modify: `src/instagram_video_bot/services/instagram_client.py:1-45,187-295`
- Test: `tests/test_instagram_client_metadata.py:1-15,215-306`

**Interfaces:**
- Produces: `PublicYtdlpSource(visual_url: str, extension: str, audio_url: str | None = None, audio_extension: str | None = None)`.
- Produces: `InstagramClient._public_ytdlp_source(entry: dict) -> PublicYtdlpSource | None`.
- Consumes: yt-dlp `formats` dictionaries with `url`, `ext`, `vcodec`, `acodec`, dimensions, filesize, `tbr`, and `abr`.

- [ ] **Step 1: Write the failing selector test**

Add `PublicYtdlpSource` to the import from `instagram_client`, then add this test to `tests/test_instagram_client_metadata.py`:

```python
def test_public_ytdlp_source_pairs_highest_quality_video_with_best_audio():
    entry = {
        "formats": [
            {
                "url": "https://cdn.example/video-720.mp4",
                "ext": "mp4",
                "vcodec": "vp9",
                "acodec": "none",
                "width": 720,
                "height": 1280,
                "tbr": 900,
            },
            {
                "url": "https://cdn.example/video-1080.mp4",
                "ext": "mp4",
                "vcodec": "vp9",
                "acodec": "none",
                "width": 1080,
                "height": 1920,
                "tbr": 1500,
            },
            {
                "url": "https://cdn.example/audio-low.m4a",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "abr": 48,
            },
            {
                "url": "https://cdn.example/audio-high.m4a",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "abr": 96,
            },
        ]
    }

    assert InstagramClient._public_ytdlp_source(entry) == PublicYtdlpSource(
        visual_url="https://cdn.example/video-1080.mp4",
        extension="mp4",
        audio_url="https://cdn.example/audio-high.m4a",
        audio_extension="m4a",
    )
```

- [ ] **Step 2: Run the selector test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py::test_public_ytdlp_source_pairs_highest_quality_video_with_best_audio
```

Expected: collection fails because `PublicYtdlpSource` does not exist. After adding only the importable dataclass shell, the assertion must fail because the current selector returns a two-item tuple and does not select audio.

- [ ] **Step 3: Add the typed selection result and audio-aware selector**

Add after `InstagramDownloadResult`:

```python
@dataclass(frozen=True)
class PublicYtdlpSource:
    """Selected public visual source and its optional separate audio track."""

    visual_url: str
    extension: str
    audio_url: str | None = None
    audio_extension: str | None = None
```

Replace `_public_ytdlp_source` with:

```python
@staticmethod
def _public_ytdlp_source(entry: dict) -> Optional[PublicYtdlpSource]:
    """Select the best visual source and pair separate audio when required."""
    formats = [
        media_format
        for media_format in entry.get("formats") or []
        if isinstance(media_format, dict) and media_format.get("url")
    ]
    video_formats = [
        media_format
        for media_format in formats
        if media_format.get("vcodec") != "none"
    ]
    if video_formats:
        selected = max(
            video_formats,
            key=lambda media_format: (
                media_format.get("height") or 0,
                media_format.get("width") or 0,
                media_format.get("filesize") or 0,
                media_format.get("tbr") or 0,
            ),
        )
        audio_url = None
        audio_extension = None
        if selected.get("acodec") == "none":
            audio_formats = [
                media_format
                for media_format in formats
                if media_format.get("vcodec") == "none"
                and media_format.get("acodec") not in (None, "none")
            ]
            if audio_formats:
                selected_audio = max(
                    audio_formats,
                    key=lambda media_format: (
                        media_format.get("abr") or media_format.get("tbr") or 0,
                        media_format.get("filesize") or 0,
                    ),
                )
                audio_url = str(selected_audio["url"])
                audio_extension = selected_audio.get("ext") or "m4a"
    else:
        thumbnails = [
            thumbnail
            for thumbnail in entry.get("thumbnails") or []
            if isinstance(thumbnail, dict) and thumbnail.get("url")
        ]
        if not thumbnails:
            return None
        selected = max(
            thumbnails,
            key=lambda thumbnail: (
                (thumbnail.get("width") or 0) * (thumbnail.get("height") or 0),
                thumbnail.get("width") or 0,
                thumbnail.get("height") or 0,
            ),
        )
        audio_url = None
        audio_extension = None

    visual_url = str(selected["url"])
    extension = selected.get("ext") or Path(urlparse(visual_url).path).suffix.lstrip(".")
    return PublicYtdlpSource(
        visual_url=visual_url,
        extension=extension or "bin",
        audio_url=audio_url,
        audio_extension=audio_extension,
    )
```

In `download_public_ytdlp_media`, change only the current tuple unpacking so existing direct downloads continue working during this task:

```python
source_url = source.visual_url
extension = source.extension
```

- [ ] **Step 4: Run focused selector and existing public metadata tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py::test_public_ytdlp_source_pairs_highest_quality_video_with_best_audio tests/test_instagram_client_metadata.py::test_public_ytdlp_media_downloads_video_and_thumbnail_entries
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the selection change**

```bash
git add src/instagram_video_bot/services/instagram_client.py tests/test_instagram_client_metadata.py
git commit -m "fix: pair public Instagram video and audio sources"
```

---

### Task 2: Download, Merge, Validate, and Clean Separate Tracks

**Files:**
- Modify: `src/instagram_video_bot/services/instagram_client.py:1-12,187-247`
- Test: `tests/test_instagram_client_metadata.py`

**Interfaces:**
- Consumes: `PublicYtdlpSource` from Task 1.
- Produces: `InstagramClient._download_public_source(source: PublicYtdlpSource, output_path: Path) -> bool`.
- Produces: `InstagramClient._merge_public_tracks(video_path: Path, audio_path: Path, output_path: Path) -> bool`.
- Produces: `InstagramClient._public_output_has_av_streams(path: Path) -> bool`.

- [ ] **Step 1: Write a failing public recovery merge test**

Add `json` and `subprocess` production imports only after the test has been added and observed failing. Add this test to `tests/test_instagram_client_metadata.py`:

```python
def test_public_ytdlp_media_merges_separate_audio_before_returning(monkeypatch, tmp_path):
    class _YoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "Reel with separate tracks",
                "formats": [
                    {
                        "url": "https://cdn.example/video.mp4",
                        "ext": "mp4",
                        "vcodec": "vp9",
                        "acodec": "none",
                        "width": 1080,
                        "height": 1920,
                    },
                    {
                        "url": "https://cdn.example/audio.m4a",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "aac",
                        "abr": 96,
                    },
                ],
            }

    class _Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    requested_urls = []
    ffmpeg_commands = []

    def _get(url, timeout):
        requested_urls.append((url, timeout))
        return _Response(url.encode())

    def _run(command, **_kwargs):
        ffmpeg_commands.append(command)
        Path(command[-1]).write_bytes(b"muxed-av")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YoutubeDL))
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_client.requests.get", _get
    )
    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: True),
        raising=False,
    )

    result = InstagramClient.download_public_ytdlp_media(
        "https://www.instagram.com/reel/example/", tmp_path
    )

    assert result is not None
    assert result.file_paths == [tmp_path / "public_1.mp4"]
    assert result.file_paths[0].read_bytes() == b"muxed-av"
    assert requested_urls == [
        ("https://cdn.example/video.mp4", 15.0),
        ("https://cdn.example/audio.m4a", 15.0),
    ]
    assert ffmpeg_commands[0][0:2] == ["ffmpeg", "-v"]
    assert ffmpeg_commands[0][-9:-1] == [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
    ]
    assert list(tmp_path.glob(".*.video.*")) == []
    assert list(tmp_path.glob(".*.audio.*")) == []
```

- [ ] **Step 2: Run the merge test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py::test_public_ytdlp_media_merges_separate_audio_before_returning
```

Expected: FAIL because only the visual URL is requested and no ffmpeg command runs.

- [ ] **Step 3: Implement track download and merge**

Add the import:

```python
import subprocess
```

Add these static methods to `InstagramClient`:

```python
@staticmethod
def _download_url_to_path(url: str, path: Path) -> None:
    response = requests.get(
        url,
        timeout=settings.IG_FALLBACK_YTDLP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    path.write_bytes(response.content)
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("public media download produced an empty file")

@staticmethod
def _merge_public_tracks(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> bool:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=settings.IG_FALLBACK_YTDLP_TIMEOUT_SECONDS,
        check=False,
    )
    return (
        result.returncode == 0
        and output_path.exists()
        and output_path.stat().st_size > 0
    )

@staticmethod
def _download_public_source(
    source: PublicYtdlpSource,
    output_path: Path,
) -> bool:
    if source.audio_url is None:
        InstagramClient._download_url_to_path(source.visual_url, output_path)
        return True

    video_path = output_path.with_name(
        f".{output_path.stem}.video.{source.extension}"
    )
    audio_path = output_path.with_name(
        f".{output_path.stem}.audio.{source.audio_extension or 'm4a'}"
    )
    succeeded = False
    try:
        InstagramClient._download_url_to_path(source.visual_url, video_path)
        InstagramClient._download_url_to_path(source.audio_url, audio_path)
        succeeded = InstagramClient._merge_public_tracks(
            video_path,
            audio_path,
            output_path,
        )
        return succeeded
    finally:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
        if not succeeded:
            output_path.unlink(missing_ok=True)
```

Replace the direct `requests.get` block in `download_public_ytdlp_media` with:

```python
file_path = output_dir / f"public_{index}.{source.extension}"
try:
    if InstagramClient._download_public_source(source, file_path):
        file_paths.append(file_path)
except Exception as error:
    file_path.unlink(missing_ok=True)
    logger.info(
        "Public yt-dlp media fetch or merge failed",
        extra={
            "entry_index": index,
            "error_class": error.__class__.__name__,
        },
    )
```

- [ ] **Step 4: Run merge and existing public recovery tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py -k 'public_ytdlp'
```

Expected: all selected public yt-dlp tests pass.

- [ ] **Step 5: Write a failing validation/cleanup test**

Add:

```python
def test_public_source_rejects_merged_file_without_audio(monkeypatch, tmp_path):
    source = PublicYtdlpSource(
        visual_url="https://cdn.example/video.mp4",
        extension="mp4",
        audio_url="https://cdn.example/audio.m4a",
        audio_extension="m4a",
    )
    output = tmp_path / "public_1.mp4"

    def _download(_url, path):
        path.write_bytes(b"track")

    def _merge(_video, _audio, path):
        path.write_bytes(b"video-only-output")
        return True

    monkeypatch.setattr(InstagramClient, "_download_url_to_path", staticmethod(_download))
    monkeypatch.setattr(InstagramClient, "_merge_public_tracks", staticmethod(_merge))
    monkeypatch.setattr(
        InstagramClient,
        "_public_output_has_av_streams",
        staticmethod(lambda _path: False),
    )

    assert InstagramClient._download_public_source(source, output) is False
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 6: Run the validation/cleanup test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py::test_public_source_rejects_merged_file_without_audio
```

Expected: FAIL because `_download_public_source` returns `True` and leaves the unvalidated output in place.

- [ ] **Step 7: Add ffprobe validation to the merge success condition**

Add the import:

```python
import json
```

Add this static method to `InstagramClient`:

```python
@staticmethod
def _public_output_has_av_streams(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=settings.IG_FALLBACK_YTDLP_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            return False
        payload = json.loads(result.stdout)
        stream_types = {
            stream.get("codec_type")
            for stream in payload.get("streams") or []
            if isinstance(stream, dict)
        }
        return {"video", "audio"}.issubset(stream_types)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return False
```

Change the merge assignment in `_download_public_source` to:

```python
succeeded = InstagramClient._merge_public_tracks(
    video_path,
    audio_path,
    output_path,
) and InstagramClient._public_output_has_av_streams(output_path)
```

- [ ] **Step 8: Run the validation/cleanup and public recovery tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py -k 'public_ytdlp or public_source'
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit merge and validation behavior**

```bash
git add src/instagram_video_bot/services/instagram_client.py tests/test_instagram_client_metadata.py
git commit -m "fix: merge public Instagram audio tracks"
```

---

### Task 3: Prevent the Normalizer from Losing Existing Audio

**Files:**
- Modify: `src/instagram_video_bot/services/media_normalizer.py:73-133`
- Test: `tests/test_media_normalizer.py:90-152`

**Interfaces:**
- Consumes: existing `VideoProbe.audio_codecs` tuples.
- Preserves: `_normalize_video_item(item: MediaItem) -> MediaItem` return contract.

- [ ] **Step 1: Write the failing normalizer regression test**

Add to `tests/test_media_normalizer.py`:

```python
def test_normalization_rejects_output_that_loses_source_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-with-audio")
    info = _video_info(source)

    def fake_probe(path: Path) -> VideoProbe:
        if path == source:
            return _probe(video_codec="vp9", audio_codecs=("aac",))
        return _probe(video_codec="h264", audio_codecs=())

    def fake_run(_command: list[str], output_path: Path) -> bool:
        output_path.write_bytes(b"normalized-without-audio")
        return True

    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._probe_video", fake_probe
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._decode_is_valid",
        lambda _path: True,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.media_normalizer._run_ffmpeg", fake_run
    )

    result = normalize_instagram_media(info)

    assert result is info
    assert result.file_path == source
    assert not (tmp_path / "source.ios.mp4").exists()
```

- [ ] **Step 2: Run the normalizer test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_media_normalizer.py::test_normalization_rejects_output_that_loses_source_audio
```

Expected: FAIL because the current normalizer accepts the candidate and returns `source.ios.mp4`.

- [ ] **Step 3: Add the source/output audio invariant**

Immediately after `candidate_probe = _probe_video(candidate)`, add:

```python
if source_probe.audio_codecs and not candidate_probe.audio_codecs:
    reason = "output_audio_missing"
    raise RuntimeError("normalized output lost source audio")
```

The existing exception handler removes the candidate, logs `normalization_reason=output_audio_missing`, and returns the original item.

- [ ] **Step 4: Run all normalizer tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_media_normalizer.py
```

Expected on hosts without ffmpeg: unit tests pass and real-media tests skip. Expected in the application container: all tests pass without skips caused by missing ffmpeg.

- [ ] **Step 5: Commit the invariant**

```bash
git add src/instagram_video_bot/services/media_normalizer.py tests/test_media_normalizer.py
git commit -m "fix: reject normalized video that loses audio"
```

---

### Task 4: Bypass Permanent Silent Instagram Inline Cache Entries

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py:1-70,1145-1230`
- Test: `tests/test_telegram_bot_true_inline.py:866-1142`

**Interfaces:**
- Produces: `_inline_media_cache_key(provider: str, normalized_url: str) -> str`.
- Preserves: non-Instagram cache key format `<provider>:<normalized_url>`.
- Changes: Instagram cache key format to `instagram:av2:<normalized_url>`.

- [ ] **Step 1: Write failing cache-key tests**

Import `_inline_media_cache_key` from `telegram_bot` and add:

```python
def test_inline_media_cache_key_versions_only_instagram_media():
    instagram_url = "https://www.instagram.com/reel/abc/"
    twitter_url = "https://x.com/example/status/1"

    assert _inline_media_cache_key("instagram", instagram_url) == (
        "instagram:av2:https://www.instagram.com/reel/abc/"
    )
    assert _inline_media_cache_key("twitter", twitter_url) == (
        "twitter:https://x.com/example/status/1"
    )
```

- [ ] **Step 2: Run the cache-key test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_telegram_bot_true_inline.py::test_inline_media_cache_key_versions_only_instagram_media
```

Expected: collection fails because `_inline_media_cache_key` does not exist.

- [ ] **Step 3: Implement and use the versioned key helper**

Add near the module constants in `telegram_bot.py`:

```python
_INSTAGRAM_INLINE_MEDIA_CACHE_VERSION = "av2"


def _inline_media_cache_key(provider: str, normalized_url: str) -> str:
    if provider == "instagram":
        return f"{provider}:{_INSTAGRAM_INLINE_MEDIA_CACHE_VERSION}:{normalized_url}"
    return f"{provider}:{normalized_url}"
```

Replace the inline delivery key construction with:

```python
cache_key = _inline_media_cache_key(
    str(session["provider"]),
    str(session["normalized_url"]),
)
```

Update inline tests that inspect current Instagram cache entries to call `_inline_media_cache_key("instagram", url)` instead of hardcoding `instagram:<url>`. Leave deliberately stale-cache setup using the old hardcoded key unchanged.

- [ ] **Step 4: Add a functional old-cache bypass test**

Add:

```python
@pytest.mark.asyncio
async def test_inline_delivery_bypasses_pre_audio-fix_instagram_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -100)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    url = "https://www.instagram.com/reel/abc/"
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="s1",
        user_id=1001,
        original_url=url,
        normalized_url=url,
        provider="instagram",
        provider_label="Instagram",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.attach_inline_message("s1", inline_message_id="inline-msg")
    store.save_inline_cached_media(
        cache_key=f"instagram:{url}",
        provider="instagram",
        normalized_url=url,
        media_items=[{"media_type": "video", "file_id": "silent-file-id"}],
    )
    download_calls = []

    class FakeDownloader:
        async def download_video(self, original_url, target_dir):
            download_calls.append(original_url)
            media_file = target_dir / "video-with-audio.mp4"
            media_file.write_bytes(b"video-with-audio")
            return VideoInfo(
                file_path=media_file,
                title="Title",
                media_items=[MediaItem(file_path=media_file, media_type="video")],
                primary_media_type="video",
            )

    async def fake_upload(*_args, **_kwargs):
        return InlineCachedMediaItem(
            media_type="video",
            file_id="audio-fixed-file-id",
        )

    edited = []

    async def edit_message_media(**kwargs):
        edited.append(kwargs)

    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.VideoDownloader", FakeDownloader
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.telegram_bot.upload_first_media_to_storage",
        fake_upload,
    )

    bot = TelegramBot(state_store=store)
    await bot._deliver_inline_session(
        SimpleNamespace(bot=SimpleNamespace(edit_message_media=edit_message_media)),
        session_token="s1",
        one_time_payment_id=None,
    )

    assert download_calls == [url]
    corrected = store.get_inline_cached_media(_inline_media_cache_key("instagram", url))
    assert corrected["media_items"][0]["file_id"] == "audio-fixed-file-id"
    assert edited
```

- [ ] **Step 5: Run inline cache and delivery tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_telegram_bot_true_inline.py -k 'inline_delivery or inline_media_cache_key'
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit cache invalidation behavior**

```bash
git add src/instagram_video_bot/services/telegram_bot.py tests/test_telegram_bot_true_inline.py
git commit -m "fix: invalidate silent Instagram inline cache"
```

---

### Task 5: Add Real-FFmpeg Regression Coverage and Verify the Complete Fix

**Files:**
- Create: `tests/test_instagram_public_media_ffmpeg.py`
- Verify: all modified source and test files from Tasks 1-4.

**Interfaces:**
- Consumes: `InstagramClient._merge_public_tracks` and `_public_output_has_av_streams` from Task 2.
- Produces: deterministic proof that the production merge helper creates a file with both video and audio streams.

- [ ] **Step 1: Add the real-ffmpeg integration test**

Create `tests/test_instagram_public_media_ffmpeg.py`:

```python
import shutil
import subprocess

import pytest

from src.instagram_video_bot.services.instagram_client import InstagramClient


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg tools are required",
)
def test_real_ffmpeg_public_track_merge_contains_video_and_audio(tmp_path):
    video_path = tmp_path / "video-only.mp4"
    audio_path = tmp_path / "audio-only.m4a"
    output_path = tmp_path / "merged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=12:duration=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-vn",
            "-c:a",
            "aac",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    assert InstagramClient._merge_public_tracks(
        video_path,
        audio_path,
        output_path,
    )
    assert InstagramClient._public_output_has_av_streams(output_path)
```

- [ ] **Step 2: Verify the test detects the pre-fix behavior**

Before keeping the production helper call, temporarily replace the merge assertion with a copy of `video_path` to `output_path` and run:

```bash
docker compose exec -T instagram-video-bot uv run --no-sync pytest -q tests/test_instagram_public_media_ffmpeg.py
```

Expected: FAIL at `_public_output_has_av_streams(output_path)`. Restore the production helper call immediately after observing the failure.

- [ ] **Step 3: Run focused tests on the host**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q tests/test_instagram_client_metadata.py tests/test_media_normalizer.py tests/test_telegram_bot_true_inline.py tests/test_video_downloader_flow.py
```

Expected: all unit tests pass; only tests explicitly guarded by missing host ffmpeg/ffprobe may skip.

- [ ] **Step 4: Run real media tests in the application container**

Run:

```bash
docker compose exec -T instagram-video-bot uv run --no-sync pytest -q tests/test_instagram_public_media_ffmpeg.py tests/test_media_normalizer.py
```

Expected: all tests pass and the real-ffmpeg tests do not skip.

- [ ] **Step 5: Run the full suite**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv run pytest -q
```

Expected: full suite passes with only documented pre-existing warnings and environment-dependent skips.

- [ ] **Step 6: Inspect the final diff for scope and secrets**

Run:

```bash
git diff --check HEAD~4..HEAD
git status --short
git diff HEAD~4..HEAD -- src tests
```

Expected: only the files listed in this plan changed; no URLs from live probes, Telegram file IDs, account names, proxies, cookies, or credentials appear.

- [ ] **Step 7: Commit real-ffmpeg coverage**

```bash
git add tests/test_instagram_public_media_ffmpeg.py
git commit -m "test: cover Instagram public audio merge"
```

- [ ] **Step 8: Record durable verification evidence**

Update Second Brain with the final commit range, focused/full pytest counts, container ffmpeg result, and the confirmed cache namespace `instagram:av2:`. Do not store live reel URLs, Telegram file IDs, account names, proxy endpoints, tokens, or credentials.
