# Inline Session Actions and Group Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add owner-authorized, payment-safe Cancel and Retry actions to true inline deliveries and verify ordinary URL recognition in Telegram groups and supergroups.

**Architecture:** Keep direct `job:*` actions unchanged and add a compact `inline-action:*` protocol backed by atomic `inline_sessions` transitions. Persist live delivery stage, retryability, and attempt count so one-time entitlement decisions remain safe across retries, races, and restarts; diagnose Telegram privacy mode separately because withheld group updates cannot be recovered in application code.

**Tech Stack:** Python 3.12, python-telegram-bot, SQLite, asyncio, pytest, pytest-asyncio, uv.

## Global Constraints

- Only the inline session owner may act, including in group and supergroup inline messages.
- One Stars charge buys exactly one confirmed successful delivery of its bound normalized URL.
- Retryable failures retain the one-time claim; safe cancellation and certain terminal failure refund once.
- `inline_edit` is the no-cancel boundary; ambiguous final edits are neither retried nor automatically refunded.
- Subscription accounting records final session outcomes only; promo credit is consumed only on success.
- Callback data must remain within Telegram's 64-byte limit.
- Existing direct-request `job:*` actions and pricing policy must remain unchanged.

---

### Task 1: Inline Action Protocol and Keyboards

**Files:**
- Create: `src/instagram_video_bot/services/telegram/inline_actions.py`
- Create: `tests/test_telegram_inline_actions.py`

**Interfaces:**
- Produces: `InlineAction`, `build_inline_action_data(action, session_token)`, `parse_inline_action_data(data)`, `inline_cancel_keyboard(session_token, language_code)`, and `inline_retry_keyboard(session_token, language_code)`.
- Callback format: `inline-action:<cancel|retry>:<session_token>`.

- [ ] **Step 1: Write failing protocol and keyboard tests**

```python
def test_cancel_keyboard_is_owner_session_scoped_and_compact():
    markup = inline_cancel_keyboard("s" * 24, language_code="en")
    button = markup.inline_keyboard[0][0]
    assert button.text == "Cancel"
    assert button.callback_data == f"inline-action:cancel:{'s' * 24}"
    assert len(button.callback_data.encode()) <= 64


def test_retry_callback_round_trips():
    data = build_inline_action_data(InlineAction.RETRY, "session_1")
    assert parse_inline_action_data(data) == (InlineAction.RETRY, "session_1")


@pytest.mark.parametrize(
    "data",
    ["", "inline-action:unknown:s1", "inline-action:retry:",
     "inline-action:retry:bad.token", f"inline-action:retry:{'s' * 50}"],
)
def test_invalid_inline_action_data_is_rejected(data):
    assert parse_inline_action_data(data) is None
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_telegram_inline_actions.py -q`

Expected: collection fails because `telegram.inline_actions` does not exist.

- [ ] **Step 3: Implement the compact protocol and localized keyboards**

```python
class InlineAction(str, Enum):
    CANCEL = "cancel"
    RETRY = "retry"


def build_inline_action_data(action: InlineAction, session_token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_token):
        raise ValueError("Invalid inline session token")
    data = f"inline-action:{action.value}:{session_token}"
    if len(data.encode()) > 64:
        raise ValueError("Callback data exceeds Telegram's 64-byte limit")
    return data


def parse_inline_action_data(data: str) -> tuple[InlineAction, str] | None:
    match = re.fullmatch(r"inline-action:(cancel|retry):([A-Za-z0-9_-]+)", data)
    if not match or len(data.encode()) > 64:
        return None
    return InlineAction(match.group(1)), match.group(2)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_telegram_inline_actions.py -q`

Expected: all inline action protocol tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/instagram_video_bot/services/telegram/inline_actions.py tests/test_telegram_inline_actions.py
git commit -m "feat: add inline session action protocol"
```

### Task 2: Persisted Inline Session State Machine

**Files:**
- Modify: `src/instagram_video_bot/services/state_schema.py`
- Modify: `src/instagram_video_bot/services/state_store.py`
- Modify: `tests/test_state_store_true_inline.py`

**Interfaces:**
- Produces columns `delivery_stage TEXT`, `failure_retryable INTEGER NOT NULL DEFAULT 0`, and `attempt_count INTEGER NOT NULL DEFAULT 0` on `inline_sessions`.
- Produces `claim_inline_delivery(...) -> str`, `advance_inline_delivery_stage(...) -> bool`, `claim_inline_retry(...) -> str`, `cancel_inline_delivery(...) -> str`, `finish_inline_delivery(...) -> bool`, and `get_claimed_inline_one_time_payment(session_token) -> dict | None`.
- Transition result strings are `claimed`, `duplicate`, `expired`, `unauthorized`, `message_mismatch`, `not_retryable`, `unsafe`, and `terminal` as applicable.

- [ ] **Step 1: Write failing migration and transition tests**

```python
def test_inline_action_migration_exposes_durable_attempt_state(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_inline_session(
        session_token="s1", user_id=1001, original_url="https://x.com/u/status/1",
        normalized_url="https://x.com/u/status/1", provider="twitter",
        provider_label="Twitter/X", expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    session = store.get_inline_session("s1")
    assert session["delivery_stage"] is None
    assert session["failure_retryable"] == 0
    assert session["attempt_count"] == 0


def test_retry_claim_is_atomic_and_clears_failure_metadata(tmp_path):
    store = _store_with_failed_inline_session(tmp_path, retryable=True)
    assert store.claim_inline_retry(
        "s1", user_id=1001, inline_message_id="inline-1"
    ) == "claimed"
    assert store.claim_inline_retry(
        "s1", user_id=1001, inline_message_id="inline-1"
    ) == "duplicate"
    session = store.get_inline_session("s1")
    assert session["status"] == "delivering"
    assert session["attempt_count"] == 2
    assert session["failure_class"] is None


def test_cancel_rejects_inline_edit_boundary(tmp_path):
    store = _store_with_delivering_inline_session(tmp_path, stage="inline_edit")
    assert store.cancel_inline_delivery(
        "s1", user_id=1001, inline_message_id="inline-1"
    ) == "unsafe"
    assert store.get_inline_session("s1")["status"] == "delivering"
```

- [ ] **Step 2: Run state tests and verify RED**

Run: `uv run pytest tests/test_state_store_true_inline.py -q`

Expected: failures report missing columns and transition methods.

- [ ] **Step 3: Add schema migration and transactional transitions**

```python
add_column_if_missing(conn, "inline_sessions", "delivery_stage", "delivery_stage TEXT")
add_column_if_missing(
    conn, "inline_sessions", "failure_retryable",
    "failure_retryable INTEGER NOT NULL DEFAULT 0",
)
add_column_if_missing(
    conn, "inline_sessions", "attempt_count",
    "attempt_count INTEGER NOT NULL DEFAULT 0",
)
```

Implement transitions as single SQLite transactions with conditional `UPDATE ... WHERE` predicates. Initial claim sets `status='delivering'`, `delivery_stage='preflight'`, and `attempt_count=1`. Retry requires `status='failed' AND failure_retryable=1`, preserves the attached inline message and payment binding, clears failure fields, and increments `attempt_count`. Cancel requires `status='delivering'` and `delivery_stage IN ('preflight','download','storage_upload')`. `finish_inline_delivery` changes only nonterminal matching sessions and returns whether it won the transition.

Payment lookup must use the existing claimed session binding:

```sql
SELECT * FROM inline_one_time_payments
WHERE request_id = ? AND status = 'claimed'
ORDER BY updated_at DESC LIMIT 1
```

where the request ID remains `inline:<session_token>`.

- [ ] **Step 4: Run state tests and verify GREEN**

Run: `uv run pytest tests/test_state_store_true_inline.py -q`

Expected: all state-store inline tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/instagram_video_bot/services/state_schema.py src/instagram_video_bot/services/state_store.py tests/test_state_store_true_inline.py
git commit -m "feat: persist inline action state"
```

### Task 3: Inline Callback, Delivery, and Payment Integration

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `src/instagram_video_bot/services/telegram_wiring.py`
- Modify: `src/instagram_video_bot/services/telegram_inline_sessions.py`
- Modify: `src/instagram_video_bot/services/chaos_text.py`
- Modify: `tests/test_telegram_bot_true_inline.py`
- Modify: `tests/test_telegram_bot_errors.py`

**Interfaces:**
- Consumes Task 1 keyboard/parser functions and Task 2 atomic state transitions.
- Produces `TelegramBot.inline_action_callback_handler(update, context)`.
- Produces active task map `inline_delivery_tasks: dict[str, asyncio.Task[None]]`.
- Extends `_safe_edit_inline_text(..., reply_markup=...)` and final media edits with explicit reply markup.

- [ ] **Step 1: Write failing owner, cancel, retry, and payment tests**

```python
async def test_inline_action_rejects_non_owner_in_group(tmp_path):
    store = _claimed_inline_store(tmp_path, access_kind="subscription")
    bot = TelegramBot(state_store=store)
    query = _FakeCallbackQuery(
        "inline-action:cancel:s1", inline_message_id="inline-1", user_id=2002
    )
    await bot.inline_action_callback_handler(_FakeUpdate(callback_query=query), _context())
    assert store.get_inline_session("s1")["status"] == "delivering"
    assert query.answers == [("Only the person who started this request can use this action.", True)]


async def test_one_time_retry_keeps_claim_then_success_consumes_once(tmp_path):
    store = _claimed_one_time_store(tmp_path)
    bot = TelegramBot(state_store=store)
    await _fail_inline_attempt_retryably(bot, store, "s1")
    payment = store.get_claimed_inline_one_time_payment("s1")
    assert payment["status"] == "claimed"
    await _tap_inline_retry_and_complete(bot, store, "s1")
    assert store.get_inline_one_time_payment(payment["payment_id"])["status"] == "delivered"


async def test_inline_edit_timeout_is_unknown_without_retry_or_refund(tmp_path):
    store = _claimed_one_time_store(tmp_path)
    bot, context = _bot_with_inline_edit_error(store, TimedOut("timeout"))
    await bot._deliver_inline_session(context, session_token="s1", one_time_payment_id=None)
    session = store.get_inline_session("s1")
    assert session["status"] == "delivery_unknown"
    assert session["failure_retryable"] == 0
    assert context.bot.refunds == []
    assert context.bot.edited_text[-1]["reply_markup"] is None
```

Also add tests for safe cancellation refunding exactly once, terminal failure refunding exactly once, repeated Retry taps creating one task, retry after a new `TelegramBot` instance, subscription final-only accounting, promo success-only consumption, and callback handler registration.

- [ ] **Step 2: Run focused integration tests and verify RED**

Run: `uv run pytest tests/test_telegram_bot_true_inline.py tests/test_telegram_bot_errors.py -q`

Expected: failures report the missing callback handler, task map, keyboards, and payment-safe transitions.

- [ ] **Step 3: Implement callback routing and task ownership**

Register:

```python
CallbackQueryHandler(
    bot.inline_action_callback_handler,
    pattern=r"^inline-action:(?:cancel|retry):[A-Za-z0-9_-]+$",
)
```

The handler parses the action, verifies `query.from_user`, `query.inline_message_id`, the session owner, and exact message binding, then delegates the state change to the atomic store method. Cancel removes the keyboard, cancels a local task when present, and refunds only a claimed one-time payment. Retry keeps the claim, installs Cancel, and schedules one new attempt.

- [ ] **Step 4: Make delivery stages and outcomes explicit**

Before each external stage call, persist the live stage. Immediately before `edit_message_media`, persist `inline_edit`; call it with `reply_markup=None`. Map outcomes as follows:

```python
if failure_stage == "inline_edit" and isinstance(error, (NetworkError, TimedOut)):
    outcome = "delivery_unknown"
    retryable = False
elif failure_stage in {"download", "storage_upload"} and is_transient_download_error(error):
    outcome = "failed"
    retryable = True
else:
    outcome = "failed"
    retryable = False
```

Use the existing Telegram classifier for stored failure metadata while keeping retry/ambiguity as separate decisions. Record subscription failure only for terminal certain failure, record success once, and never record cancellation or unknown delivery as a provider failure. Resolve a one-time claim from SQLite when the task argument is absent, so Retry after restart remains payment-safe.

- [ ] **Step 5: Run focused integration tests and verify GREEN**

Run: `uv run pytest tests/test_telegram_inline_actions.py tests/test_state_store_true_inline.py tests/test_telegram_bot_true_inline.py tests/test_telegram_bot_errors.py -q`

Expected: all inline action, state, delivery, and wiring tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/instagram_video_bot/services/telegram_bot.py src/instagram_video_bot/services/telegram_wiring.py src/instagram_video_bot/services/telegram_inline_sessions.py src/instagram_video_bot/services/chaos_text.py tests/test_telegram_bot_true_inline.py tests/test_telegram_bot_errors.py
git commit -m "feat: add payment-safe inline cancel and retry"
```

### Task 4: Group Recognition and Privacy Diagnostics

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_wiring.py`
- Modify: `tests/test_telegram_bot_media_send.py`
- Modify: `tests/test_telegram_bot_errors.py`
- Modify: `README.md`

**Interfaces:**
- Produces `warn_if_group_privacy_enabled(bot) -> None`, called during existing post-init setup.
- Keeps the existing `filters.TEXT & ~filters.COMMAND` message handler contract.

- [ ] **Step 1: Write failing group and privacy tests**

```python
@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_plain_group_url_enters_request_intake(chat_type, telegram_bot_factory):
    bot = telegram_bot_factory()
    update = _FakeUpdate("https://x.com/example/status/123", chat_type=chat_type)
    await bot.handle_message(update, _FakeContext())
    assert len(update.message.status_messages) == 1
    markup = update.message.status_messages[0].reply_markup
    assert markup.inline_keyboard[0][0].callback_data.startswith("job:cancel:")


async def test_post_init_warns_when_group_privacy_hides_plain_messages(caplog):
    fake_bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(can_read_all_group_messages=False))
    )
    await warn_if_group_privacy_enabled(fake_bot)
    assert "BotFather /setprivacy" in caplog.text
```

Add capability-true, capability-absent, and `get_me` failure cases; the diagnostic must never prevent startup.

- [ ] **Step 2: Run group-focused tests and verify RED**

Run: `uv run pytest tests/test_telegram_bot_media_send.py tests/test_telegram_bot_errors.py -q`

Expected: group recognition behavior passes or exposes the exact fixture gap, while privacy diagnostic tests fail because the helper is missing.

- [ ] **Step 3: Implement diagnostic and documentation**

```python
async def warn_if_group_privacy_enabled(bot: Any) -> None:
    try:
        identity = await bot.get_me()
    except Exception:
        logger.warning("Could not inspect Telegram group privacy capability", exc_info=True)
        return
    if getattr(identity, "can_read_all_group_messages", None) is False:
        logger.warning(
            "Telegram privacy mode may hide ordinary group URLs; disable it with "
            "BotFather /setprivacy or grant the bot administrative message visibility."
        )
```

Call it from post-init without removing existing deployment notifications. Document `/setprivacy` Disable, administrative visibility, and remove/re-add guidance under Paid true inline mode troubleshooting.

- [ ] **Step 4: Run group and wiring tests and verify GREEN**

Run: `uv run pytest tests/test_telegram_bot_media_send.py tests/test_telegram_bot_errors.py -q`

Expected: group/supergroup recognition and all post-init cases pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/instagram_video_bot/services/telegram_wiring.py tests/test_telegram_bot_media_send.py tests/test_telegram_bot_errors.py README.md
git commit -m "fix: diagnose group URL privacy mode"
```

### Task 5: Full Verification and Design Conformance

**Files:**
- Modify only files required to fix failures caused by Tasks 1-4.

**Interfaces:**
- Consumes all previous task deliverables.
- Produces a clean feature branch whose full test suite and diff validation pass.

- [ ] **Step 1: Run all focused regression suites together**

Run: `uv run pytest tests/test_telegram_inline_actions.py tests/test_state_store_true_inline.py tests/test_telegram_bot_true_inline.py tests/test_telegram_job_actions.py tests/test_telegram_job_actions_integration.py tests/test_telegram_bot_media_send.py tests/test_telegram_bot_errors.py -q`

Expected: all focused action, payment, inline delivery, group, and existing direct-job tests pass.

- [ ] **Step 2: Run the complete suite**

Run: `uv run pytest -q`

Expected: zero failures; only previously known warnings or skips are acceptable.

- [ ] **Step 3: Run static and diff validation**

Run: `uv run ruff check src tests`

Expected: zero lint errors.

Run: `git diff --check HEAD~4..HEAD`

Expected: no whitespace errors.

- [ ] **Step 4: Review every specification invariant against evidence**

Confirm from tests and persisted transitions: owner-only actions; one-task retry idempotence; cancellation boundary; one-time claim retention/consumption/refund; unknown-delivery no-refund/no-retry; final-only subscription accounting; success-only promo accounting; explicit keyboard removal; group and supergroup intake; privacy warning.

- [ ] **Step 5: Commit any verification-only corrections**

If verification required code corrections, stage only those exact files and commit with `fix: close inline action verification gaps`. If no correction was needed, create no empty commit.
