# Cold Instagram Reel Latency Design

## Goal

Decrease the time from a cold, unique Instagram reel URL submission to Telegram video delivery. Repeat/viral URL acceleration is explicitly out of scope for this pass because repeats are rare in current usage.

The current production metrics show Telegram delivery is not the main bottleneck:

- Instagram average delivery to Telegram is about 621 ms.
- Fast-path Instagram successes complete in about 4-5 seconds.
- Authenticated fallback successes are about 44 seconds median.
- Most recent Instagram jobs failed the fast path and used fallback.

This pass targets fast-path misses and authenticated fallback latency.

## Scope

In scope:

- Add a total fast-path budget and endpoint-level timing around public Instagram extraction.
- Make authenticated fallback prefer raw/direct media download before slower compatibility paths.
- Reuse metadata already fetched during fallback instead of making duplicate metadata calls.
- Record enough metrics to compare fast-path failure cost and fallback path cost after deployment.

Out of scope:

- Global result cache or cross-chat Telegram file ID reuse.
- File-ID-only cache hits when local files are missing.
- Long-lived Instagram client pooling.
- Local Telegram Bot API server or webhook migration.
- Changing Telegram media delivery behavior beyond existing metadata/file ID usage.

## Architecture

The existing high-level flow remains:

`TelegramBot -> JobManager -> VideoDownloader -> InstagramFastExtractor / InstagramClient fallback -> Telegram delivery`

The optimization is split into three bounded units.

1. `InstagramFastExtractor` gets fast-path timing and a total budget so public endpoint failures stop early instead of consuming several sequential network delays.
2. `InstagramClient` fallback gets a direct-first reel path. After authenticated raw media info is fetched, usable direct video URLs are downloaded before trying slower compatibility fallbacks.
3. Provider adapter metadata handling reuses raw metadata from fallback downloads when available, reducing extra Instagram calls and avoiding local probing where the provider already supplied duration and dimensions.

## Data Flow

For a cold Instagram reel:

1. Telegram receives the URL and creates the normal shared job.
2. `VideoDownloader` starts the Instagram fast path.
3. `InstagramFastExtractor` records each endpoint attempt and duration:
   - oEmbed/media-id lookup
   - mobile media info
   - embed page
   - GraphQL fallback
   - binary media download
4. If the total fast-path budget is exhausted, the fast path stops and returns a classified failure.
5. Authenticated fallback starts.
6. Fallback fetches raw media info once.
7. If raw info contains usable video URLs, fallback downloads directly from those URLs.
8. If raw/direct download fails, fallback tries existing instagrapi-native download behavior.
9. `yt-dlp` is used late as a compatibility fallback after raw/direct and instagrapi-native options fail.
10. Metadata collected from raw info travels into the returned `MediaItem` and `VideoInfo`, reducing post-download metadata calls.
11. Telegram delivery uses the existing `_send_media` flow.

## Configuration

Add conservative settings with safe defaults:

- `IG_FAST_TOTAL_BUDGET_SECONDS`: total public extraction budget before fallback.
- `IG_FAST_METADATA_TIMEOUT_CONNECT_SECONDS`: connect timeout for metadata endpoints.
- `IG_FAST_METADATA_TIMEOUT_READ_SECONDS`: read timeout for metadata endpoints.
- `IG_FALLBACK_YTDLP_TIMEOUT_SECONDS`: shorter timeout for the late `yt-dlp` compatibility fallback.

The first implementation should not add a `yt-dlp` order flag. The default and only intended order for this pass is direct/raw first, instagrapi-native second, and `yt-dlp` late.

## Metrics

Extend the existing performance metrics with enough fields to tune the next pass:

- fast-path total budget exhausted: yes/no
- fast endpoint timings and final fast failure class
- fallback path used: `raw_direct`, `instagrapi_native`, `yt_dlp`
- fallback metadata reused: yes/no
- final download duration, already recorded today

Admin summaries can stay compact, but the raw persisted data should be available for ad hoc inspection from SQLite.

## Error Handling

Fast-path timeout or endpoint-budget exhaustion is not a user-visible failure. It should be classified and passed to fallback exactly like other fast-path misses.

Authenticated fallback should preserve existing behavior:

- Auth-like failures still rotate/quarantine accounts through the account manager.
- Direct/raw download failures fall through to instagrapi-native behavior.
- `yt-dlp` failures remain compatibility fallback failures unless no later path exists.
- Final user-facing error text remains unchanged.

If direct/raw metadata exists but the direct URL download produces an empty or invalid file, the local file should be removed and the next fallback path should run.

## Testing

Add focused tests before implementation:

- Fast-path budget stops later endpoint attempts once exhausted.
- Per-endpoint timing is recorded for fast-path attempts.
- Authenticated reel fallback uses raw/direct video URLs before `yt-dlp`.
- If raw/direct download fails, fallback still reaches the existing compatibility path.
- Metadata from raw info is propagated into `MediaItem` without duplicate metadata calls.
- Existing fallback error handling and account health behavior remain covered.

Run at minimum:

- `uv run pytest -q tests/test_instagram_fast_extractor.py`
- `uv run pytest -q tests/test_video_downloader_flow.py`
- `uv run pytest -q tests/test_telegram_bot_media_send.py`
- `uv run pytest -q`

## Rollout

Ship behind conservative defaults. After deployment, compare recent Instagram metrics:

- fast-path failed duration before fallback
- fallback path distribution
- fallback p50/p90 download duration
- auth failures and provider timeouts

If fallback remains the dominant latency after raw/direct reordering, the next design should investigate session validation TTL or bounded per-account client reuse.
