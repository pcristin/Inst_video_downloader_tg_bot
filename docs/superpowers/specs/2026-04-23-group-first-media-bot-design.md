# Group-First Media Bot Design

Date: 2026-04-23
Repo: `/Users/ozaytsev/Softs/Inst_video_downloader_tg_bot`
Status: Approved for planning, pending implementation approval on written spec

## Summary

Reshape the bot from a simple inline URL responder into a group-first media helper that:

- supports Instagram, Twitter/X, and YouTube Shorts links
- remains responsive when multiple users submit links at nearly the same time
- provides compact, understandable status updates in active group chats
- avoids redundant work through URL normalization, duplicate suppression, and recent-result caching
- stays operable on a modest VPS by using bounded concurrency and lightweight persistence

## Goals

- Improve group chat UX without turning the bot into a separate web product.
- Add first-class support for YouTube Shorts.
- Make concurrent requests feel responsive instead of serialized or stuck.
- Reduce noisy bot chatter in busy groups.
- Keep the architecture maintainable as more providers are added later.

## Non-Goals

- No web dashboard or external admin UI in this phase.
- No premium tiers, user profiles, or advanced download preference matrix.
- No heavy infrastructure such as Redis, Postgres, or separate worker services.
- No full long-form YouTube support in this phase beyond Shorts URLs.

## Product Behavior

### Supported Providers

- Instagram posts, reels, share links, and supported current routes
- Twitter/X status links
- YouTube Shorts URLs

### Group-First UX

When a user posts a supported link in a group:

1. The bot classifies the URL and normalizes it.
2. The bot responds quickly with a compact status message.
3. If workers are busy, the status shows that the job is queued and includes queue position.
4. The same status message is edited through the lifecycle:
   - `queued`
   - `downloading`
   - `uploading`
   - `done`
   - `failed`
5. The bot replies to the original user message with the downloaded media.

### Multi-Link Messages

- Accept multiple supported links in a single message up to a configured cap.
- Each link becomes its own job.
- The bot may summarize admission if too many links are submitted at once.

### Duplicate Suppression

- Normalize URLs before queue insertion.
- If the same normalized URL is already running in the same group, attach the new request to the existing job where practical.
- If the same normalized URL was completed recently and the cached result is still valid, reuse the cached result instead of triggering a fresh provider download.

### Thread Compaction

- Keep only one compact status message per accepted job.
- Avoid extra progress chatter in busy groups.
- Support a quiet-mode group setting that suppresses non-essential intermediate messages.

### Commands

- `/help`: show supported providers and example usage
- `/status`: show a user-safe queue summary and basic availability information
- `/formats`: show supported URL types and provider list
- `/cancel`: let a user cancel their own queued job and attempt to cancel their running job when feasible
- `/stats`: optional lightweight group statistics if enabled

### Command Access Model

Public commands available to any user:

- `/help`
- `/status`
- `/formats`
- `/cancel` for that user's own jobs
- `/stats` when enabled

Owner-only admin commands and settings changes:

- any future commands that mutate bot configuration or queue policy
- detailed operational or diagnostic status views beyond the public `/status`
- toggling quiet mode, duplicate suppression, stats collection, or concurrency overrides

The owner identity should be explicit in configuration, for example `BOT_OWNER_USER_ID`, and checked against the Telegram user id of the command sender. This is preferred over relying on group admin roles because the operational owner of the bot is what matters here, not chat-level moderation permissions.

## Architecture

### Components

#### Telegram Interaction Layer

Responsible for:

- parsing incoming messages and commands
- extracting candidate URLs
- posting and editing status messages
- enforcing chat-level and user-level interaction rules

Primary home: current `telegram_bot.py`, split into smaller focused helpers as needed.

#### Request Parser

Responsible for:

- extracting multiple URLs from a message
- normalizing provider-specific URL variants
- resolving provider type
- rejecting unsupported links before queue admission

This should become a dedicated module rather than keeping provider routing inside the bot handler.

#### Job Manager

Responsible for:

- queue admission
- fairness and concurrency limits
- duplicate suppression and active-job coalescing
- job state transitions
- cancellation
- progress notifications

This is the main new coordination layer.

#### Provider Adapters

Hide provider-specific download logic behind one interface.

Planned adapters:

- Instagram adapter
  - fast extractor first
  - authenticated fallback second
- Twitter/X adapter via `yt-dlp`
- YouTube Shorts adapter via `yt-dlp`

Each adapter should expose a consistent request/response contract so the bot and queue logic do not need provider-specific branching everywhere.

#### Result Cache

Responsible for:

- storing recent normalized URL results
- validating TTL and availability
- reusing prior results where safe

#### State Store

SQLite-backed persistence for:

- recent jobs
- group settings
- recent normalized URL results
- optional lightweight group statistics

## Concurrency Model

### Principles

- Message handling must remain fast even while downloads are running.
- Downloads should run in bounded background tasks.
- Parallelism must be conservative by default because the VPS is modest and providers can be rate-sensitive.

### Default Limits

- global concurrent jobs: `3`
- per-chat concurrent jobs: `2`
- per-user active jobs: `1`

These must be configurable through settings.

### Job Lifecycle

Each job moves through explicit states:

- `queued`
- `running`
- `uploading`
- `completed`
- `failed`
- `cancelled`

### Why Current State Must Change

The current downloader keeps shared mutable state such as:

- one downloader-level Instagram client
- one downloader-level last-download timestamp
- one mutable current account in account management flow

That model does not scale cleanly under concurrent jobs. The redesign should make provider execution job-scoped and account usage lease-based.

## Instagram Account And Proxy Handling

### Account Leasing

For authenticated Instagram fallback:

- a job requests a temporary lease on an available account
- the lease includes the assigned account and proxy for that job
- when the job completes, the lease is returned
- if the job encounters auth or challenge failure, the leased account is marked temporarily unavailable and removed from immediate rotation

### Benefits

- avoids race conditions around one shared current account
- supports concurrent fallback jobs across multiple accounts
- keeps proxy affinity tied to the account being used by the job

## Persistence Model

Use SQLite for lightweight local persistence.

### Tables

#### `jobs`

Store:

- job id
- chat id
- user id
- original URL
- normalized URL
- provider
- status
- queue timestamps
- execution timings
- error class

Retention can be short and bounded.

#### `group_settings`

Store:

- chat id
- quiet mode enabled
- duplicate suppression enabled
- optional local overrides for concurrency caps
- stats enabled

All writes to group settings must be restricted to the configured bot owner.

#### `recent_results`

Store:

- normalized URL
- provider
- cached metadata
- local file path or regenerated-send reference
- created at
- expires at
- cache status

#### `group_stats`

Keep this intentionally lightweight:

- successful downloads count
- failed downloads count
- top users by completed requests
- top providers or domains

## Feature Flags And Settings

Add configuration flags for safe rollout:

- `QUEUE_MANAGER_ENABLED`
- `RESULT_CACHE_ENABLED`
- `GROUP_STATS_ENABLED`
- `YOUTUBE_SHORTS_ENABLED`
- `DUPLICATE_SUPPRESSION_ENABLED`
- `GLOBAL_MAX_CONCURRENT_JOBS`
- `CHAT_MAX_CONCURRENT_JOBS`
- `USER_MAX_ACTIVE_JOBS`
- `RECENT_RESULT_TTL_SECONDS`
- `MAX_LINKS_PER_MESSAGE`
- `BOT_OWNER_USER_ID`

These allow gradual rollout without destabilizing the bot.

## Provider Scope

### Instagram

- Preserve current fast extractor as primary path where supported.
- Preserve authenticated fallback for protected or fast-path-failure cases.
- Keep existing account rotation and challenge handling logic, but move account ownership to job-scoped leasing.

### Twitter/X

- Preserve existing `yt-dlp` approach.
- Route through the common provider adapter interface.

### YouTube Shorts

- Add a dedicated adapter backed by `yt-dlp`.
- Restrict scope to Shorts-style URLs in this phase.
- Reuse existing media packaging and Telegram send flow.

## Error Handling

Errors shown to users should be short and actionable. Internal logs can stay detailed.

User-facing categories:

- unsupported link
- provider unavailable
- rate-limited
- authentication/session expired
- timeout
- upload failed
- file too large or unsupported media result

Operational rules:

- one failed job must not block unrelated jobs
- provider-specific failures remain isolated
- if a valid cached result exists and fresh retrieval fails, reuse of cached result is allowed when safe and enabled

## VPS Resource Posture

This design is intentionally single-process and conservative:

- Telegram handling remains in-process
- SQLite replaces external database needs
- bounded concurrency limits CPU, network, and memory pressure
- `yt-dlp` and Telegram upload remain the main resource consumers

The initial rollout should prefer safe defaults over peak throughput.

## Rollout Plan

1. Introduce request parsing, URL normalization, and provider abstraction.
2. Introduce the job manager with bounded concurrency.
3. Add compact editable status messages and queue visibility.
4. Add recent-result cache and duplicate suppression.
5. Add YouTube Shorts support through the common provider layer.
6. Add `/help`, `/status`, `/formats`, and `/cancel`.
7. Add optional `/stats` and quiet-mode group settings.

## Testing Strategy

Add or expand tests for:

- URL extraction and normalization
- provider routing
- duplicate coalescing behavior
- cache hit, miss, and expiry behavior
- queue fairness and concurrency caps
- command handling for `/status`, `/formats`, and `/cancel`
- concurrent request handling from multiple users in the same chat
- YouTube Shorts adapter behavior

## Success Criteria

- Multiple users in a group can submit links nearly simultaneously without the bot feeling blocked.
- Users get concise and understandable progress and failure feedback.
- Duplicate reposts in groups are faster and less noisy.
- Instagram, Twitter/X, and YouTube Shorts all work through one coherent request pipeline.
- The bot remains stable on a modest VPS with conservative concurrency settings.
