# Local Telegram Bot API Design

## Goal

Route all bot traffic through a self-hosted Telegram Bot API server so the bot
can upload videos up to an application-enforced 500 MB limit, while bounding
local disk usage and retaining a safe cloud-API fallback before migration.

## Selected Approach

Run Telegram's official `telegram-bot-api` implementation as a second service
in `docker-compose.local-api.yml`. Build it from a pinned upstream source
revision instead of using an unpinned third-party runtime image. Keep the
service private to the Compose network and enable Telegram's `--local` mode.
The default `docker-compose.yml` remains a valid cloud-only deployment.

The bot application remains cloud-compatible by default. Local mode is enabled
only when `TELEGRAM_LOCAL_MODE=true`; this flag configures the Python Telegram
client with the internal API and file URLs and enables local-path media inputs.

Alternatives rejected:

- An unpinned community image is easier to deploy but adds unnecessary supply
  chain risk for a service receiving the bot token and Telegram API credentials.
- Continuing with the cloud API plus aggressive transcoding avoids migration,
  but it does not meet the requirement to send source videos above 50 MB.
- Running the server directly on the host complicates lifecycle management and
  disk isolation compared with the existing Compose deployment.

## Configuration

Add the following settings:

- `TELEGRAM_LOCAL_MODE`, default `false`.
- `TELEGRAM_BOT_API_BASE_URL`, default
  `http://telegram-bot-api:8081/bot`.
- `TELEGRAM_BOT_API_BASE_FILE_URL`, default
  `http://telegram-bot-api:8081/file/bot`.
- `TELEGRAM_MAX_UPLOAD_BYTES`, default `524288000` (500 MiB).
- `TELEGRAM_LARGE_FILE_CACHE_THRESHOLD_BYTES`, default `52428800` (50 MiB).

The Compose service consumes `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`. These
values remain in the untracked `.env` file and are never committed or logged.
The existing `BOT_TOKEN` continues to authenticate the bot independently.

## Media Data Flow

In cloud mode, media continues to be opened as a binary stream and uploaded by
multipart request. In local mode, the sender and storage-chat stager pass the
absolute `/app/temp/...` path to `python-telegram-bot`. Its local mode converts
that path to a file URI for the Local Bot API server.

Both containers mount the same host `./temp` directory at `/app/temp`. The API
container mounts it read-only. This avoids a second full-size multipart copy and
prevents the API service from modifying downloader output.

Every uncached local media item is checked before sending. A zero-byte file or a
file larger than 500 MiB fails with a permanent, non-retryable delivery error.
The final downloaded file size is authoritative because provider metadata can
be absent or approximate.

After Telegram returns a reusable file ID, files larger than 50 MiB are not
retained as result-cache payloads. Their cache entries retain Telegram file IDs
and metadata, so repeated delivery does not require local media. Smaller files
continue using the existing four-hour cache behavior.

## Disk Bounds

The Local Bot API service uses:

- A persistent named volume for its TDLib database and bot session state.
- A 1 GiB `tmpfs` for its temporary directory.
- The shared `/app/temp` mount in read-only mode.
- Docker log rotation with three 10 MiB files.

The bot accepts at most three global concurrent jobs. At a 500 MiB final-file
limit, separate video/audio inputs and merge output can temporarily require
about 3 GiB in `/app/temp`. Large successful files are removed after file-ID
staging instead of remaining for four hours. The existing expiration cleanup
continues handling ordinary cached files.

The application does not call Telegram `getFile`, so this workflow does not
intentionally download Telegram-hosted media into the API server's persistent
data volume.

## Startup And Failure Behavior

The local Compose override starts the API service before the bot and makes the
bot depend on its HTTP health check. The bot also performs its existing `getMe`
initialization through the configured endpoint; failure leaves the container
unhealthy/restarting instead of silently using the cloud API. The default
cloud-only Compose command is unchanged before migration.

There is no automatic fallback from local to cloud after migration. Automatic
fallback could create two competing Bot API sessions and lose or duplicate
updates. Recovery keeps the local server's persistent state and restarts it.

## Migration

Migration is a deliberate operational step performed only after:

1. `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are present locally.
2. The Local Bot API container is healthy.
3. Unit tests and Compose configuration validation pass.
4. The existing bot container is stopped.

The bot then calls `logOut` once against `https://api.telegram.org`, enables
`TELEGRAM_LOCAL_MODE=true`, and starts against the local endpoint. The local
server is verified with `getMe`, polling, a private-chat link, an inline query,
and an upload larger than 50 MB. Telegram permits immediate local login but
blocks a return to the cloud Bot API for ten minutes after `logOut`.

## Testing

Automated tests cover:

- Settings defaults and environment parsing.
- Cloud and local `ApplicationBuilder` configuration.
- Path inputs in local mode and binary streams in cloud mode.
- The 500 MiB boundary and non-retryable oversize classification.
- Large-file cleanup only after a reusable Telegram file ID is persisted.
- Existing single-media, album, inline staging, retry, and cache behavior.
- `docker compose config` validity.

Live verification is separated from unit tests because `logOut` changes remote
Telegram state. It is performed once, after explicit confirmation at the final
migration checkpoint.
