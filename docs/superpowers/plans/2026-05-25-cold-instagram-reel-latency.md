# Cold Instagram Reel Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce cold unique Instagram reel latency by failing public extraction faster, making authenticated fallback direct/raw-first, and reusing fallback metadata.

**Architecture:** Keep the existing Telegram and job-manager flow intact. Add focused timing/budget support to `InstagramFastExtractor`, add a structured authenticated fallback result from `InstagramClient`, then teach `InstagramProviderAdapter` and `VideoDownloader` to persist the new fallback metrics.

**Tech Stack:** Python 3.11, pytest, pytest-asyncio, requests, instagrapi, yt-dlp, SQLite-backed state store.

---

## File Structure

- Modify `src/instagram_video_bot/config/settings.py`: add conservative fast-path and fallback timeout settings.
- Modify `src/instagram_video_bot/services/download_models.py`: add provider metrics fields for fast budget exhaustion, fast endpoint timings, fallback path, and metadata reuse.
- Modify `src/instagram_video_bot/services/instagram_fast_extractor.py`: add endpoint timing, total budget checks, and a `last_endpoint_timings`/`last_budget_exhausted` interface.
- Modify `src/instagram_video_bot/services/instagram_client.py`: add an `InstagramDownloadResult` dataclass and direct/raw-first authenticated video fallback.
- Modify `src/instagram_video_bot/services/provider_adapters.py`: consume `InstagramDownloadResult`, reuse metadata, and avoid duplicate metadata calls when result metadata is present.
- Modify `src/instagram_video_bot/services/video_downloader.py`: pass new provider metrics from fast extractor and fallback adapter into `ProviderExecutionMetrics`.
- Modify `src/instagram_video_bot/services/state_store.py`: persist the new metrics fields.
- Modify `src/instagram_video_bot/services/telegram_bot.py`: record the new provider metrics.
- Modify `tests/test_instagram_fast_extractor.py`: add fast budget and endpoint timing tests.
- Modify `tests/test_video_downloader_flow.py`: add fallback direct-first, fallback path metric, and metadata reuse tests.
- Modify `tests/test_performance_metrics.py`: add persistence/summary coverage for new metrics.

## Task 1: Add Metrics and Settings Plumbing

**Files:**
- Modify: `src/instagram_video_bot/config/settings.py`
- Modify: `src/instagram_video_bot/services/download_models.py`
- Modify: `src/instagram_video_bot/services/state_store.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Test: `tests/test_performance_metrics.py`

- [ ] **Step 1: Write failing metrics persistence test**

Append to `tests/test_performance_metrics.py`:

```python
def test_metrics_records_cold_instagram_latency_breakdown(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.start_job_metrics(
        job_id="job-cold",
        chat_id=77,
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/cold/",
    )
    store.mark_job_metrics_started("job-cold")
    store.record_download_metrics(
        "job-cold",
        download_duration_ms=9000,
        instagram_fast_status="failed",
        instagram_fast_duration_ms=2200,
        instagram_fast_budget_exhausted=True,
        instagram_fast_endpoint_timings_json='[{"name":"mobile_info","status":"miss","duration_ms":1500}]',
        instagram_fallback_attempted=True,
        instagram_success_path="fallback",
        instagram_fallback_path="raw_direct",
        instagram_metadata_reused=True,
    )
    store.finalize_job_metrics("job-cold", status="completed")

    summary = store.get_performance_summary(77, limit=50)

    assert summary["instagram"]["fast_budget_exhausted"] == 1
    assert summary["instagram"]["fallback_paths"]["raw_direct"] == 1
    assert summary["instagram"]["metadata_reused"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q tests/test_performance_metrics.py::test_metrics_records_cold_instagram_latency_breakdown
```

Expected: FAIL because `record_download_metrics()` does not accept the new keyword arguments.

- [ ] **Step 3: Add settings**

In `src/instagram_video_bot/config/settings.py`, add these fields near the existing fast Instagram settings:

```python
    IG_FAST_TOTAL_BUDGET_SECONDS: float = 10.0
    IG_FAST_METADATA_TIMEOUT_CONNECT_SECONDS: float = 5.0
    IG_FAST_METADATA_TIMEOUT_READ_SECONDS: float = 8.0
    IG_FALLBACK_YTDLP_TIMEOUT_SECONDS: float = 15.0
```

- [ ] **Step 4: Extend provider metrics dataclass**

In `src/instagram_video_bot/services/download_models.py`, extend `ProviderExecutionMetrics`:

```python
    instagram_fast_budget_exhausted: bool = False
    instagram_fast_endpoint_timings_json: Optional[str] = None
    instagram_fallback_path: Optional[str] = None
    instagram_metadata_reused: bool = False
```

- [ ] **Step 5: Add SQLite columns with migration safety**

In `src/instagram_video_bot/services/state_store.py`, extend the `performance_metrics` schema with:

```sql
                    instagram_fast_budget_exhausted INTEGER NOT NULL DEFAULT 0,
                    instagram_fast_endpoint_timings_json TEXT,
                    instagram_fallback_path TEXT,
                    instagram_metadata_reused INTEGER NOT NULL DEFAULT 0,
```

Then extend the existing migration helper that checks `PRAGMA table_info(performance_metrics)` with:

```python
            if "instagram_fast_budget_exhausted" not in columns:
                self._conn.execute(
                    "ALTER TABLE performance_metrics ADD COLUMN instagram_fast_budget_exhausted INTEGER NOT NULL DEFAULT 0"
                )
            if "instagram_fast_endpoint_timings_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE performance_metrics ADD COLUMN instagram_fast_endpoint_timings_json TEXT"
                )
            if "instagram_fallback_path" not in columns:
                self._conn.execute(
                    "ALTER TABLE performance_metrics ADD COLUMN instagram_fallback_path TEXT"
                )
            if "instagram_metadata_reused" not in columns:
                self._conn.execute(
                    "ALTER TABLE performance_metrics ADD COLUMN instagram_metadata_reused INTEGER NOT NULL DEFAULT 0"
                )
```

- [ ] **Step 6: Update metrics writer and summary**

Change `StateStore.record_download_metrics()` signature to include:

```python
        instagram_fast_budget_exhausted: bool = False,
        instagram_fast_endpoint_timings_json: str | None = None,
        instagram_fallback_path: str | None = None,
        instagram_metadata_reused: bool = False,
```

Add these assignments in the SQL `SET` clause:

```sql
                instagram_fast_budget_exhausted = ?,
                instagram_fast_endpoint_timings_json = ?,
                instagram_fallback_path = ?,
                instagram_metadata_reused = ?,
```

Add these values before `failure_class`/`job_id` in the values tuple:

```python
                1 if instagram_fast_budget_exhausted else 0,
                instagram_fast_endpoint_timings_json,
                instagram_fallback_path,
                1 if instagram_metadata_reused else 0,
```

In `get_performance_summary()`, extend the `instagram` dict:

```python
                "fast_budget_exhausted": sum(
                    1 for row in instagram_rows if row["instagram_fast_budget_exhausted"]
                ),
                "fallback_paths": {
                    path: sum(
                        1 for row in instagram_rows if row["instagram_fallback_path"] == path
                    )
                    for path in sorted(
                        {
                            row["instagram_fallback_path"]
                            for row in instagram_rows
                            if row["instagram_fallback_path"]
                        }
                    )
                },
                "metadata_reused": sum(
                    1 for row in instagram_rows if row["instagram_metadata_reused"]
                ),
```

- [ ] **Step 7: Pass metrics from TelegramBot**

In `src/instagram_video_bot/services/telegram_bot.py`, update `_record_provider_metrics()` to pass:

```python
            instagram_fast_budget_exhausted=bool(
                getattr(metrics, "instagram_fast_budget_exhausted", False)
            ),
            instagram_fast_endpoint_timings_json=getattr(
                metrics, "instagram_fast_endpoint_timings_json", None
            ),
            instagram_fallback_path=getattr(metrics, "instagram_fallback_path", None),
            instagram_metadata_reused=bool(
                getattr(metrics, "instagram_metadata_reused", False)
            ),
```

- [ ] **Step 8: Run metrics tests**

Run:

```bash
uv run pytest -q tests/test_performance_metrics.py
```

Expected: PASS.

- [ ] **Step 9: Commit metrics plumbing**

```bash
git add src/instagram_video_bot/config/settings.py src/instagram_video_bot/services/download_models.py src/instagram_video_bot/services/state_store.py src/instagram_video_bot/services/telegram_bot.py tests/test_performance_metrics.py
git commit -m "Add cold Instagram latency metrics"
```

## Task 2: Add Fast-Path Total Budget and Endpoint Timings

**Files:**
- Modify: `src/instagram_video_bot/services/instagram_fast_extractor.py`
- Modify: `src/instagram_video_bot/services/video_downloader.py`
- Test: `tests/test_instagram_fast_extractor.py`
- Test: `tests/test_video_downloader_flow.py`

- [ ] **Step 1: Write failing fast budget test**

Append to `tests/test_instagram_fast_extractor.py`:

```python
def test_fast_extractor_budget_stops_later_endpoint_attempts(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor(total_budget_seconds=0.01)
    calls = []

    def fake_get_media_id(_canonical_url):
        calls.append("media_id")
        time.sleep(0.02)
        return None

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("later endpoint should not run after budget is exhausted")

    monkeypatch.setattr(extractor, "_get_media_id", fake_get_media_id)
    monkeypatch.setattr(extractor, "_request_embed_data", fail_if_called)
    monkeypatch.setattr(extractor, "_request_graphql_data", fail_if_called)

    with pytest.raises(InstagramFastExtractorError, match="fast_path_budget_exhausted"):
        extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    assert calls == ["media_id"]
    assert extractor.last_budget_exhausted is True
    assert extractor.last_endpoint_timings[0]["name"] == "media_id"
```

- [ ] **Step 2: Write failing endpoint timing test**

Append to `tests/test_instagram_fast_extractor.py`:

```python
def test_fast_extractor_records_endpoint_timings(monkeypatch, tmp_path):
    extractor = InstagramFastExtractor(total_budget_seconds=5.0)

    monkeypatch.setattr(extractor, "_get_media_id", lambda _canonical_url: None)
    monkeypatch.setattr(extractor, "_request_embed_data", lambda _shortcode: {})
    monkeypatch.setattr(extractor, "_request_graphql_data", lambda _shortcode: {})

    with pytest.raises(InstagramFastExtractorError):
        extractor.extract_and_download("https://www.instagram.com/reel/abc123/", tmp_path)

    names = [item["name"] for item in extractor.last_endpoint_timings]
    assert names == ["media_id", "embed", "graphql"]
    assert all("duration_ms" in item for item in extractor.last_endpoint_timings)
    assert all(item["status"] in {"miss", "failed"} for item in extractor.last_endpoint_timings)
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_instagram_fast_extractor.py::test_fast_extractor_budget_stops_later_endpoint_attempts tests/test_instagram_fast_extractor.py::test_fast_extractor_records_endpoint_timings
```

Expected: FAIL because `total_budget_seconds`, `last_budget_exhausted`, and `last_endpoint_timings` do not exist yet.

- [ ] **Step 4: Implement timing helper and constructor fields**

In `src/instagram_video_bot/services/instagram_fast_extractor.py`, add imports:

```python
from time import monotonic, perf_counter
from contextlib import contextmanager
from collections.abc import Iterator
```

Change `__init__`:

```python
    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout_connect: float = 10,
        timeout_read: float = 45,
        metadata_timeout: tuple[float, float] | None = None,
        total_budget_seconds: float | None = None,
    ) -> None:
        self.proxy = proxy
        self.timeout = (timeout_connect, timeout_read)
        self.metadata_timeout = metadata_timeout or self.timeout
        self.total_budget_seconds = max(
            0.1,
            float(total_budget_seconds if total_budget_seconds is not None else settings.IG_FAST_TOTAL_BUDGET_SECONDS),
        )
        self.session = requests.Session()
        self.last_endpoint_timings: list[dict[str, Any]] = []
        self.last_budget_exhausted = False
        self._budget_deadline = 0.0
```

Add helpers:

```python
    def _reset_attempt_metrics(self) -> None:
        self.last_endpoint_timings = []
        self.last_budget_exhausted = False
        self._budget_deadline = monotonic() + self.total_budget_seconds

    def _ensure_budget(self) -> None:
        if monotonic() > self._budget_deadline:
            self.last_budget_exhausted = True
            raise InstagramFastExtractorError("fast_path_budget_exhausted")

    @contextmanager
    def _timed_endpoint(self, name: str) -> Iterator[None]:
        self._ensure_budget()
        started_at = perf_counter()
        status = "failed"
        try:
            yield
            status = "miss"
        except Exception:
            status = "failed"
            raise
        finally:
            self.last_endpoint_timings.append(
                {
                    "name": name,
                    "status": status,
                    "duration_ms": max(0, round((perf_counter() - started_at) * 1000)),
                }
            )
```

- [ ] **Step 5: Wrap endpoint attempts**

At the start of `extract_and_download()`, after parsing the URL, call:

```python
        self._reset_attempt_metrics()
```

In `_extract_post()`, wrap endpoint attempts:

```python
        with self._timed_endpoint("media_id"):
            media_id = self._get_media_id(canonical_url)
        self._ensure_budget()

        if media_id:
            with self._timed_endpoint("mobile_info"):
                mobile_item = self._request_mobile_media_info(media_id)
            self._ensure_budget()
            caption, items = self._parse_mobile_item(mobile_item)
            if items:
                self.last_endpoint_timings[-1]["status"] = "hit"
                return caption, items

        with self._timed_endpoint("embed"):
            embed_data = self._request_embed_data(shortcode)
        self._ensure_budget()
        caption, items = self._parse_embed_or_graphql_data(embed_data)
        if items:
            self.last_endpoint_timings[-1]["status"] = "hit"
            return caption, items

        with self._timed_endpoint("graphql"):
            gql_data = self._request_graphql_data(shortcode)
        self._ensure_budget()
        caption, items = self._parse_embed_or_graphql_data(gql_data)
        if items:
            self.last_endpoint_timings[-1]["status"] = "hit"
            return caption, items
```

Use `timeout=self.metadata_timeout` for metadata requests by adding an optional `timeout` parameter to `_request_raw()` and passing it through from `_request_json()`:

```python
        timeout: tuple[float, float] | None = None,
```

and inside `session.request()`:

```python
                timeout=timeout or self.timeout,
```

Call metadata requests with `timeout=self.metadata_timeout`.

- [ ] **Step 6: Instantiate extractor with settings**

In `VideoDownloader.__init__`, build the extractor with:

```python
        fast_extractor = InstagramFastExtractor(
            timeout_connect=settings.IG_FAST_TIMEOUT_CONNECT,
            timeout_read=settings.IG_FAST_TIMEOUT_READ,
            metadata_timeout=(
                settings.IG_FAST_METADATA_TIMEOUT_CONNECT_SECONDS,
                settings.IG_FAST_METADATA_TIMEOUT_READ_SECONDS,
            ),
            total_budget_seconds=settings.IG_FAST_TOTAL_BUDGET_SECONDS,
        )
```

- [ ] **Step 7: Persist fast extractor metrics into provider metrics**

In `_download_instagram_media()`, after the fast-path call succeeds or fails, set:

```python
                self.last_provider_metrics.instagram_fast_budget_exhausted = bool(
                    getattr(self.fast_extractor, "last_budget_exhausted", False)
                )
                self.last_provider_metrics.instagram_fast_endpoint_timings_json = json.dumps(
                    getattr(self.fast_extractor, "last_endpoint_timings", [])
                )
```

Add `import json` at the top of `video_downloader.py`.

- [ ] **Step 8: Run fast extractor and downloader tests**

Run:

```bash
uv run pytest -q tests/test_instagram_fast_extractor.py tests/test_video_downloader_flow.py
```

Expected: PASS.

- [ ] **Step 9: Commit fast-path budget**

```bash
git add src/instagram_video_bot/services/instagram_fast_extractor.py src/instagram_video_bot/services/video_downloader.py tests/test_instagram_fast_extractor.py tests/test_video_downloader_flow.py
git commit -m "Bound Instagram fast-path extraction time"
```

## Task 3: Add Direct/Raw-First Fallback Result

**Files:**
- Modify: `src/instagram_video_bot/services/instagram_client.py`
- Modify: `src/instagram_video_bot/services/provider_adapters.py`
- Test: `tests/test_video_downloader_flow.py`

- [ ] **Step 1: Write failing direct-first fallback test**

Append to `tests/test_video_downloader_flow.py`:

```python
def test_instagram_client_uses_raw_direct_video_before_ytdlp(monkeypatch, tmp_path):
    from src.instagram_video_bot.services.instagram_client import InstagramClient

    client = InstagramClient(username="acc", password="pw")
    output_file = tmp_path / "video_123.mp4"
    calls = []

    class _FakeInstagrapi:
        user_agent = "ua"
        cookie_jar = {}

        def media_pk_from_url(self, _url):
            calls.append("pk")
            return 123

    client.client = _FakeInstagrapi()
    monkeypatch.setattr(
        client,
        "_get_media_dict_raw",
        lambda _pk: {
            "caption": {"text": "raw caption"},
            "video_duration": 12.5,
            "video_versions": [
                {"url": "https://cdn.example.com/raw.mp4", "width": 720, "height": 1280}
            ],
        },
    )

    def fake_manual(video_url, media_pk, output_dir):
        calls.append(("manual", video_url, media_pk, output_dir))
        output_file.write_bytes(b"video")
        return output_file

    monkeypatch.setattr(client, "_download_video_manually", fake_manual)
    monkeypatch.setattr(
        client,
        "_download_with_ytdlp_first",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("yt-dlp should be late")),
    )

    result = client.download_media("https://www.instagram.com/reel/raw/", tmp_path)

    assert result.file_paths == [output_file]
    assert result.fallback_path == "raw_direct"
    assert result.metadata["title"] == "raw caption"
    assert result.metadata["duration"] == 12.5
    assert result.metadata["width"] == 720
    assert result.metadata["height"] == 1280
    assert calls[0] == "pk"
    assert calls[1][0] == "manual"
```

- [ ] **Step 2: Write failing fallback continuation test**

Append to `tests/test_video_downloader_flow.py`:

```python
def test_instagram_client_falls_back_to_native_then_ytdlp_when_raw_direct_fails(monkeypatch, tmp_path):
    from src.instagram_video_bot.services.instagram_client import InstagramClient

    client = InstagramClient(username="acc", password="pw")
    native_file = tmp_path / "native.mp4"
    native_file.write_bytes(b"video")
    calls = []

    class _FakeInstagrapi:
        user_agent = "ua"
        cookie_jar = {}

        def media_pk_from_url(self, _url):
            return 123

        def video_download(self, media_pk):
            calls.append(("native", media_pk))
            return native_file

    client.client = _FakeInstagrapi()
    monkeypatch.setattr(
        client,
        "_get_media_dict_raw",
        lambda _pk: {
            "video_versions": [{"url": "https://cdn.example.com/raw.mp4"}],
        },
    )
    monkeypatch.setattr(client, "_download_video_manually", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        client,
        "_download_with_ytdlp_first",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("yt-dlp should not run when native succeeds")),
    )

    result = client.download_media("https://www.instagram.com/reel/native/", tmp_path)

    assert result.file_paths == [native_file]
    assert result.fallback_path == "instagrapi_native"
    assert calls == [("native", 123)]
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_video_downloader_flow.py::test_instagram_client_uses_raw_direct_video_before_ytdlp tests/test_video_downloader_flow.py::test_instagram_client_falls_back_to_native_then_ytdlp_when_raw_direct_fails
```

Expected: FAIL because `download_media()` currently returns `Path | list[Path] | None`.

- [ ] **Step 4: Add `InstagramDownloadResult` and metadata helpers**

In `src/instagram_video_bot/services/instagram_client.py`, import dataclass and Literal:

```python
from dataclasses import dataclass, field
from typing import Literal, Optional
```

Add above `InstagramClient`:

```python
@dataclass
class InstagramDownloadResult:
    file_paths: list[Path]
    fallback_path: Literal["raw_direct", "instagrapi_native", "yt_dlp", "album", "photo", "story"]
    metadata: dict = field(default_factory=dict)
    metadata_reused: bool = False
```

Add helper:

```python
    def _metadata_from_raw_item(self, raw_item: dict, media_pk: int) -> dict:
        caption = raw_item.get("caption")
        title = ""
        if isinstance(caption, dict):
            title = str(caption.get("text") or "")
        video_url_item = self._pick_highest_video_version(raw_item) or {}
        return {
            "title": title,
            "duration": self._safe_float(raw_item.get("video_duration")) or 0,
            "width": self._safe_int(video_url_item.get("width")),
            "height": self._safe_int(video_url_item.get("height")),
            "user": ((raw_item.get("user") or {}).get("username") if isinstance(raw_item.get("user"), dict) else "unknown") or "unknown",
            "pk": media_pk,
        }

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pick_highest_video_version(item: dict) -> Optional[dict]:
        video_versions = item.get("video_versions")
        if not isinstance(video_versions, list):
            return None
        candidates = [
            candidate
            for candidate in video_versions
            if isinstance(candidate, dict) and candidate.get("url")
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda candidate: int(candidate.get("width") or 0) * int(candidate.get("height") or 0),
        )
```

- [ ] **Step 5: Convert return values to structured result**

Change `download_media()` return type:

```python
    def download_media(self, url: str, output_dir: Path) -> Optional[InstagramDownloadResult]:
```

In `_download_post_media()`, return `InstagramDownloadResult` for album/photo/video branches:

```python
                return InstagramDownloadResult(
                    file_paths=album_paths,
                    fallback_path="album",
                    metadata=self._metadata_from_raw_item(raw_item, media_pk),
                    metadata_reused=True,
                )
```

For photo:

```python
                return InstagramDownloadResult(
                    file_paths=[photo_path],
                    fallback_path="photo",
                    metadata=self._metadata_from_raw_item(raw_item, media_pk),
                    metadata_reused=True,
                )
```

For story, return:

```python
            return InstagramDownloadResult(file_paths=[story_path], fallback_path="story")
```

- [ ] **Step 6: Reorder video fallback direct/raw first**

Replace `download_video()` body with this order while preserving auth handling:

```python
    def download_video(self, url: str, output_dir: Path) -> Optional[InstagramDownloadResult]:
        self.last_failure_class = None
        self.last_failure_reason = None
        try:
            media_pk = int(self.client.media_pk_from_url(url))
            logger.info(f"Extracted media PK: {media_pk}")
            output_dir.mkdir(parents=True, exist_ok=True)

            raw_item = self._get_media_dict_raw(media_pk) or {}
            metadata = self._metadata_from_raw_item(raw_item, media_pk) if raw_item else {"title": "", "duration": 0, "pk": media_pk}
            video_url = self._pick_video_url(raw_item)
            if video_url:
                raw_path = self._download_video_manually(video_url, media_pk, output_dir)
                if raw_path and raw_path.exists():
                    return InstagramDownloadResult(
                        file_paths=[raw_path],
                        fallback_path="raw_direct",
                        metadata=metadata,
                        metadata_reused=bool(raw_item),
                    )

            native_path = self._download_video_native(media_pk, output_dir)
            if native_path and native_path.exists():
                return InstagramDownloadResult(
                    file_paths=[native_path],
                    fallback_path="instagrapi_native",
                    metadata=metadata,
                    metadata_reused=bool(raw_item),
                )

            ytdlp_path = self._download_with_ytdlp_first(url, media_pk, output_dir)
            if ytdlp_path:
                return InstagramDownloadResult(
                    file_paths=[ytdlp_path],
                    fallback_path="yt_dlp",
                    metadata=metadata,
                    metadata_reused=bool(raw_item),
                )

            logger.error("All download methods failed")
            return None
        except InstagramAuthError:
            raise
        except Exception as error:
            logger.error(f"Video download failed: {error}")
            self._record_failure(error)
            if self._is_auth_error(error):
                raise InstagramAuthError(str(error)) from error
            return None
```

Add `_download_video_native()` extracted from the old standard `video_download` branch:

```python
    def _download_video_native(self, media_pk: int, output_dir: Path) -> Optional[Path]:
        try:
            video_path = self.client.video_download(media_pk)
            if video_path and video_path.exists():
                import shutil
                final_path = output_dir / video_path.name
                if video_path != final_path:
                    shutil.move(str(video_path), str(final_path))
                logger.info(f"Video downloaded: {final_path}")
                return final_path
        except Exception as download_error:
            failure_class = self._classify_instagram_error(download_error)
            self._record_failure(download_error)
            if self._is_auth_error(download_error):
                logger.warning("Session expired during native download, attempting re-login...")
                if not self._relogin():
                    raise InstagramAuthError(str(download_error)) from download_error
                try:
                    video_path = self.client.video_download(media_pk, folder=output_dir)
                    if video_path and video_path.exists():
                        return video_path
                except Exception as retry_error:
                    if self._is_auth_error(retry_error):
                        raise InstagramAuthError(str(retry_error)) from retry_error
                    logger.warning("Native download still failed after re-login")
            logger.warning("Native video download failed", extra={"failure_class": failure_class})
        return None
```

- [ ] **Step 7: Make yt-dlp timeout configurable**

In `_download_with_ytdlp_first()`, replace:

```python
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
```

with:

```python
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(1.0, float(settings.IG_FALLBACK_YTDLP_TIMEOUT_SECONDS)),
            )
```

- [ ] **Step 8: Run direct fallback tests**

Run:

```bash
uv run pytest -q tests/test_video_downloader_flow.py::test_instagram_client_uses_raw_direct_video_before_ytdlp tests/test_video_downloader_flow.py::test_instagram_client_falls_back_to_native_then_ytdlp_when_raw_direct_fails
```

Expected: PASS.

- [ ] **Step 9: Commit direct-first fallback**

```bash
git add src/instagram_video_bot/services/instagram_client.py tests/test_video_downloader_flow.py
git commit -m "Prefer direct Instagram fallback downloads"
```

## Task 4: Reuse Fallback Metadata in Adapter and Provider Metrics

**Files:**
- Modify: `src/instagram_video_bot/services/provider_adapters.py`
- Modify: `src/instagram_video_bot/services/video_downloader.py`
- Test: `tests/test_video_downloader_flow.py`

- [ ] **Step 1: Write failing metadata reuse adapter test**

Append to `tests/test_video_downloader_flow.py`:

```python
def test_instagram_adapter_reuses_structured_download_metadata(monkeypatch, tmp_path):
    from src.instagram_video_bot.services.instagram_client import InstagramDownloadResult

    video_file = tmp_path / "raw.mp4"
    video_file.write_bytes(b"video")

    class _StructuredClient:
        username = "acc_structured"
        proxy = None

        def download_media(self, _url: str, _output_dir: Path):
            return InstagramDownloadResult(
                file_paths=[video_file],
                fallback_path="raw_direct",
                metadata={"title": "raw title", "duration": 13.0, "width": 720, "height": 1280},
                metadata_reused=True,
            )

        def get_media_info(self, _url: str):
            raise AssertionError("metadata lookup should be skipped when raw metadata exists")

    monkeypatch.setattr(
        "src.instagram_video_bot.services.provider_adapters.probe_video_metadata",
        lambda _path: (_ for _ in ()).throw(AssertionError("ffprobe should not run when dimensions exist")),
    )

    adapter = InstagramProviderAdapter(fast_extractor=None)
    info = adapter.download_with_instagram_client(
        client=_StructuredClient(),
        url="https://www.instagram.com/reel/raw/",
        output_dir=tmp_path,
        redact_proxy=lambda proxy: proxy,
    )

    assert info.title == "raw title"
    assert info.media_items[0].duration == 13.0
    assert info.media_items[0].width == 720
    assert info.media_items[0].height == 1280
    assert adapter.last_fallback_path == "raw_direct"
    assert adapter.last_metadata_reused is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q tests/test_video_downloader_flow.py::test_instagram_adapter_reuses_structured_download_metadata
```

Expected: FAIL because the adapter does not expose `last_fallback_path` and does not handle structured results.

- [ ] **Step 3: Consume structured results in adapter**

In `InstagramProviderAdapter.__init__`, add:

```python
        self.last_fallback_path: str | None = None
        self.last_metadata_reused = False
```

In `download_with_instagram_client()`, after `downloaded_paths = ...`, normalize result:

```python
        self.last_fallback_path = None
        self.last_metadata_reused = False
        downloaded_result = self._download_with_legacy_client(client, url, output_dir)
        if hasattr(downloaded_result, "file_paths"):
            file_paths = list(downloaded_result.file_paths)
            media_info = dict(downloaded_result.metadata or {})
            self.last_fallback_path = downloaded_result.fallback_path
            self.last_metadata_reused = bool(downloaded_result.metadata_reused)
        elif isinstance(downloaded_result, list):
            file_paths = downloaded_result
            media_info = {"title": "", "duration": 0}
        elif downloaded_result:
            file_paths = [downloaded_result]
            media_info = {"title": "", "duration": 0}
        else:
            file_paths = []
            media_info = {"title": "", "duration": 0}
```

Replace downstream uses of `downloaded_paths` accordingly. Only call `client.get_media_info(url)` if `media_info` does not contain a useful title/duration/dimensions:

```python
        if not self._has_useful_metadata(media_info):
            try:
                info = client.get_media_info(url)
                if info:
                    media_info = info
            except InstagramAuthError as auth_error:
                ...
```

Add:

```python
    @staticmethod
    def _has_useful_metadata(media_info: dict) -> bool:
        return bool(
            media_info.get("title")
            or media_info.get("duration")
            or media_info.get("width")
            or media_info.get("height")
        )
```

Pass width/height into `_build_media_item()`:

```python
                    width=media_info.get("width"),
                    height=media_info.get("height"),
```

- [ ] **Step 4: Propagate adapter metrics to downloader**

In `VideoDownloader._download_with_leased_account_sync()` and `_download_with_single_account_sync()`, after adapter returns, keep metrics readable from `self.instagram_adapter`.

In `_download_with_account_leases()`, after a successful result:

```python
                self.last_provider_metrics.instagram_fallback_path = getattr(
                    self.instagram_adapter, "last_fallback_path", None
                )
                self.last_provider_metrics.instagram_metadata_reused = bool(
                    getattr(self.instagram_adapter, "last_metadata_reused", False)
                )
```

Add the same assignments in `_download_with_single_account()` before returning.

- [ ] **Step 5: Run adapter and downloader tests**

Run:

```bash
uv run pytest -q tests/test_video_downloader_flow.py
```

Expected: PASS.

- [ ] **Step 6: Commit metadata reuse**

```bash
git add src/instagram_video_bot/services/provider_adapters.py src/instagram_video_bot/services/video_downloader.py tests/test_video_downloader_flow.py
git commit -m "Reuse Instagram fallback metadata"
```

## Task 5: Full Verification and Deployment Readiness

**Files:**
- No new files expected.
- Verify all files touched by Tasks 1-4.

- [ ] **Step 1: Run focused suites**

Run:

```bash
uv run pytest -q tests/test_instagram_fast_extractor.py
uv run pytest -q tests/test_video_downloader_flow.py
uv run pytest -q tests/test_performance_metrics.py
uv run pytest -q tests/test_telegram_bot_media_send.py
```

Expected: all PASS.

- [ ] **Step 2: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all PASS.

- [ ] **Step 3: Check diff hygiene**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exits 0. `git status --short` shows only intentional committed or staged files.

- [ ] **Step 4: Inspect metrics fields in a quick smoke script**

Run:

```bash
uv run python - <<'PY'
from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.download_models import ProviderExecutionMetrics

metrics = ProviderExecutionMetrics(provider="instagram")
metrics.instagram_fast_budget_exhausted = True
metrics.instagram_fallback_path = "raw_direct"
metrics.instagram_metadata_reused = True
print(settings.IG_FAST_TOTAL_BUDGET_SECONDS)
print(metrics)
PY
```

Expected: prints a numeric budget and a `ProviderExecutionMetrics` repr containing `raw_direct`.

- [ ] **Step 5: Final commit if needed**

If Task 5 required any fixups:

```bash
git add <changed-files>
git commit -m "Stabilize cold Instagram latency changes"
```

If no fixups were needed, do not create an empty commit.

## Post-Implementation PR Notes

When opening the PR, include:

- Production baseline used for design: delivery ~621 ms, fast success ~4-5 s, fallback success ~44 s median.
- Main behavior change: authenticated fallback now tries raw/direct media URLs before `yt-dlp`.
- Risk controls: `yt-dlp` remains as a late compatibility fallback, user-facing errors unchanged, auth rotation semantics preserved.
- Verification: list all focused suites and full suite.

## Rollback Plan

The implementation is controlled by conservative settings. If deployment shows worse success rate:

1. Increase `IG_FAST_TOTAL_BUDGET_SECONDS` and metadata read timeout first.
2. Increase `IG_FALLBACK_YTDLP_TIMEOUT_SECONDS` if late fallback is timing out too aggressively.
3. If direct/raw-first causes unexpected failures, revert the fallback-order commit while keeping metrics plumbing.
