# Chaos Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-chat opt-in Russian Chaos Mode for Telegram messages, commands, duplicate/cache moments, and stats without changing downloader behavior.

**Architecture:** Extend `StateStore` with one chat setting and one duplicate marker, add a focused `chaos_text.py` renderer for Russian text, and integrate it through `TelegramBot` at existing command and request lifecycle boundaries. Keep provider downloaders and queue execution unchanged.

**Tech Stack:** Python 3.11, python-telegram-bot, SQLite via `sqlite3`, pytest/pytest-asyncio, uv.

---

## File Map

- Create `src/instagram_video_bot/services/chaos_text.py`: Russian text renderer for normal and chaos-mode bot messages.
- Modify `src/instagram_video_bot/services/state_store.py`: persist `chaos_mode_enabled`, record duplicate-joined requests, and expose enriched group stats.
- Modify `src/instagram_video_bot/services/job_manager.py`: pass `joined_existing=True` into persisted duplicate request rows.
- Modify `src/instagram_video_bot/services/telegram_bot.py`: register `/chaos`, translate user-facing text to Russian, use renderer for lifecycle/status/error/stats text, and enforce admin-or-owner permission for `/chaos`.
- Modify `tests/test_telegram_bot_media_send.py`: update Russian expectations and add command/lifecycle tests.
- Create `tests/test_chaos_text.py`: renderer unit coverage.
- Create `tests/test_state_store_chaos.py`: persistence and stats coverage.

## Task 1: Persist Chaos Mode and Duplicate Joins

**Files:**
- Modify: `src/instagram_video_bot/services/state_store.py`
- Modify: `src/instagram_video_bot/services/job_manager.py`
- Create: `tests/test_state_store_chaos.py`

- [ ] **Step 1: Write failing state-store tests**

Create `tests/test_state_store_chaos.py` with:

```python
from src.instagram_video_bot.services.state_store import StateStore


def test_group_settings_include_disabled_chaos_mode_by_default(tmp_path):
    store = StateStore(tmp_path / "state.db")

    settings = store.ensure_group_settings(77)

    assert settings["chaos_mode_enabled"] is False


def test_group_settings_can_enable_chaos_mode(tmp_path):
    store = StateStore(tmp_path / "state.db")

    settings = store.update_group_settings(77, chaos_mode_enabled=True)

    assert settings["chaos_mode_enabled"] is True
    assert store.ensure_group_settings(77)["chaos_mode_enabled"] is True


def test_group_stats_include_cache_hits_duplicate_joins_and_provider_counts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_job("job-1", 77, "https://x.com/a/status/1", "twitter", "queued")
    store.create_request(
        "req-1",
        "job-1",
        77,
        1001,
        "@alice",
        "twitter",
        "https://x.com/a/status/1",
        "queued",
    )
    store.create_request(
        "req-2",
        "job-1",
        77,
        1002,
        "@bob",
        "twitter",
        "https://x.com/a/status/1",
        "queued",
        joined_existing=True,
    )
    store.update_request_status("req-1", "completed", cache_hit=True)
    store.update_request_status("req-2", "completed")

    stats = store.get_group_stats(77)

    assert stats["completed"] == 2
    assert stats["cache_hits"] == 1
    assert stats["duplicate_joins"] == 1
    assert stats["top_providers"] == [("twitter", 2)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state_store_chaos.py -q`

Expected: FAIL because `chaos_mode_enabled` and `joined_existing` are not persisted yet.

- [ ] **Step 3: Implement persistence**

Update `StateStore._initialize()` to add `chaos_mode_enabled INTEGER NOT NULL DEFAULT 0` to `group_settings` and `joined_existing INTEGER NOT NULL DEFAULT 0` to `request_events`, with migration checks for existing databases.

Update `ensure_group_settings()` to return `chaos_mode_enabled`, `update_group_settings()` to allow it, and `create_request()` to accept `joined_existing: bool = False`.

Update `get_group_stats()` to include `cache_hits`, `duplicate_joins`, and existing provider/user aggregates.

Update `JobManager.submit()` so duplicate-suppressed existing jobs call `create_request(..., joined_existing=True)`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_state_store_chaos.py -q`

Expected: PASS.

## Task 2: Add Russian Text Renderer

**Files:**
- Create: `src/instagram_video_bot/services/chaos_text.py`
- Create: `tests/test_chaos_text.py`

- [ ] **Step 1: Write failing renderer tests**

Create `tests/test_chaos_text.py` with:

```python
from src.instagram_video_bot.services.chaos_text import ChaosText, TextContext


def test_normal_submission_text_is_russian():
    text = ChaosText.submission(
        TextContext(provider_label="Instagram", chaos_enabled=False),
        queue_position=1,
        joined_existing=False,
    )

    assert text == "Принял Instagram. Скоро начну скачивать."


def test_chaos_submission_text_is_russian_and_provider_aware():
    text = ChaosText.submission(
        TextContext(provider_label="Twitter/X", chaos_enabled=True),
        queue_position=1,
        joined_existing=False,
    )

    assert "Twitter/X" in text
    assert "шум" in text.lower()


def test_chaos_duplicate_text_is_russian():
    text = ChaosText.submission(
        TextContext(provider_label="Instagram", chaos_enabled=True),
        queue_position=1,
        joined_existing=True,
    )

    assert "уже" in text.lower()
    assert "Instagram" in text


def test_error_text_is_russian_for_rate_limit():
    text = ChaosText.error(RuntimeError("rate limit"), chaos_enabled=False)

    assert "лимит" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_chaos_text.py -q`

Expected: FAIL because `chaos_text.py` does not exist yet.

- [ ] **Step 3: Implement renderer**

Create `chaos_text.py` with `TextContext` and `ChaosText` static/class methods for:

- `help()`
- `formats()`
- `submission()`
- `running()`
- `cancelled()`
- `failed()`
- `unexpected_error()`
- `error()`
- `stats_disabled()`
- `stats()`
- `setting_usage()`
- `setting_updated()`
- `numeric_setting_usage()`
- `numeric_setting_updated()`
- `owner_unconfigured()`
- `owner_required()`
- `chaos_usage()`
- `chaos_status()`
- `chaos_updated()`
- `admin_required()`

Use Russian user-facing strings only. Keep internal method names English.

- [ ] **Step 4: Run renderer tests**

Run: `uv run pytest tests/test_chaos_text.py -q`

Expected: PASS.

## Task 3: Wire Chaos Command and Russian Command Text

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `tests/test_telegram_bot_media_send.py`

- [ ] **Step 1: Write failing command tests**

Add tests proving:

- `/chaos on` enables `chaos_mode_enabled` when the sender is the bot owner.
- `/chaos off` disables it.
- `/chaos status` reports Russian enabled/disabled status.
- non-owner/non-admin group users are rejected with Russian text.
- `/help`, `/formats`, `/status`, and `/stats` produce Russian text.

- [ ] **Step 2: Run selected tests to verify failure**

Run: `uv run pytest tests/test_telegram_bot_media_send.py -q`

Expected: FAIL because command text and `/chaos` are not implemented yet.

- [ ] **Step 3: Implement command integration**

In `TelegramBot`:

- Import `ChaosText` and `TextContext`.
- Add `chaos_command()`.
- Register `CommandHandler("chaos", self.chaos_command)`.
- Implement `_require_chaos_admin(update, context)`:
  - private chat: allow sender
  - bot owner: allow sender
  - group/supergroup: allow Telegram `creator` or `administrator`
  - otherwise reply with Russian admin-required text
- Rewrite `/help`, `/formats`, `/status`, `/stats`, owner guard messages, and generic setting/numeric setting responses through `ChaosText`.

- [ ] **Step 4: Run command tests**

Run: `uv run pytest tests/test_telegram_bot_media_send.py -q`

Expected: PASS.

## Task 4: Wire Lifecycle Chaos Text

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `tests/test_telegram_bot_media_send.py`

- [ ] **Step 1: Write failing lifecycle tests**

Add tests proving:

- normal request submission text is Russian when Chaos Mode is off.
- running status text is Russian when Chaos Mode is off.
- duplicate-joined text is Russian and playful when Chaos Mode is on.
- cache-hit successful flow records a cache hit and uses the Russian status/caption behavior without changing media delivery.
- failure text is Russian and uses the existing error categories.

- [ ] **Step 2: Run selected tests to verify failure**

Run: `uv run pytest tests/test_telegram_bot_media_send.py -q`

Expected: FAIL until lifecycle methods use `ChaosText`.

- [ ] **Step 3: Implement lifecycle integration**

In `handle_message()`, read `chaos_mode_enabled` from group settings and pass it into submission/status rendering.

In `_on_job_state_change()`, render running/cancelled/failed text through `ChaosText`, while preserving quiet-mode and joined-existing running suppression.

In `_await_request()`, render `VideoDownloadError` and unexpected errors through `ChaosText.error()` and `ChaosText.unexpected_error()`.

In `_build_caption_text()`, switch the prefix to Russian-friendly media caption text while keeping length truncation behavior.

- [ ] **Step 4: Run lifecycle tests**

Run: `uv run pytest tests/test_telegram_bot_media_send.py -q`

Expected: PASS.

## Task 5: Full Verification and Commit

**Files:**
- All changed files.

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q`

Expected: 70+ tests pass with only the existing `test_proxies.py::test_all_proxies` return-value warning unless new tests increase the count.

- [ ] **Step 2: Inspect git diff**

Run: `git diff --stat`

Expected: changes are limited to Chaos Mode implementation, tests, and this plan.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git add -f docs/superpowers/plans/2026-04-23-chaos-mode.md
git add src/instagram_video_bot/services/chaos_text.py src/instagram_video_bot/services/state_store.py src/instagram_video_bot/services/job_manager.py src/instagram_video_bot/services/telegram_bot.py tests/test_chaos_text.py tests/test_state_store_chaos.py tests/test_telegram_bot_media_send.py
git commit -m "feat: add russian chaos mode"
```

Expected: commit succeeds on branch `codex/chaos-mode`.

## Self-Review

Spec coverage:

- per-chat opt-in setting: Task 1 and Task 3
- admin-controlled `/chaos`: Task 3
- Russian user-facing text: Task 2, Task 3, Task 4
- event-driven lifecycle rendering: Task 4
- duplicate/cache/stats flavor: Task 1, Task 2, Task 4
- downloader behavior unchanged: Task 4 and Task 5 verification

Placeholder scan:

- No `TBD`, `TODO`, incomplete sections, or intentionally vague "handle later" steps.

Type consistency:

- `chaos_mode_enabled`, `joined_existing`, `ChaosText`, and `TextContext` names are used consistently across tasks.
