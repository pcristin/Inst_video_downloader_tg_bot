# Structured Job Actions Design

## Goal

Reduce user-visible download failures by giving every request a typed state and
typed failure reason, then exposing safe inline Cancel and Retry actions on the
single transient status message. Keep the existing single-process polling bot,
SQLite store, and in-memory execution model.

## Scope

Included:

- typed job states with the existing persisted values: `queued`, `running`,
  `completed`, `failed`, and `cancelled`;
- typed failure reasons and an explicit retryable flag;
- durable retry metadata linked to the failed request;
- a Cancel button while a request is queued or running;
- a Retry button after a retryable acquisition or safe delivery failure;
- stage-specific status text for downloading, preparing, and sending;
- callback authorization so only the original requester in the original chat
  can cancel or retry;
- immediate resubmission of the same normalized link when Retry is pressed;
- continued support for the existing `/cancel` command.

Excluded:

- worker processes, Redis, Celery, or additional services;
- automatic retries beyond the existing provider retry policy;
- automatic replay after restart;
- replay of Telegram sends whose outcome is ambiguous;
- changes to Telegram inline-mode delivery and payment callbacks.

## Architecture

Add a focused `job_states` module containing `JobState`, `FailureReason`, and a
`FailureDetails` value object. It owns exception-to-failure classification and
the retry-safety decision. Job orchestration continues to live in `JobManager`,
while Telegram rendering and callback handling remain separate from provider
execution.

The callback payload contains only an action and request ID:
`job:cancel:<request_id>` or `job:retry:<request_id>`. Both fit Telegram's
callback-data limit. Retry data is loaded from SQLite by joining the persisted
request and job records rather than embedding a URL in callback data.

## Data Model

The `request_events` table gains:

- `failure_reason TEXT`;
- `retryable INTEGER NOT NULL DEFAULT 0`;
- `retry_of_request_id TEXT`.

Existing databases are migrated with the project's idempotent
`add_column_if_missing` mechanism. A retry creates a new request and job record;
it never rewrites the failed attempt. `retry_of_request_id` preserves the
attempt chain for diagnostics.

`JobState` values remain byte-for-byte compatible with existing stored status
strings and statistics queries.

## Failure Model

Failure classification produces a stable reason, a retryable boolean, and a
user-facing message key. Initial reasons are:

- `unsupported_url` — permanent;
- `authentication_required` — permanent for the requester;
- `media_unavailable` — permanent;
- `file_too_large` — permanent;
- `provider_rate_limited` — retryable;
- `provider_timeout` — retryable;
- `provider_unavailable` — retryable;
- `telegram_delivery` — retryable only when Telegram definitely rejected the
  send before acceptance;
- `delivery_ambiguous` — never retryable, preventing duplicate media;
- `unknown` — retryable for acquisition failures and non-retryable for
  ambiguous delivery.

Classification prefers exception types and explicit failure metadata. Text
matching is retained only as a compatibility fallback for provider libraries
that expose no structured error type.

## User Flow

1. A supported link creates a persisted request and a status message with a
   Cancel button.
2. The same status message is edited through queued, downloading, preparing,
   and sending stages. Cancel remains present until delivery begins.
3. Cancel verifies callback ownership, deactivates the request, cancels the
   shared job when no requesters remain, edits the status to cancelled, and
   removes the keyboard.
4. A failure is classified and persisted. Retryable failures display Retry;
   permanent or ambiguous failures have no action.
5. Retry verifies ownership and failed/retryable state, parses the persisted
   normalized URL, creates a fresh request linked to the old request, and reuses
   the existing status message with a new Cancel callback.
6. Success deletes the transient status message as today.

Repeated or stale callbacks are idempotent: they receive a short callback alert
and do not create duplicate work.

## Error Handling and Security

- The callback user ID and chat ID must match the persisted request.
- Retry is accepted only when the request is failed and marked retryable.
- Cancel is accepted only while the in-memory request is active.
- Unsupported or no-longer-parseable persisted URLs fail closed.
- Callback queries are always answered so Telegram does not leave a spinner.
- Ambiguous Telegram delivery never exposes Retry.

## Testing

Tests follow red-green-refactor cycles and cover:

- stable enum values and failure classification;
- schema migration and persisted failure/retry linkage;
- Cancel and Retry keyboard rendering;
- callback ownership and stale-callback rejection;
- cancel of a sole requester and a joined requester;
- immediate retry using the same normalized URL and existing status message;
- permanent failures without Retry;
- ambiguous delivery without Retry;
- stage-specific status edits;
- unchanged `/cancel`, duplicate suppression, delivery handoff, inline mode,
  and full-suite behavior.

## Rollout

The migration is additive and backward compatible. No new configuration or
dependency is required. Deployment uses the existing Docker Compose flow. If
rollback occurs, older code ignores the added nullable/defaulted columns.
