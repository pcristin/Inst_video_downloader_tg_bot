# Reliable Telegram Delivery Design

## Goal

Reduce user-visible first-attempt failures for short-form media downloads to a sustained 2-5% by separating acquisition from delivery, safely retrying uploads outside user chats, and exposing accurate failure metrics.

## Evidence

- Direct local uploads intentionally disable network retries because an ambiguous Telegram response can mean the user already received media.
- The configured private inline storage chat already supports retryable uploads and durable Telegram file IDs.
- Persisted jobs currently mark download completion even when subsequent delivery times out, understating request failures.
- Historical provider metrics show Instagram acquisition failures above the target, including exhausted account pools and provider timeouts.

## Architecture

1. Introduce a reusable media-staging service that uploads local media to the private storage chat using the existing bounded Telegram retry helper. A successful storage response yields a reusable bot-scoped `file_id`.
2. For direct requests, stage every local media item before the final user delivery and persist each file ID in the existing result cache. Final user delivery uses only stored file IDs and remains non-retried, preserving the no-duplicate guarantee.
3. Persist delivery attempts and make job/request state explicit: acquisition completes when downloadable media is cached or staged; delivery completes only after a final user-chat send succeeds. Record an `unknown` delivery outcome for ambiguous final-send failures.
4. Report download, staging, and user-delivery reliability separately. Use the result to maintain account-pool capacity and target classified Instagram failures rather than masking them as generic download errors.

## Error Handling

- Retry private-storage uploads for `TimedOut`, `NetworkError`, and `RetryAfter` using fresh file handles/media objects on each attempt.
- Do not retry final sends to user chats after ambiguous network errors.
- Persist a delivery-attempt row for every stage result, including duration, error class, and retry count.
- A subsequent same-link request reuses cached/staged file IDs and never re-downloads the media when cached output remains valid.

## Scope

The first implementation increment covers direct single-item and media-group delivery, state/metrics needed to distinguish delivery failures, and automated tests. Account-pool operational changes are measured through the new metrics and handled as a follow-up operating procedure, not mixed into delivery code.

## Verification

- Tests prove a transient storage upload is retried and returns a persisted file ID.
- Tests prove direct final delivery uses the staged file ID and does not retry an ambiguous user-chat network error.
- Tests prove delivery failures do not leave the request marked completed and are separately visible in persistence.
- Focused and full test suites pass.
