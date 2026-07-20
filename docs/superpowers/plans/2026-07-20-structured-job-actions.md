# Structured Job Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed job/failure state and safe inline Cancel/Retry controls while preserving the bot's current single-process architecture.

**Architecture:** A new focused `job_states` module owns stable state values and failure classification. SQLite stores retry metadata, `JobManager` remains the sole request/job coordinator, and a small Telegram job-actions module owns callback payloads and keyboards. Retry creates a new request from persisted normalized-link data and reuses the existing status message.

**Tech Stack:** Python 3.11, python-telegram-bot 22.x, asyncio, SQLite, pytest, pytest-asyncio.

## Global Constraints

- Keep the existing single-process polling deployment; do not add workers, queues, services, or dependencies.
- Never offer Retry for an ambiguous Telegram delivery outcome.
- Callback authorization must match both the persisted user ID and chat ID.
- Callback data must stay within Telegram's 64-byte limit.
- Existing `/cancel`, inline mode, duplicate suppression, and delivery handoff behavior must remain compatible.
- Every production behavior change must follow a failing-test-first red-green cycle.

---

### Task 1: Typed states and durable failure metadata

**Files:**
- Create: `src/instagram_video_bot/services/job_states.py`
- Modify: `src/instagram_video_bot/services/state_schema.py`
- Modify: `src/instagram_video_bot/services/state_store.py`
- Modify: `src/instagram_video_bot/services/job_manager.py`
- Create: `tests/test_job_states.py`
- Create: `tests/test_state_store_job_actions.py`
- Modify: `tests/test_job_manager.py`

**Interfaces:**
- Produces: `JobState`, `FailureReason`, `FailureStage`, `FailureDetails`, and `classify_failure(error, *, stage, ambiguous_delivery=False)`.
- Produces: `StateStore.get_request_for_action(request_id)` and failure-aware `update_request_status`.
- Produces: optional `retry_of_request_id` on `JobManager.submit`.

- [ ] **Step 1: Write failing model and classifier tests**

```python
def test_job_state_values_remain_storage_compatible():
    assert JobState.QUEUED.value == "queued"
    assert JobState.RUNNING.value == "running"
    assert JobState.COMPLETED.value == "completed"
    assert JobState.FAILED.value == "failed"
    assert JobState.CANCELLED.value == "cancelled"


def test_provider_timeout_is_retryable():
    details = classify_failure(
        TimeoutError("provider timed out"), stage=FailureStage.ACQUISITION
    )
    assert details == FailureDetails(FailureReason.PROVIDER_TIMEOUT, retryable=True)


def test_ambiguous_delivery_is_never_retryable():
    details = classify_failure(
        NetworkError("send timed out"),
        stage=FailureStage.DELIVERY,
        ambiguous_delivery=True,
    )
    assert details.reason is FailureReason.DELIVERY_AMBIGUOUS
    assert details.retryable is False
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest tests/test_job_states.py -q`

Expected: collection fails because `job_states` does not exist.

- [ ] **Step 3: Implement the typed model and compatibility classifier**

```python
class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureReason(str, Enum):
    UNSUPPORTED_URL = "unsupported_url"
    AUTHENTICATION_REQUIRED = "authentication_required"
    MEDIA_UNAVAILABLE = "media_unavailable"
    FILE_TOO_LARGE = "file_too_large"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TELEGRAM_DELIVERY = "telegram_delivery"
    DELIVERY_AMBIGUOUS = "delivery_ambiguous"
    UNKNOWN = "unknown"


class FailureStage(str, Enum):
    ACQUISITION = "acquisition"
    DELIVERY = "delivery"


@dataclass(frozen=True)
class FailureDetails:
    reason: FailureReason
    retryable: bool
```

Use exception types first and compatibility text matching second. Explicitly
short-circuit ambiguous delivery to `DELIVERY_AMBIGUOUS`.

- [ ] **Step 4: Write failing persistence tests**

```python
def test_request_failure_metadata_and_retry_link_survive_reopen(tmp_path):
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    store.create_job("job-1", 10, "https://x.com/u/status/1", "twitter", "queued")
    store.create_request(
        request_id="request-1",
        job_id="job-1",
        chat_id=10,
        user_id=20,
        user_label="User",
        provider="twitter",
        normalized_url="https://x.com/u/status/1",
        status="queued",
    )
    store.update_request_status(
        "request-1",
        "failed",
        failure_reason="provider_timeout",
        retryable=True,
    )
    row = store.get_request_for_action("request-1")
    assert row["failure_reason"] == "provider_timeout"
    assert row["retryable"] == 1

    store.create_request(
        request_id="request-2",
        job_id="job-1",
        chat_id=10,
        user_id=20,
        user_label="User",
        provider="twitter",
        normalized_url="https://x.com/u/status/1",
        status="queued",
        retry_of_request_id="request-1",
    )
    assert store.get_request_for_action("request-2")["retry_of_request_id"] == "request-1"
```

- [ ] **Step 5: Run the persistence test and verify RED**

Run: `uv run pytest tests/test_state_store_job_actions.py -q`

Expected: failure because the columns and method do not exist.

- [ ] **Step 6: Add the additive schema migration and store APIs**

Add `failure_reason`, `retryable`, and `retry_of_request_id` to the create-table
statement and to the idempotent migration section. Extend `create_request` and
`update_request_status` with keyword-only optional metadata. Add this lookup:

```python
def get_request_for_action(self, request_id: str) -> sqlite3.Row | None:
    with self._lock:
        return self._conn.execute(
            """
            SELECT r.*, j.normalized_url AS job_normalized_url,
                   j.provider AS job_provider
            FROM request_events AS r
            JOIN jobs AS j ON j.job_id = r.job_id
            WHERE r.request_id = ?
            """,
            (request_id,),
        ).fetchone()
```

The normalized URL is the safe retry source; no additional URL column is added.

- [ ] **Step 7: Write failing JobManager propagation tests**

Verify that a failed executor stores a typed failure on `SharedJob`, marks its
active requests with reason/retryable metadata, and that `retry_of_request_id`
is passed to `create_request` for new retry submissions.

- [ ] **Step 8: Run the JobManager tests and verify RED**

Run: `uv run pytest tests/test_job_manager.py -q`

Expected: new assertions fail because typed failure propagation is absent.

- [ ] **Step 9: Implement JobManager propagation and verify GREEN**

Use `JobState` values in new code without changing stored strings. Add
`failure: FailureDetails | None` to `SharedJob`. Classify acquisition failures
in `_run_job`, persist metadata for active requesters, and accept
`retry_of_request_id: str | None = None` in `submit`.

Run: `uv run pytest tests/test_job_states.py tests/test_state_store_job_actions.py tests/test_job_manager.py -q`

Expected: all selected tests pass.

- [ ] **Step 10: Commit Task 1**

```bash
git add src/instagram_video_bot/services/job_states.py \
  src/instagram_video_bot/services/state_schema.py \
  src/instagram_video_bot/services/state_store.py \
  src/instagram_video_bot/services/job_manager.py \
  tests/test_job_states.py tests/test_state_store_job_actions.py tests/test_job_manager.py
git commit -m "feat: persist structured job failures"
```

### Task 2: Status stages and inline action keyboards

**Files:**
- Create: `src/instagram_video_bot/services/telegram/job_actions.py`
- Modify: `src/instagram_video_bot/services/telegram_status.py`
- Modify: `src/instagram_video_bot/services/chaos_text.py`
- Create: `tests/test_telegram_job_actions.py`
- Modify: `tests/test_chaos_text.py`

**Interfaces:**
- Produces: `JobAction`, `build_job_action_data`, `parse_job_action_data`, `cancel_keyboard`, and `retry_keyboard`.
- Produces: markup-aware `edit_status_message` and `safe_edit_text`.
- Produces: localized preparing/sending/failure-reason text.

- [ ] **Step 1: Write failing callback payload and keyboard tests**

```python
def test_cancel_keyboard_uses_compact_request_callback():
    markup = cancel_keyboard("a" * 32, language_code="en")
    button = markup.inline_keyboard[0][0]
    assert button.text == "Cancel"
    assert button.callback_data == f"job:cancel:{'a' * 32}"
    assert len(button.callback_data.encode()) <= 64


def test_retry_callback_round_trip():
    data = build_job_action_data(JobAction.RETRY, "request-1")
    assert parse_job_action_data(data) == (JobAction.RETRY, "request-1")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_telegram_job_actions.py -q`

Expected: collection fails because `telegram.job_actions` does not exist.

- [ ] **Step 3: Implement compact payload parsing and localized keyboards**

Reject unknown actions, empty IDs, IDs outside `[A-Za-z0-9_-]`, and payloads
over 64 UTF-8 bytes. Return `None` for invalid callback data.

- [ ] **Step 4: Write failing stage/error copy and markup-edit tests**

Cover English and Russian preparing/sending text, typed failure messages, Retry
labels, and preservation/removal of reply markup during edits.

- [ ] **Step 5: Run tests and verify RED**

Run: `uv run pytest tests/test_chaos_text.py tests/test_telegram_job_actions.py -q`

Expected: failures for missing text and markup support.

- [ ] **Step 6: Implement stage text and markup-aware edits**

Only pass `reply_markup` to `Message.edit_text` when explicitly supplied, so
existing fake-message signatures and callers remain compatible. Use a sentinel
to distinguish "leave unchanged" from "remove keyboard".

- [ ] **Step 7: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_chaos_text.py tests/test_telegram_job_actions.py -q`

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/instagram_video_bot/services/telegram/job_actions.py \
  src/instagram_video_bot/services/telegram_status.py \
  src/instagram_video_bot/services/chaos_text.py \
  tests/test_telegram_job_actions.py tests/test_chaos_text.py
git commit -m "feat: add job action keyboards"
```

### Task 3: Authorized Cancel and durable Retry callbacks

**Files:**
- Modify: `src/instagram_video_bot/services/telegram/request_intake.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `src/instagram_video_bot/services/telegram_wiring.py`
- Create: `tests/test_telegram_job_actions_integration.py`
- Modify: `tests/test_telegram_bot_media_send.py`

**Interfaces:**
- Consumes: persisted action row, typed failure metadata, and job-action keyboards.
- Produces: `TelegramRequestIntake.submit_parsed_link(...)` shared by original and retry intake.
- Produces: `TelegramBot.job_action_callback_handler(update, context)`.

- [ ] **Step 1: Write failing initial-status Cancel test**

Submit a normal supported link and assert the status reply contains
`job:cancel:<request_id>` while the job is queued/running.

- [ ] **Step 2: Run test and verify RED**

Run: `uv run pytest tests/test_telegram_job_actions_integration.py -k cancel_keyboard -q`

Expected: failure because initial submission has no reply markup.

- [ ] **Step 3: Extract reusable parsed-link submission and add Cancel markup**

The helper must retain rate limiting, group settings, duplicate suppression,
request-context creation, task cleanup, and existing status copy. It accepts an
optional existing status message and `retry_of_request_id`; initial submission
replies, while retry edits and reuses the existing message.

- [ ] **Step 4: Write failing callback authorization and cancellation tests**

Cover owner success, wrong-user rejection, wrong-chat rejection, stale request,
and joined-request cancellation without cancelling remaining requesters.

- [ ] **Step 5: Run callback tests and verify RED**

Run: `uv run pytest tests/test_telegram_job_actions_integration.py -k job_action -q`

Expected: failure because the handler and registration do not exist.

- [ ] **Step 6: Implement Cancel callback and register its handler**

Register `CallbackQueryHandler(bot.job_action_callback_handler,
pattern=r"^job:(?:cancel|retry):[A-Za-z0-9_-]+$")` before the generic message
handler. Always answer the callback. On success, edit the status to cancelled
and remove its keyboard.

- [ ] **Step 7: Write failing durable Retry tests**

Create a failed retryable request in SQLite, construct a fresh bot instance to
prove no in-memory request context is required, tap Retry as the owner, and
assert a new request is submitted with the same normalized URL and
`retry_of_request_id`. Also verify permanent and ambiguous failures do not
resubmit.

- [ ] **Step 8: Run retry tests and verify RED**

Run: `uv run pytest tests/test_telegram_job_actions_integration.py -k retry_action -q`

Expected: failure because Retry behavior is absent.

- [ ] **Step 9: Implement Retry callback and verify GREEN**

Load the action row, verify user/chat/status/retryable, parse the stored
normalized URL through `RequestParser.extract_supported_links`, and call the
shared submission helper with the existing status message. A successful retry
replaces Retry with a new Cancel callback.

Run: `uv run pytest tests/test_telegram_job_actions_integration.py tests/test_telegram_bot_media_send.py -q`

Expected: all selected tests pass.

- [ ] **Step 10: Commit Task 3**

```bash
git add src/instagram_video_bot/services/telegram/request_intake.py \
  src/instagram_video_bot/services/telegram_bot.py \
  src/instagram_video_bot/services/telegram_wiring.py \
  tests/test_telegram_job_actions_integration.py tests/test_telegram_bot_media_send.py
git commit -m "feat: add cancel and retry callbacks"
```

### Task 4: Delivery-stage integration and regression verification

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `tests/test_telegram_bot_media_send.py`
- Modify: `tests/test_telegram_job_actions_integration.py`

**Interfaces:**
- Consumes: typed classification, keyboards, and markup-aware status edits.
- Produces: downloading/preparing/sending status transitions and safe failure actions.

- [ ] **Step 1: Write failing stage-transition tests**

Assert a successful request edits the same status through downloading,
preparing, and sending before deletion. Assert Cancel disappears when sending
starts.

- [ ] **Step 2: Write failing safe-failure action tests**

Assert provider timeout exposes Retry, unsupported input does not, definite
delivery rejection follows its classification, and ambiguous `NetworkError`
delivery never exposes Retry.

- [ ] **Step 3: Run tests and verify RED**

Run: `uv run pytest tests/test_telegram_job_actions_integration.py tests/test_telegram_bot_media_send.py -q`

Expected: new stage/action assertions fail.

- [ ] **Step 4: Implement minimal stage and failure integration**

Update status immediately before staging and immediately before the user send.
On each exception path, classify with the correct stage and ambiguity flag,
persist through `mark_request_failed`, and attach Retry only when explicitly
retryable.

- [ ] **Step 5: Run focused and related regression suites**

Run: `uv run pytest tests/test_job_states.py tests/test_job_manager.py tests/test_state_store_job_actions.py tests/test_telegram_job_actions_integration.py tests/test_telegram_bot_media_send.py tests/test_telegram_bot_true_inline.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Run the full verification suite twice**

Run twice: `uv run pytest -q tests`

Expected: all tests pass twice; only the three pre-existing PTB deprecation warnings are allowed.

- [ ] **Step 7: Run static repository checks**

```bash
git diff --check
uv run black --target-version py311 --check src tests
uv run isort --check-only src tests
```

Expected: both commands exit zero.

- [ ] **Step 8: Commit Task 4**

```bash
git add src tests
git commit -m "feat: expose structured download progress"
```

- [ ] **Step 9: Final branch review**

Inspect `git diff main...HEAD`, confirm the design scope is fully covered, no
secret/config material was added, and the worktree contains no unrelated files.
