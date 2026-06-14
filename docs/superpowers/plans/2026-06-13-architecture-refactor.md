# Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the architectural weight of `TelegramBot` by moving cohesive Telegram workflows into focused services while preserving runtime behavior.

**Architecture:** Keep `TelegramBot` as the public handler facade used by `telegram_wiring.py`, but delegate workflow bodies to small services under `src/instagram_video_bot/services/telegram/`. The first pass allows services to receive the bot facade where that keeps the extraction safe; later passes can narrow those dependencies after tests prove behavior is stable.

**Tech Stack:** Python 3.11, python-telegram-bot 22.7, pytest, pytest-asyncio, uv.

---

## File Structure

- Create `src/instagram_video_bot/services/telegram/__init__.py` to hold Telegram workflow collaborators.
- Create `src/instagram_video_bot/services/telegram/request_context.py` for the request context dataclass shared by Telegram workflow services.
- Create `src/instagram_video_bot/services/telegram/request_intake.py` for text/caption URL intake and queue submission.
- Create `src/instagram_video_bot/services/telegram/command_handlers.py` for standard command handlers and group-setting commands.
- Modify `src/instagram_video_bot/services/telegram_bot.py` to construct the new services and delegate existing public handler methods.
- Keep existing tests in `tests/test_telegram_bot_media_send.py` and `tests/test_telegram_bot_true_inline.py` as the primary behavior harness.

## Task 1: Extract Direct Request Intake

**Files:**
- Create: `src/instagram_video_bot/services/telegram/__init__.py`
- Create: `src/instagram_video_bot/services/telegram/request_context.py`
- Create: `src/instagram_video_bot/services/telegram/request_intake.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Test: `tests/test_telegram_bot_media_send.py`

- [ ] **Step 1: Write a focused characterization test**

Add a test to `tests/test_telegram_bot_media_send.py` proving that `TelegramBot.handle_message()` delegates to an injected request intake service:

```python
@pytest.mark.asyncio
async def test_handle_message_delegates_to_request_intake(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("https://www.instagram.com/reel/a/")
    context = _FakeContext(_FakeBot())
    calls = []

    class FakeRequestIntake:
        async def handle_message(self, received_update, received_context):
            calls.append((received_update, received_context))

    telegram_bot.request_intake = FakeRequestIntake()

    await telegram_bot.handle_message(update, context)

    assert calls == [(update, context)]
```

- [ ] **Step 2: Run the new test and verify it fails**

Run: `uv run pytest tests/test_telegram_bot_media_send.py::test_handle_message_delegates_to_request_intake -q`

Expected: FAIL because `TelegramBot.handle_message()` still contains the full workflow and does not delegate to `request_intake`.

- [ ] **Step 3: Create the Telegram workflow package**

Create `src/instagram_video_bot/services/telegram/__init__.py`:

```python
"""Focused Telegram workflow services."""
```

- [ ] **Step 4: Move the direct intake workflow**

Create `src/instagram_video_bot/services/telegram/request_context.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from telegram import Message


@dataclass
class RequestContext:
    """Telegram state for one user request tied to a shared job."""

    request_id: str
    chat_id: int
    user_id: int
    provider_label: str
    normalized_url: str
    original_url: str
    original_message_id: int
    status_message: Message
    quiet_mode: bool
    joined_existing: bool
    chaos_enabled: bool = False
    language_code: str = "ru"
```

Create `src/instagram_video_bot/services/telegram/request_intake.py` with `TelegramRequestIntake`. Move the existing body of `TelegramBot.handle_message()` into `TelegramRequestIntake.handle_message()`, replacing direct `self` calls with `bot = self._bot` and calls on `bot`.

The class shape must be:

```python
from __future__ import annotations

import asyncio
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ...config.settings import settings
from ..chaos_text import ChaosText
from ..request_parser import RequestParser
from .request_context import RequestContext


class TelegramRequestIntake:
    """Handle incoming text/caption messages and queue provider downloads."""

    def __init__(self, bot: Any):
        self._bot = bot

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        bot = self._bot
```

The method body must be the exact current `TelegramBot.handle_message()` workflow after substituting `bot` for every access that still belongs to the facade, including `_request_user_id`, `legacy_redirect_handler`, `_purge_expired_cache`, `_language_for_update`, `_message_is_from_owner`, `_whitelist_forwarded_visible_user`, `state_store`, `_consume_user_rate_limit`, `job_manager`, `_request_user_label`, `_build_job_executor`, `_build_submission_message`, `request_contexts`, `active_request_tasks`, `_await_request`, and `_cleanup_request_task`.

- [ ] **Step 5: Delegate from `TelegramBot`**

Modify `TelegramBot.__init__()`:

```python
from .telegram.request_intake import TelegramRequestIntake
from .telegram.request_context import RequestContext

self.request_intake = TelegramRequestIntake(self)
```

Replace `TelegramBot.handle_message()` with:

```python
async def handle_message(
    self, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle incoming messages by queueing supported provider links."""
    await self.request_intake.handle_message(update, context)
```

- [ ] **Step 6: Run focused request-intake tests**

Run: `uv run pytest tests/test_telegram_bot_media_send.py tests/test_telegram_bot_true_inline.py -q`

Expected: PASS.

- [ ] **Step 7: Run full suite**

Run: `uv run pytest -q`

Expected: PASS with all tests.

- [ ] **Step 8: Self-review and commit**

Review `git diff` for accidental behavior changes and circular import risk. Commit:

```bash
git add src/instagram_video_bot/services/telegram/__init__.py src/instagram_video_bot/services/telegram/request_context.py src/instagram_video_bot/services/telegram/request_intake.py src/instagram_video_bot/services/telegram_bot.py tests/test_telegram_bot_media_send.py
git commit -m "refactor: extract telegram request intake"
```

## Task 2: Extract Command Handler Workflow

**Files:**
- Create: `src/instagram_video_bot/services/telegram/command_handlers.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Test: `tests/test_telegram_bot_true_inline.py`

- [ ] **Step 1: Write a delegation test**

Add a test to `tests/test_telegram_bot_true_inline.py` proving `start_command()` delegates:

```python
@pytest.mark.asyncio
async def test_start_command_delegates_to_command_handlers(tmp_path):
    telegram_bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    update = _FakeUpdate("/start")
    context = _FakeContext(_FakeBot())
    calls = []

    class FakeCommandHandlers:
        async def start_command(self, received_update, received_context):
            calls.append((received_update, received_context))

    telegram_bot.command_handlers = FakeCommandHandlers()

    await telegram_bot.start_command(update, context)

    assert calls == [(update, context)]
```

- [ ] **Step 2: Run the new test and verify it fails**

Run: `uv run pytest tests/test_telegram_bot_true_inline.py::test_start_command_delegates_to_command_handlers -q`

Expected: FAIL because `start_command()` has not been delegated yet.

- [ ] **Step 3: Create `TelegramCommandHandlers`**

Create `src/instagram_video_bot/services/telegram/command_handlers.py` with a class that receives the bot facade:

```python
from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..chaos_text import ChaosText


class TelegramCommandHandlers:
    """Handle standard Telegram commands for the bot facade."""

    def __init__(self, bot: Any):
        self._bot = bot
```

Move these methods from `TelegramBot` into the class, using `bot = self._bot` where needed: `start_command`, `language_command`, `help_command`, `admin_help_command`, `formats_command`, `status_command`, `cancel_command`, `stats_command`, `chaos_command`, `quiet_command`, `dupes_command`, `statsmode_command`, `chatlimit_command`, and `userlimit_command`.

- [ ] **Step 4: Delegate command methods from `TelegramBot`**

Modify `TelegramBot.__init__()`:

```python
from .telegram.command_handlers import TelegramCommandHandlers

self.command_handlers = TelegramCommandHandlers(self)
```

Replace each moved `TelegramBot` method with a one-line delegation to `self.command_handlers`.

- [ ] **Step 5: Run focused command and Telegram tests**

Run: `uv run pytest tests/test_telegram_bot_true_inline.py tests/test_telegram_bot_media_send.py tests/test_telegram_bot_errors.py -q`

Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`

Expected: PASS with all tests.

- [ ] **Step 7: Self-review and commit**

Review `git diff` for changed command text, changed owner checks, or missed imports. Commit:

```bash
git add src/instagram_video_bot/services/telegram/command_handlers.py src/instagram_video_bot/services/telegram_bot.py tests/test_telegram_bot_true_inline.py
git commit -m "refactor: extract telegram command handlers"
```

## Task 3: Re-index, Review, and Decide Next Slice

**Files:**
- Modify: `/tmp/refactor-Inst_video_downloader_tg_bot.md`

- [ ] **Step 1: Re-index the repository**

Call the codebase-memory MCP tool `index_repository` with `repo_path="/root/Inst_video_downloader_tg_bot"`, `mode="full"`, and `persistence=true`.

- [ ] **Step 2: Inspect architecture**

Call the codebase-memory MCP tool `get_architecture` with `project="root-Inst_video_downloader_tg_bot"` and `aspects=["all"]`, then compare line counts and hotspots.

- [ ] **Step 3: Run final local E2E verification**

Run:

```bash
uv run pytest tests/test_telegram_bot_media_send.py tests/test_telegram_bot_true_inline.py tests/test_video_downloader_flow.py -q
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 4: Check real credential feasibility without printing secrets**

Run a secret-safe environment presence check that reports only whether required values are configured. Do not print token or account values.

- [ ] **Step 5: Commit progress docs if changed in repo**

If only `/tmp/refactor-Inst_video_downloader_tg_bot.md` changed, do not commit it because it is outside the repository. If repo docs changed, commit them.

- [ ] **Step 6: Decide whether to continue**

Continue with inline/payment extraction only if the first two slices are stable and the remaining `telegram_bot.py` responsibilities are still too broad. Otherwise stop with a verified architecture checkpoint.
