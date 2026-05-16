# Instagram Throughput and Performance Metrics Design

## Goal

Improve reliability and processing speed for the Telegram media downloader bot, with special focus on Instagram because it is the main traffic source. The work should deliver immediate throughput improvements while adding enough persistent metrics to show where time is spent and whether the changes helped.

## Current Context

The project already has a mature request pipeline:

- `TelegramBot` parses messages, submits jobs, sends media, and exposes owner/group commands.
- `RequestParser` normalizes Instagram, Twitter/X, and YouTube Shorts links.
- `JobManager` coordinates shared jobs, duplicate suppression, cancellation, and bounded concurrency.
- `VideoDownloader` routes provider downloads and handles Instagram fast extraction plus authenticated fallback.
- `AccountManager` leases Instagram accounts and prevents the same account from being used by concurrent jobs.
- `StateStore` persists jobs, request events, group settings, result cache, and lightweight stats in SQLite.

The design should build on these boundaries instead of replacing them.

## Chosen Approach

Use a metrics plus adaptive throughput approach.

This means:

- Add persistent stage timing and failure metrics to SQLite.
- Expose compact performance reporting in `/admin_status`.
- Add provider-aware concurrency limits.
- Improve Instagram throughput by parallelizing across healthy leased accounts while keeping per-account throttling.
- Add bounded retry classification so transient failures are retried and permanent failures are not.

This is preferred over metrics-only because users should feel some immediate speed improvement. It is preferred over blind concurrency increases because Instagram account health and provider rate limits are real constraints.

## Architecture

The existing flow remains:

Telegram message -> `RequestParser` -> `JobManager` -> `VideoDownloader` -> Telegram delivery -> `StateStore`.

The new performance layer has three parts:

1. Stage timing metrics

   Add persistent SQLite records for queue wait, download duration, Telegram delivery duration, cache hit or miss, provider, retry count, failure class, and final status. Metrics should be written at natural lifecycle boundaries in `JobManager`, `TelegramBot`, and `VideoDownloader`.

2. Provider-aware throughput

   Keep Instagram anti-ban behavior conservative, but make concurrency provider-specific. Twitter/X and YouTube Shorts can run with higher effective throughput than Instagram because they use `yt-dlp` and do not consume the Instagram account pool. Instagram concurrency should increase only when separate healthy account leases are available.

3. Smarter retry path

   Classify failures into transient, provider or auth, Telegram delivery, and permanent unsupported-link errors. Retry only cases likely to succeed: transient downloader errors, Telegram network errors, and alternate Instagram accounts after auth-like failures.

## Components

### Metrics Persistence

Add SQLite-backed metrics support to `StateStore`. The implementation may use one job-level table plus optional attempt rows, or one table with nullable provider-specific columns, as long as it can answer the admin reporting needs.

Required job-level fields:

- `job_id`
- `chat_id`
- `provider`
- `normalized_url`
- `queued_at`
- `started_at`
- `finished_at`
- `status`
- `cache_hit`
- `queue_wait_ms`
- `download_duration_ms`
- `delivery_duration_ms`
- `retry_count`
- `failure_class`

Required Instagram-specific fields or attempt records:

- fast path attempted, skipped, succeeded, or failed
- fast path duration
- fallback attempted
- account attempt count
- account retry count
- auth-like failure count
- final success path: cache, fast path, fallback, or failed

Metrics writes must be best-effort. A metrics write failure should be logged and must not break media delivery.

### JobManager Timing Hooks

`JobManager` already knows when a job is submitted, starts running, completes, fails, or is cancelled. Add small hooks there to:

- start metrics at submit time
- record `started_at` when execution begins
- finalize status and total timing on completed, failed, or cancelled jobs
- retain existing job and request event behavior

The public queue API should stay mostly unchanged.

### Provider Concurrency Policy

Add settings for provider-specific throughput:

- `INSTAGRAM_MAX_CONCURRENT_JOBS`
- `TWITTER_MAX_CONCURRENT_JOBS`
- `YOUTUBE_SHORTS_MAX_CONCURRENT_JOBS`
- `PROVIDER_TRANSIENT_RETRY_ATTEMPTS`
- `PROVIDER_RETRY_BACKOFF_SECONDS`
- `INSTAGRAM_FAST_PATH_TIMEOUT_BUDGET_SECONDS`, if fast-path timing shows the need for a shorter budget

Instagram concurrency must be account-aware:

- The bot may run more than one Instagram job at the same time.
- Each concurrent Instagram job must use a distinct leased account when fallback is required.
- The existing `AccountManager` lease set remains the source of truth for account exclusivity.
- Per-account throttle and jitter remain in place.
- If no healthy account is available, Instagram jobs wait instead of sharing an active account.

Fast-path Instagram extraction does not consume an account lease. It should remain the first path for non-story URLs, but its success rate and cost must be measured.

### Instagram Attempt Tracking

`VideoDownloader._download_instagram_media` should record:

- whether fast path was attempted, skipped, succeeded, or failed
- fast-path duration and error class
- whether fallback was attempted
- number of account attempts
- number of auth-like failures
- final success path

Account identifiers should not expose credentials, proxies, or raw sensitive data. If per-account identification is useful, use redacted or hashed identifiers.

### Admin Reporting

Extend `/admin_status` with a compact performance section. It should use a recent window, such as the last 50 jobs or last 24 hours, so old history does not hide current behavior.

Include:

- average queue wait by provider
- average download time by provider
- average Telegram delivery time
- cache hit count and rate
- duplicate join count
- Instagram fast-path hit rate
- Instagram fallback count
- Instagram account retry count
- auth-like failure count
- recent slow jobs or top failure classes

The Telegram message must stay concise and must not expose credentials or proxy secrets.

## Data Flow

1. A user sends one or more links.
2. `TelegramBot.handle_message` extracts and normalizes supported links.
3. `JobManager.submit` creates job and request records, then starts a metrics row with `queued_at`.
4. When `JobManager._run_job` enters execution, it records `started_at`.
5. The job executor checks the result cache.
6. On a cache hit, metrics mark `cache_hit=true`, provider download is skipped, and cached files are sent.
7. On a cache miss, `VideoDownloader` records provider-stage details.
8. Instagram tries fast path first for non-story URLs.
9. If fast path succeeds, no account is leased.
10. If fast path fails or the URL is a story, Instagram fallback leases healthy accounts one at a time.
11. Multiple Instagram jobs may run in parallel only when separate account leases are available.
12. Twitter/X and YouTube Shorts use their own provider limits and retry rules.
13. `TelegramBot._send_media` records delivery duration and any Telegram failure class.
14. `JobManager` marks the final job status and metrics become available for `/admin_status`.

## Error Handling and Safety

The safety principle is to improve throughput by removing wasted time, not by making the bot reckless.

Instagram rules:

- Keep per-account throttling and jitter.
- Parallelize Instagram only across distinct leased accounts.
- Wait when no healthy account is available.
- Preserve existing account health tracking and quarantine thresholds.
- Keep fast-path failures separate from account failures.
- Keep story URLs on fallback unless a future change proves a safe fast story path.

Retry rules:

- Use explicit failure classes: `transient_network`, `provider_timeout`, `auth_challenge`, `unsupported_url`, `telegram_network`, `telegram_api`, and `unknown`.
- Retry only transient classes.
- Use short backoff with jitter.
- Record retry count and final failure class.
- Do not retry unsupported URLs or clearly permanent provider errors.
- Do not retry Telegram delivery indefinitely. Existing duplicate-job delivery handoff should continue to work.

Persistence rules:

- SQLite schema changes must be additive.
- Existing jobs, request events, group settings, and cache tables must continue working.
- Metrics failures must be logged and ignored by the user-facing download path.

## Testing

Use focused tests with fake clients and downloaders. Do not make live Instagram, Twitter/X, YouTube, or Telegram calls.

Required test coverage:

- `StateStore` initializes new metrics schema and old databases still migrate.
- Metrics rows can be started, updated, finalized, and summarized.
- `JobManager` records queued, running, completed, failed, and cancelled timings.
- Cache hits mark metrics as cache hits and skip provider download.
- Provider concurrency policy keeps Instagram account-aware and allows separate limits for Twitter/X and YouTube Shorts.
- Instagram fast-path success records fast success and skips fallback.
- Instagram fast-path failure records fast failure and fallback attempt.
- Instagram story URLs bypass fast path.
- Instagram auth-like failures retry with another healthy account and record retry metrics.
- Transient errors retry, unsupported or permanent errors do not.
- `/admin_status` includes compact performance stats and does not expose credentials or proxy secrets.

## Out of Scope

- New user-facing Telegram commands unrelated to performance.
- Live provider integration tests.
- Replacing SQLite with another database.
- Rewriting the downloader architecture.
- Aggressive Instagram concurrency that shares one account across multiple active jobs.
