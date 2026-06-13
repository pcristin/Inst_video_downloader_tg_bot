# Architecture Refactor Design

Date: 2026-06-13

## Context

This repository is a Python Telegram bot that downloads Instagram, X/Twitter, and YouTube media. The indexed graph shows `instagram_video_bot` as the core package, with most tests driving it through service-level fakes. The baseline suite passes: `330 passed in 31.71s`.

The largest coordination files are:

- `src/instagram_video_bot/services/telegram_bot.py`: 2029 lines
- `src/instagram_video_bot/services/state_store.py`: 1786 lines
- `src/instagram_video_bot/services/video_downloader.py`: 766 lines

`TelegramBot` currently owns command handlers, direct-message request submission, inline monetization and delivery, shared job coordination, provider metrics, cache cleanup shims, owner checks, and Telegram runtime startup. That makes it the best first refactor target because its responsibilities are mixed but heavily covered by tests.

## Approaches Considered

### Approach A: Extract Telegram Workflows Incrementally

Move cohesive Telegram workflows out of `telegram_bot.py` while keeping `TelegramBot` as the public facade registered by `telegram_wiring.py`. Start with command/admin helpers and direct-message request submission, then inline delivery and payments if the first slices stay clean.

Trade-off: This keeps compatibility and minimizes blast radius, but it requires temporary delegation methods while tests and callers still instantiate `TelegramBot`.

### Approach B: Split Persistence Repositories First

Break `state_store.py` into focused repositories for group settings, jobs, inline subscriptions, cache, metrics, and notifications.

Trade-off: This would improve persistence boundaries, but it touches many database methods and migrations at once. It carries higher regression risk and is harder to live-test through the existing Telegram fake flows.

### Approach C: Rebuild Provider Download Dispatch

Introduce provider-specific downloader classes behind a registry so Instagram, Twitter/X, and YouTube dispatch no longer lives in one `VideoDownloader` class.

Trade-off: This would clarify provider ownership, but the Telegram orchestration bottleneck would remain. It also risks changing download behavior before the app shell is cleaner.

## Selected Design

Use Approach A first. Preserve `TelegramBot` as the runtime facade and handler registration target, but move cohesive behavior into small collaborators under `src/instagram_video_bot/services/telegram/`.

Initial slices:

1. Extract group/admin command operations into a command service.
2. Extract direct message request submission into a request intake service.
3. Extract owner/admin authorization helpers into a focused access service if the first two slices leave duplicated owner logic.

Each collaborator receives the existing dependencies explicitly: `StateStore`, `JobManager`, and small callback functions where it must call back into `TelegramBot` for existing private behavior. This avoids a broad constructor rewrite and keeps tests behavior-preserving.

## Boundaries

`TelegramBot` remains responsible for:

- Owning runtime state such as active request tasks and request contexts.
- Registering handlers through `telegram_wiring.py`.
- Delegating update handling to focused services.
- Starting polling through `run()`.

New Telegram services are responsible for:

- Implementing workflow-specific behavior with explicit dependencies.
- Avoiding direct application startup or handler registration.
- Returning data or invoking narrow callbacks instead of reaching across the whole bot object where practical.

No database schema changes are planned for the first architecture pass.

## Testing and Live Verification

After each significant slice:

- Run the focused test file for the changed behavior.
- Run the full suite with `uv run pytest -q`.
- Run at least one live-style local flow through fake Telegram updates and provider doubles.
- If safe credentials are available and the code path changed justifies it, run a real startup or integration check without printing secrets.

The local `.env`, `accounts.txt`, and `.venv` exist, but secrets must not be printed. Real Telegram/Instagram network checks are optional and should only run if they do not send unexpected messages or mutate production accounts.

## Commit Strategy

Commit after every significant verified slice:

- Design/spec commit.
- One commit per behavior-preserving extraction.
- Final cleanup commit if needed.

Each commit must follow a self-review pass and include the verification commands run.

