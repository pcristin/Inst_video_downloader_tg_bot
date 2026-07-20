# Reliability Stabilization Design

## Scope

This is the first implementation slice of the architecture and reliability
roadmap approved on 2026-07-20. It addresses the currently failing reliability
tests and the smallest production risks that can be corrected without changing
the bot's deployment model.

Included:

- deterministic Instagram provider timeout and cancellation behavior;
- an explicit lifecycle boundary around the shared Instagram executor;
- offline-by-default unit tests for Instagram fallbacks;
- Docker health semantics aligned with supported bot modes;
- crash-safe persistence of `accounts_state.json`;
- a directory-level Docker state mount that permits atomic file replacement.

Deferred to the next slice:

- replacing provider threads with killable worker processes;
- splitting `TelegramBot` and `StateStore`;
- SQLite WAL, migrations, constraints, backups, and a dedicated data volume;
- durable resumption or restart notifications for in-flight jobs.

## Evidence and Root Causes

The baseline command `uv run pytest -q tests` completed with 452 passing,
7 failing, and 2 skipped tests. The failures cluster in
`tests/test_video_downloader_flow.py`.

Two causes interact:

1. The public yt-dlp recovery path now runs before authenticated fallback.
   Existing timeout tests disable the fast extractor but do not replace the
   public recovery adapter, so nominal unit tests can perform real network
   requests. Those requests consume the same class-level executor used by the
   operation under test and make ordering depend on external latency.
2. A stale-worker timer recycles that executor using
   `shutdown(wait=False, cancel_futures=True)`. A queued future can therefore
   become `concurrent.futures.CancelledError` before the async deadline loop
   observes its own timeout. The generic fallback wrapper then turns the empty
   exception string into `DownloadError("Download failed: ")`.

The other confirmed mismatches are independent:

- application startup requires only `BOT_TOKEN` and intentionally supports
  Twitter-only/public-fallback operation, while the Docker health command
  fails when Instagram username/password and a multi-account file are absent;
- `AccountManager._save_state` truncates the live JSON file before writing the
  replacement, so interruption or disk failure can destroy the last good
  account state.

## Approaches Considered

### A. Patch only the immediate symptoms

Mock the public fallback in the seven tests, catch
`concurrent.futures.CancelledError`, remove the credential health check, and
write account state through a temporary file.

This is the smallest change, but executor ownership and cleanup remain implicit.
Future tests or shutdown paths can recreate the same shared-state problem.

### B. Stabilize behavior and introduce an executor lifecycle seam

Apply the behavioral fixes from approach A and add a focused executor-runtime
component that owns submission, stale-generation recycling, and idempotent
shutdown. `VideoDownloader` continues to own download policy and deadline/error
classification, while the runtime owns only thread resources.

Tests inject or reset that runtime and replace every network boundary. The
runtime interface becomes the seam for a process-backed implementation in the
next slice.

This is the recommended approach. It fixes the present failures without
prematurely serializing account/client objects for multiprocessing, and it
reduces the risk of the later process migration.

### C. Move directly to worker processes

Replace the executor immediately with multiprocessing or a separate worker
service. This provides hard termination, but the current operations close over
live account managers, Instagram clients, callbacks, and paths. Designing a
serializable command/result protocol belongs in a dedicated slice with its own
failure and shutdown tests.

## Detailed Design

### Instagram execution runtime

Create a small service responsible for:

- returning the active bounded executor;
- submitting a blocking provider operation;
- atomically retiring only the executor generation that became stale;
- cancelling queued work during retirement without misclassifying it;
- idempotently shutting down the active generation.

`VideoDownloader._run_instagram_sync` will continue to enforce the async
deadline. If a queued future is cancelled because its executor generation was
retired, the runtime will resubmit it once to the fresh generation while using
the original deadline. A running operation is never resubmitted. The method
will raise `InstagramProviderTimeoutError` when the original deadline expires
and preserve `asyncio.CancelledError` when the caller cancels the request. It
must never expose a blank `DownloadError`.

Runtime cleanup will be usable from tests and from a later Telegram
post-shutdown hook. This slice will not attempt to terminate an already-running
thread; the detached-lease/account-retirement behavior remains unchanged.

### Offline test boundary

Provider-flow unit tests will replace the public yt-dlp adapter explicitly.
An autouse guard in the provider-flow test module will fail immediately if an
unconfigured test attempts the public network fallback. Tests that exercise the
fallback will opt in by installing a fake adapter result or exception.

No production-only "testing mode" flag will be added.

### Health contract

The Docker health command represents process/container health, not the
availability of every optional provider.

It will require:

- a configured `BOT_TOKEN`;
- writable temporary storage;
- the sessions directory;
- a readable/writable state database when configured;
- no stale active job beyond the existing threshold.

Missing Instagram credentials, accounts, or cookies will not fail health.
Provider availability remains visible through logs and admin status.

### Atomic account-state persistence

`AccountManager` will serialize state to a uniquely named temporary file in the
same directory as `accounts_state.json`, flush it, call `os.fsync`, and replace
the destination with `os.replace`.

Requirements:

- the old file remains intact if serialization or writing fails;
- temporary files are removed on failure;
- replacement is atomic on the destination filesystem;
- the parent directory is created when necessary;
- existing logging behavior remains, without exposing account credentials.

Docker Compose currently bind-mounts `accounts_state.json` as an individual
file. Linux does not reliably allow `os.replace` to replace a mount point, so
Compose will instead mount `./account-state` at `/app/account-state`.
`ACCOUNT_STATE_FILE` will default to `accounts_state.json` for backward
compatibility and Compose will set it to
`/app/account-state/accounts_state.json`.

The deployment guide will include a one-time migration command that copies an
existing host `accounts_state.json` into
`account-state/accounts_state.json` before restarting the service.

## Testing

Implementation follows red-green-refactor cycles.

Required regression coverage:

- stale executor retirement cannot turn provider timeout into an empty generic
  download error;
- timeout/cancellation tests do not call a real public provider;
- runtime shutdown is idempotent and a new generation can be created;
- Twitter-only/no-Instagram-credential configuration passes health;
- missing `BOT_TOKEN`, unwritable temp storage, missing sessions, broken state
  storage, and stale jobs still fail health;
- a simulated account-state write failure preserves the previous JSON file and
  leaves no temporary file;
- a successful account-state save replaces the file with valid JSON.

The slice is complete only when `uv run pytest -q tests` passes twice in
succession and `docker build -t inst-video-downloader-tg-bot:reliability .`
exits successfully.

## Compatibility and Rollout

No database schema, Telegram commands, or user-visible messages change in this
slice. Deployment remains a single polling bot. The optional
`ACCOUNT_STATE_FILE` environment variable is new; its default preserves
non-Compose behavior.

The health change can convert previously unhealthy Twitter-only/public-fallback
deployments to healthy. It does not make a failing Telegram token healthy.

The executor-runtime seam is internal and retains the current concurrency and
detached-account semantics.
