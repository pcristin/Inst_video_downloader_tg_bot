# Maximum Container Isolation Design

## Context

The bot and Local Telegram Bot API already run as non-root users with read-only
root filesystems, all Linux capabilities dropped, `no-new-privileges`, PID
limits, private service networking, separate egress bridges, log rotation, and
no published Local Bot API port. Telegram `api_id` and `api_hash` are mounted
only into the Local Bot API service.

The remaining repository-contained exposure is concentrated in four areas:

1. `BOT_TOKEN` is loaded from `.env`, so Docker stores it in the bot container's
   environment metadata.
2. Startup and account-state logs include Instagram usernames and proxy
   endpoints that are unnecessary for routine operation.
3. External image tags are not fully pinned to immutable digests.
4. Runtime images may contain tools and writable execution locations that are
   not required by the running services.

Public Git history also contains old Instagram credentials. Credential rotation
is an external prerequisite for rewriting that history and is not part of an
automatic repository deployment.

## Security Objective

Minimize credential exposure, runtime tooling, writable execution surfaces, and
cross-service access while preserving Telegram inline operation, 500 MiB local
uploads, unauthenticated-first public Instagram extraction, and current recovery
workflows.

## Architecture

### Credential Boundaries

- Add a root-protected `secrets/telegram_bot_token` file and mount it read-only
  only into the bot service.
- Add `BOT_TOKEN_FILE` support to settings. The application reads the file
  directly and does not require `BOT_TOKEN` in container environment metadata.
- Remove `BOT_TOKEN` from the deployed `.env` after the secret file has been
  created and validated atomically.
- Keep Telegram `api_id` and `api_hash` mounted only into the Local Bot API.
- Do not log credential values, credential file contents, or token-bearing URLs.
- Treat host root and Docker-daemon access as trusted: either can still inspect
  running process memory and mounted secrets.

### Logging Boundaries

- Replace per-account startup logs with aggregate account and proxy counts.
- Remove Instagram usernames and proxy endpoints from routine log records.
- Log only stable failure categories and aggregate counts for challenged,
  replacement-required, reset, and available accounts.
- Preserve detailed account status only in explicitly authorized owner-facing
  Telegram commands, not container logs.
- Add tests that inject unique usernames, passwords, TOTP values, and proxy
  endpoints and prove none enter captured logs.

### Image and Process Boundaries

- Pin every external `FROM` image by immutable digest while retaining a readable
  version tag.
- Keep production dependencies in `/app/.venv`; do not restore global Python
  installers.
- Inventory shell, package-manager, network-client, and compiler artifacts in
  each final image. Remove an artifact only when application startup, health
  checks, media processing, and shutdown work without it.
- Prefer exec-form entrypoints and health checks. A shell-free Local Bot API
  entrypoint or health probe is acceptable only if implemented as a small,
  auditable compiled helper and tested for secret redaction and signal handling.
- Do not remove shared libraries merely to hide scanner package metadata.
  Vulnerability counts must reflect the actual runtime components.

### Filesystem and Network Boundaries

- Retain read-only root filesystems and narrowly scoped writable bind mounts.
- Add `nosuid`, `nodev`, and `noexec` to writable temporary mounts where live
  media tooling proves compatible. Execution-required locations remain
  read-only.
- Keep the bot and API together only on the internal Telegram API bridge.
- Keep their egress bridges separate. Destination-restricted egress requires
  host firewall policy and is excluded from this repository-contained change.
- Continue publishing no Local Bot API host port.

## Data Flow

1. The host places each Telegram credential in its root-protected secret file.
2. Compose mounts the bot token only into the bot and application credentials
   only into the Local Bot API.
3. The bot reads its token during settings initialization and communicates with
   the API over the internal bridge.
4. The Local Bot API reads its application credentials at process startup and
   communicates with Telegram over its dedicated egress bridge.
5. Downloaded media is written only to bounded writable media paths and is
   uploaded through the Local Bot API.

## Error Handling

- Missing, unreadable, empty, or malformed secret files fail startup with the
  secret path and error category, never the value.
- Migration from `.env` validates the destination file before removing the old
  variable. Failure leaves the original configuration intact.
- Image minimization proceeds one artifact class at a time. Any failed startup,
  health, download, upload, or shutdown test restores that class and records it
  as required runtime surface.
- Network and mount restrictions are applied independently so regressions have
  a single attributable cause.

## Verification

- Follow test-driven development for settings, log redaction, Compose policy,
  entrypoint behavior, and filesystem options.
- Run the complete pytest suite and Compose configuration validation.
- Build both images from pinned inputs.
- Verify non-root users, read-only roots, dropped capabilities, security options,
  PID limits, mounts, networks, environment key names, and unpublished ports
  from live Docker inspection.
- Verify token-safe `getMe`, bot health, Local Bot API health, and a public
  unauthenticated Instagram regression path.
- Scan exported image tars without mounting the Docker socket. Report both total
  and fixable findings and retain no image export or scanner cache afterward.
- Re-scan the staged patch and all Git objects for current Telegram credentials.

## Rollout

1. Add tests and code/config changes without altering live credentials.
2. Build and test the final images.
3. Atomically create `secrets/telegram_bot_token`, remove `BOT_TOKEN` from
   `.env`, and reconcile the stack.
4. Verify both services and Telegram calls before declaring migration complete.
5. Commit only source, configuration, documentation, and tests. Never stage
   `.env`, `accounts.txt`, sessions, state directories, or `secrets/`.

## Exclusions and Follow-up

- Rootless Docker migration, host firewall egress allowlists, and a dedicated VM
  are stronger host-level controls but require a separate maintenance design.
- Challenged Instagram accounts require recovery or replacement outside this
  repository.
- Rotate all Instagram credentials exposed by commits `8a97370` and `728bbe4`
  before a coordinated `git filter-repo` rewrite and force push.
