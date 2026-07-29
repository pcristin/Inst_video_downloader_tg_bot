# Local Bot API Delivery Permissions Design

## Goal

Restore reliable Telegram media delivery through the Local Bot API while preserving the application limit of `524288000` bytes (500 MiB) for one media file and retaining separate non-root identities for the bot and Local Bot API containers.

## Confirmed Failure

The bot container runs as UID/GID `1000:1000`; the Local Bot API container runs as `10001:10001`. Both see the host `./temp` directory at `/app/temp`, but `./temp` is currently mode `0700` and owned by `1000:1000`. Local Bot API therefore cannot traverse the directory even though individual media files are readable. Path-based local uploads fail with `BadRequest: Can't get stat about the file`.

`python-telegram-bot` 22.8 defines `BadRequest` as a subclass of `NetworkError`. The direct-delivery boundary currently treats every `NetworkError` as an unknown delivery outcome, so the deterministic path-access rejection is incorrectly shown as “Telegram may have delivered the media.”

## Design

### Least-privilege shared media access

Keep both service UIDs unchanged. Add GID `1000` as a supplementary group for the Local Bot API service. Prepare `./temp` as owner/group `1000:1000` with mode `0750`; media remains mounted read-only in the Local Bot API container. Existing and newly created media files remain owned by the bot group and readable by that group. World-writable or world-readable directory permissions are not required.

The Local Bot API startup path must fail closed when `/app/temp` is not readable and traversable by UID `10001` with supplementary GID `1000`. Deployment preparation must create or repair the host directory before Compose starts the services.

### Direct-delivery staging

Direct downloads continue to prefer `TELEGRAM_MEDIA_STORAGE_CHAT_ID` when explicitly configured. When it is absent, direct delivery falls back to the already configured `INLINE_STORAGE_CHAT_ID`.

The bot uploads a local file path to the private storage chat through Local Bot API, obtains a durable Telegram `file_id`, and sends that `file_id` to the requesting user. This keeps the potentially large path-based upload at the storage boundary, where bounded retries are safe, and makes the final user send small and non-retried after an ambiguous transport failure.

No media is copied into memory and no cloud Bot API endpoint is used. Local mode continues to accept one file up to `524288000` bytes. Cloud mode remains capped at 50 MiB.

### Delivery-outcome classification

Introduce one explicit predicate for ambiguous user-send outcomes. Transport timeouts and genuine connection failures remain ambiguous because Telegram may have accepted the request before the response was lost. `BadRequest` is excluded because it is a deterministic API rejection even though it inherits from `NetworkError` in python-telegram-bot.

The direct-delivery state machine uses this predicate instead of a broad `isinstance(error, NetworkError)` check. A Local Bot API stat failure is recorded as a failed user send, receives the ordinary Telegram-delivery failure message, and remains eligible for an explicit user retry. Genuine timeouts keep the duplicate-prevention warning and remain non-retryable.

## Deployment and Recovery

The local deployment preparation creates `./temp` when missing, keeps ownership at `1000:1000`, and sets mode `0750`. Compose grants only supplementary GID `1000` to Local Bot API and keeps the bind mount read-only.

After rebuilding and recreating the services, verification must prove all of the following:

- Both containers remain non-root and retain their distinct primary UIDs.
- Local Bot API can stat and read a bot-owned media file below `/app/temp`.
- Local Bot API cannot write to the read-only media mount.
- The configured application ceiling remains exactly `524288000` bytes.
- A valid MP4 larger than 50 MiB can be uploaded to the private storage chat and returns a Telegram `file_id`.
- The smoke-test Telegram message and local test file are removed afterward.

## Tests

Automated regression coverage will include:

- Compose grants supplementary GID `1000` without changing either primary UID or the read-only mount.
- Local deployment preparation establishes mode `0750` for `./temp`.
- Direct delivery uses an explicit media storage chat when present and otherwise reuses the inline storage chat.
- `BadRequest` is a definite delivery failure rather than an ambiguous outcome.
- `TimedOut` and genuine transport `NetworkError` outcomes remain ambiguous.
- Local-mode validation accepts a sparse file above 50 MiB and at or below `524288000` bytes, while cloud mode and values above the local ceiling are rejected.

## Non-goals

- Removing the Local Bot API deployment.
- Lowering the 500 MiB application ceiling.
- Retrying ambiguous final sends to user chats.
- Running both services under the same UID.
- Broad permission changes such as `0777`.
