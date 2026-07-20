# Reliability Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Instagram timeout tests deterministic, introduce an explicit executor lifecycle seam, align Docker health with supported provider modes, and make account-state persistence crash-safe in Docker.

**Architecture:** `VideoDownloader` keeps provider policy and deadline classification, while a new `InstagramProviderRuntime` owns the shared thread executor and its lifecycle. Tests replace the public Instagram recovery boundary so the suite is offline. Account state is written with fsync plus atomic replacement inside a directory-level Docker mount.

**Tech Stack:** Python 3.11, asyncio, concurrent.futures, pytest/pytest-asyncio, Pydantic Settings, Docker Compose.

## Global Constraints

- Preserve the single-process Telegram polling deployment model.
- Preserve current provider concurrency limits and detached account-lease behavior.
- Do not add production-only testing flags.
- Do not change the SQLite schema, Telegram commands, or user-visible messages.
- Keep `ACCOUNT_STATE_FILE` backward compatible by defaulting to the existing repository-root `accounts_state.json`.
- Follow red-green-refactor for every behavior change.

---

## File Structure

- Create `src/instagram_video_bot/services/instagram_provider_runtime.py`: own executor creation, generation retirement, submission, and idempotent shutdown.
- Create `tests/test_instagram_provider_runtime.py`: focused runtime lifecycle tests.
- Modify `src/instagram_video_bot/services/video_downloader.py`: inject/use the runtime and classify executor-generation cancellation.
- Modify `tests/test_video_downloader_flow.py`: force public recovery to be an offline deterministic miss unless a test installs a fake.
- Modify `src/instagram_video_bot/utils/health_check.py`: remove optional Instagram credentials from container health.
- Modify `tests/test_health_check.py`: encode the health contract.
- Modify `src/instagram_video_bot/config/settings.py`: add `ACCOUNT_STATE_FILE`.
- Modify `src/instagram_video_bot/utils/account_manager.py`: use configured state path and atomic writes.
- Modify `tests/test_settings.py` and `tests/test_account_manager.py`: cover configured paths and crash-safe persistence.
- Modify `.env.example`, `.gitignore`, `.dockerignore`, `docker-compose.yml`, and `README.md`: use a directory-level account-state mount, keep state artifacts out of source/image contexts, and document one-time migration.

---

### Task 1: Executor Runtime Lifecycle

**Files:**

- Create: `src/instagram_video_bot/services/instagram_provider_runtime.py`
- Create: `tests/test_instagram_provider_runtime.py`

**Interfaces:**

- Consumes: `Callable[[], T]`, `ThreadPoolExecutor`, and a positive `max_workers`.
- Produces:
  - `SubmittedInstagramOperation[T]` with `executor` and `future`.
  - `InstagramProviderRuntime.submit(operation, *, max_workers)`.
  - `InstagramProviderRuntime.retire(stale_executor) -> bool`.
  - `InstagramProviderRuntime.shutdown() -> None`.

- [ ] **Step 1: Write lifecycle tests**

```python
import threading

from src.instagram_video_bot.services.instagram_provider_runtime import (
    InstagramProviderRuntime,
)


def test_retire_cancels_queued_work_and_creates_fresh_generation():
    runtime = InstagramProviderRuntime()
    running_started = threading.Event()
    release_running = threading.Event()

    def block_worker():
        running_started.set()
        return release_running.wait(timeout=2)

    try:
        running = runtime.submit(
            block_worker,
            max_workers=1,
        )
        assert running_started.wait(timeout=1) is True
        queued = runtime.submit(lambda: "old", max_workers=1)

        assert runtime.retire(running.executor) is True
        assert queued.future.cancelled() is True

        fresh = runtime.submit(lambda: "fresh", max_workers=1)
        assert fresh.executor is not running.executor
        assert fresh.future.result(timeout=1) == "fresh"
    finally:
        release_running.set()
        runtime.shutdown()


def test_shutdown_is_idempotent_and_allows_a_new_generation():
    runtime = InstagramProviderRuntime()
    first = runtime.submit(lambda: "first", max_workers=1)
    assert first.future.result(timeout=1) == "first"

    runtime.shutdown()
    runtime.shutdown()

    second = runtime.submit(lambda: "second", max_workers=1)
    try:
        assert second.executor is not first.executor
        assert second.future.result(timeout=1) == "second"
    finally:
        runtime.shutdown()


def test_retire_ignores_an_executor_that_is_no_longer_active():
    runtime = InstagramProviderRuntime()
    first = runtime.submit(lambda: "first", max_workers=1)
    assert first.future.result(timeout=1) == "first"
    runtime.shutdown()
    second = runtime.submit(lambda: "second", max_workers=1)
    try:
        assert runtime.retire(first.executor) is False
        assert second.future.result(timeout=1) == "second"
    finally:
        runtime.shutdown()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_instagram_provider_runtime.py
```

Expected: collection fails because `instagram_provider_runtime` does not exist.

- [ ] **Step 3: Implement the minimal runtime**

```python
"""Lifecycle owner for blocking Instagram provider execution."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SubmittedInstagramOperation(Generic[T]):
    executor: ThreadPoolExecutor
    future: Future[T]


class InstagramProviderRuntime:
    """Own one bounded executor generation at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._max_workers: int | None = None

    def submit(
        self,
        operation: Callable[[], T],
        *,
        max_workers: int,
    ) -> SubmittedInstagramOperation[T]:
        limit = max(1, int(max_workers))
        with self._lock:
            if self._executor is None or self._max_workers != limit:
                stale = self._executor
                self._executor = ThreadPoolExecutor(
                    max_workers=limit,
                    thread_name_prefix="instagram-provider",
                )
                self._max_workers = limit
                if stale is not None:
                    stale.shutdown(wait=False, cancel_futures=True)
            executor = self._executor
            future = executor.submit(operation)
        return SubmittedInstagramOperation(executor=executor, future=future)

    def retire(self, stale_executor: ThreadPoolExecutor) -> bool:
        with self._lock:
            if self._executor is not stale_executor:
                return False
            self._executor = None
            self._max_workers = None
        stale_executor.shutdown(wait=False, cancel_futures=True)
        return True

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            self._max_workers = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
```

- [ ] **Step 4: Run lifecycle tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_instagram_provider_runtime.py
```

Expected: `3 passed`.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/instagram_video_bot/services/instagram_provider_runtime.py tests/test_instagram_provider_runtime.py
git commit -m "refactor: isolate instagram executor lifecycle"
```

---

### Task 2: Deterministic Provider Deadlines and Offline Tests

**Files:**

- Modify: `src/instagram_video_bot/services/video_downloader.py:60-778`
- Modify: `tests/test_video_downloader_flow.py:1-1200`

**Interfaces:**

- Consumes: `InstagramProviderRuntime` from Task 1.
- Produces:
  - `VideoDownloader(instagram_runtime: InstagramProviderRuntime | None = None)`.
  - `VideoDownloader.shutdown_shared_instagram_runtime()`.
  - One safe resubmission when a queued future is cancelled during runtime retirement.

- [ ] **Step 1: Add failing integration tests**

Add an offline boundary near the top of `tests/test_video_downloader_flow.py`:

```python
@pytest.fixture(autouse=True)
def _disable_real_public_instagram_recovery(monkeypatch):
    monkeypatch.setattr(
        InstagramClient,
        "download_public_ytdlp_media",
        staticmethod(lambda _url, _output_dir: None),
    )
```

Add a fake runtime and regression test:

```python
class _CancelledThenSuccessfulRuntime:
    def __init__(self):
        self.submit_calls = 0

    def submit(self, operation, *, max_workers):
        self.submit_calls += 1
        executor = object()
        future = Future()
        if self.submit_calls == 1:
            future.cancel()
        else:
            future.set_result(operation())
        return SimpleNamespace(executor=executor, future=future)

    def retire(self, _executor):
        return True

    def shutdown(self):
        return None


@pytest.mark.asyncio
async def test_instagram_sync_resubmits_queued_work_cancelled_by_recycle():
    runtime = _CancelledThenSuccessfulRuntime()
    downloader = VideoDownloader(instagram_runtime=runtime)

    result = await downloader._run_instagram_sync(lambda: "ok")

    assert result == "ok"
    assert runtime.submit_calls == 2
```

Add this fixture:

```python
@pytest.fixture
def instagram_runtime():
    runtime = InstagramProviderRuntime()
    yield runtime
    runtime.shutdown()
```

Add this import:

```python
from src.instagram_video_bot.services.instagram_provider_runtime import (
    InstagramProviderRuntime,
)
```

Add the `instagram_runtime` fixture parameter and construct
`VideoDownloader(instagram_runtime=instagram_runtime)` in these tests:

- `test_instagram_fallback_login_timeout_becomes_download_error`;
- `test_instagram_timeout_keeps_account_leased_until_worker_finishes`;
- `test_instagram_cancellation_keeps_account_leased_until_worker_finishes`;
- `test_instagram_cancellation_retires_account_as_cancelled_stale_after_detached_window`;
- `test_instagram_stale_timeout_retires_and_releases_account`;
- `test_instagram_stale_timeout_recycles_saturated_executor`;
- `test_single_account_stale_timeout_recycles_saturated_executor`;
- `test_single_account_cancellation_recycles_saturated_executor`.

Delete `_ShutdownOnceExecutor`, `_ImmediateExecutor`, and
`test_instagram_sync_retries_submit_after_executor_shutdown`. Runtime ownership
makes an executor shutdown between acquisition and submission impossible;
Task 1 directly covers shutdown followed by a fresh submission.

- [ ] **Step 2: Run the focused flow tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q \
  tests/test_video_downloader_flow.py::test_instagram_sync_resubmits_queued_work_cancelled_by_recycle \
  tests/test_video_downloader_flow.py::test_instagram_stale_timeout_recycles_saturated_executor
```

Expected: constructor rejects `instagram_runtime`, and the stale-timeout test
still exposes the existing cancellation race.

- [ ] **Step 3: Integrate the runtime into `VideoDownloader`**

Import:

```python
from concurrent.futures import CancelledError as FutureCancelledError

from .instagram_provider_runtime import InstagramProviderRuntime
```

Replace the class-level executor fields with:

```python
_shared_instagram_runtime = InstagramProviderRuntime()
```

Change construction:

```python
def __init__(
    self,
    instagram_runtime: InstagramProviderRuntime | None = None,
):
    self.instagram_runtime = (
        instagram_runtime or self._shared_instagram_runtime
    )
    self.min_delay_between_downloads = 10
    self.random_delay_range = (1.0, 3.0)
    self.fast_min_delay_between_downloads = max(
        0.0, float(settings.IG_FAST_MIN_DELAY_BETWEEN_DOWNLOADS)
    )
    self.fast_random_delay_range = (
        max(0.0, float(settings.IG_FAST_RANDOM_DELAY_MIN_SECONDS)),
        max(0.0, float(settings.IG_FAST_RANDOM_DELAY_MAX_SECONDS)),
    )
    fast_extractor = InstagramFastExtractor(
        timeout_connect=settings.IG_FAST_TIMEOUT_CONNECT,
        timeout_read=settings.IG_FAST_TIMEOUT_READ,
        metadata_timeout=(
            settings.IG_FAST_METADATA_TIMEOUT_CONNECT_SECONDS,
            settings.IG_FAST_METADATA_TIMEOUT_READ_SECONDS,
        ),
        total_budget_seconds=settings.IG_FAST_TOTAL_BUDGET_SECONDS,
    )
    self.instagram_adapter = InstagramProviderAdapter(fast_extractor)
    self.twitter_adapter = TwitterProviderAdapter(
        TwitterDownloader(proxy=settings.get_single_proxy())
    )
    self.youtube_adapter = YouTubeShortsProviderAdapter(
        YouTubeShortsDownloader()
    )
    self.last_account_health_event = None
    self.last_provider_metrics = ProviderExecutionMetrics(provider="unknown")
```

Replace `_submit_instagram_operation` with:

```python
def _submit_instagram_operation(self, operation: Callable[[], T]):
    return self.instagram_runtime.submit(
        operation,
        max_workers=settings.INSTAGRAM_MAX_CONCURRENT_JOBS,
    )
```

In `_run_instagram_sync`, preserve one deadline and resubmit only a future that
is already cancelled:

```python
submission = self._submit_instagram_operation(operation)
executor = submission.executor
future = submission.future
deadline = loop.time() + timeout_seconds
resubmitted_after_recycle = False

while True:
    if future.done():
        try:
            return future.result()
        except FutureCancelledError:
            if resubmitted_after_recycle or loop.time() >= deadline:
                break
            submission = self._submit_instagram_operation(operation)
            executor = submission.executor
            future = submission.future
            resubmitted_after_recycle = True
            continue
    remaining = deadline - loop.time()
    if remaining <= 0:
        break
    await asyncio.sleep(min(0.05, remaining))
```

Replace stale recycling with:

```python
self.instagram_runtime.retire(executor)
```

Add the lifecycle seam:

```python
@classmethod
def shutdown_shared_instagram_runtime(cls) -> None:
    cls._shared_instagram_runtime.shutdown()
```

Delete `_get_instagram_provider_executor`,
`_recycle_instagram_provider_executor`, and their executor fields.

- [ ] **Step 4: Run all provider-flow tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_video_downloader_flow.py
```

Expected: all tests pass without Instagram/yt-dlp network output.

- [ ] **Step 5: Run the executor tests together**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q \
  tests/test_instagram_provider_runtime.py \
  tests/test_video_downloader_flow.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/instagram_video_bot/services/video_downloader.py tests/test_video_downloader_flow.py
git commit -m "fix: stabilize instagram timeout execution"
```

---

### Task 3: Align Container Health With Supported Modes

**Files:**

- Modify: `src/instagram_video_bot/utils/health_check.py:1-84`
- Modify: `tests/test_health_check.py`

**Interfaces:**

- Consumes: existing `settings` and `StateStore`.
- Produces: `check_health()` that treats Instagram credentials as optional.

- [ ] **Step 1: Write the failing health-contract test**

```python
def test_check_health_accepts_bot_without_instagram_credentials(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (tmp_path / "sessions").mkdir()
    fake_settings = SimpleNamespace(
        TEMP_DIR=temp_dir,
        BASE_DIR=tmp_path,
        BOT_TOKEN="token",
        IG_USERNAME="",
        IG_PASSWORD="",
    )
    monkeypatch.setattr(health_check, "settings", fake_settings)

    assert health_check.check_health() is True
```

Keep the existing missing-session and stale-job tests. Add:

```python
def test_check_health_fails_without_bot_token(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(
        health_check,
        "settings",
        SimpleNamespace(
            TEMP_DIR=temp_dir,
            BASE_DIR=tmp_path,
            BOT_TOKEN="",
            IG_USERNAME="",
            IG_PASSWORD="",
        ),
    )

    assert health_check.check_health() is False
```

Add writable-storage and broken-state coverage:

```python
def test_check_health_fails_when_temp_storage_is_not_writable(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(
        health_check,
        "settings",
        SimpleNamespace(
            TEMP_DIR=temp_dir,
            BASE_DIR=tmp_path,
            BOT_TOKEN="token",
        ),
    )
    monkeypatch.setattr(
        health_check.Path,
        "touch",
        lambda _self: (_ for _ in ()).throw(PermissionError("read only")),
    )

    assert health_check.check_health() is False


def test_check_health_fails_when_state_path_is_not_a_database(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (tmp_path / "sessions").mkdir()
    invalid_db_path = tmp_path / "state-directory"
    invalid_db_path.mkdir()
    monkeypatch.setattr(
        health_check,
        "settings",
        SimpleNamespace(
            TEMP_DIR=temp_dir,
            BASE_DIR=tmp_path,
            BOT_TOKEN="token",
            STATE_DB_PATH=invalid_db_path,
            INSTAGRAM_PROVIDER_TIMEOUT_SECONDS=180,
        ),
    )

    assert health_check.check_health() is False
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q \
  tests/test_health_check.py::test_check_health_accepts_bot_without_instagram_credentials
```

Expected: FAIL because health currently requires Instagram credentials or an
accounts file.

- [ ] **Step 3: Remove optional-provider credential gating**

Delete `_has_configured_accounts` and this block:

```python
accounts_file = settings.BASE_DIR / "accounts.txt"
if not _has_configured_accounts(accounts_file) and (
    not settings.IG_USERNAME or not settings.IG_PASSWORD
):
    logger.error("Instagram credentials are not set")
    return False
```

Do not change writable-temp, sessions, token, state DB, stale-job, or timeout
diagnostics.

- [ ] **Step 4: Run health tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_health_check.py
```

Expected: all health tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/instagram_video_bot/utils/health_check.py tests/test_health_check.py
git commit -m "fix: align health check with optional providers"
```

---

### Task 4: Atomic Account-State Persistence and Docker Mount

**Files:**

- Modify: `src/instagram_video_bot/config/settings.py:12-132`
- Modify: `src/instagram_video_bot/utils/account_manager.py:108-247,667-679`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_account_manager.py`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `docker-compose.yml:8-31`
- Modify: `README.md`

**Interfaces:**

- Consumes: `settings.ACCOUNT_STATE_FILE`.
- Produces:
  - `Settings.ACCOUNT_STATE_FILE: Path`.
  - `AccountManager._save_state()` with same-filesystem fsync and atomic replace.
  - Compose directory mount `./account-state:/app/account-state`.

- [ ] **Step 1: Write configuration and persistence regression tests**

Add to `tests/test_settings.py`:

```python
def test_account_state_file_can_be_configured(tmp_path):
    state_file = tmp_path / "state" / "accounts.json"
    configured = Settings(
        _env_file=None,
        TEMP_DIR=tmp_path / "temp",
        CACHE_DIR=tmp_path / "cache",
        STATE_DB_PATH=tmp_path / "state.db",
        ACCOUNT_STATE_FILE=state_file,
    )

    assert configured.ACCOUNT_STATE_FILE == state_file
    assert state_file.parent.is_dir()
```

Add to `tests/test_account_manager.py`:

```python
def test_get_account_manager_uses_configured_state_file(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    _write_accounts(tmp_path / "accounts.txt", "first")
    configured_state = tmp_path / "state" / "accounts.json"
    monkeypatch.setattr(
        account_manager_module.settings,
        "ACCOUNT_STATE_FILE",
        configured_state,
    )
    monkeypatch.setattr(account_manager_module, "_account_manager", None)

    manager = account_manager_module.get_account_manager()

    assert manager is not None
    assert manager.state_file == configured_state


def test_save_state_preserves_previous_file_when_replace_fails(
    monkeypatch, tmp_path
):
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first")
    state_file.write_text('{"version": "previous"}\n')
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    monkeypatch.setattr(
        account_manager_module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    manager._save_state()

    assert json.loads(state_file.read_text()) == {"version": "previous"}
    assert list(tmp_path.glob(".accounts_state.json.*.tmp")) == []


def test_save_state_atomically_replaces_with_valid_json(tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "nested" / "accounts_state.json"
    _write_accounts(accounts_file, "first")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    manager._save_state()

    payload = json.loads(state_file.read_text())
    assert payload["accounts"][0]["username"] == "first"
    assert list(state_file.parent.glob(".accounts_state.json.*.tmp")) == []


def test_save_state_fsyncs_file_and_parent_directory(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    fsync_calls = []
    real_fsync = account_manager_module.os.fsync

    def recording_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(account_manager_module.os, "fsync", recording_fsync)

    manager._save_state()

    assert len(fsync_calls) == 2
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q \
  tests/test_settings.py::test_account_state_file_can_be_configured \
  tests/test_account_manager.py::test_get_account_manager_uses_configured_state_file \
  tests/test_account_manager.py::test_save_state_preserves_previous_file_when_replace_fails \
  tests/test_account_manager.py::test_save_state_atomically_replaces_with_valid_json \
  tests/test_account_manager.py::test_save_state_fsyncs_file_and_parent_directory
```

Expected: settings rejects/ignores the new field behavior and the replace-failure
test observes that the live file was already truncated.

- [ ] **Step 3: Add the configured path**

In `Settings`:

```python
ACCOUNT_STATE_FILE: Path = Path(
    os.getenv("ACCOUNT_STATE_FILE", BASE_DIR / "accounts_state.json")
)
```

In `Settings.__init__`:

```python
self.ACCOUNT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
```

In `get_account_manager()`:

```python
_account_manager = AccountManager(
    accounts_file=accounts_file,
    state_file=settings.ACCOUNT_STATE_FILE,
)
```

- [ ] **Step 4: Implement atomic writing**

Add imports:

```python
import os
import tempfile
```

Replace `_save_state` writing with:

```python
temporary_path: Path | None = None
try:
    state = {
        "accounts": [acc.to_dict() for acc in self.accounts],
        "last_updated": datetime.now().isoformat(),
    }
    self.state_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=self.state_file.parent,
        prefix=f".{self.state_file.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        json.dump(state, temporary, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary_path, self.state_file)
    temporary_path = None
    directory_fd = os.open(self.state_file.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except Exception as error:
    logger.error("Failed to save state: %s", error)
finally:
    if temporary_path is not None:
        temporary_path.unlink(missing_ok=True)
```

- [ ] **Step 5: Update Compose and operator documentation**

In `.env.example` add:

```dotenv
ACCOUNT_STATE_FILE=accounts_state.json
```

Add `account-state/` to both `.gitignore` and `.dockerignore`.

Replace the file bind mount in `docker-compose.yml`:

```yaml
- ./account-state:/app/account-state
```

Add to the service environment:

```yaml
- ACCOUNT_STATE_FILE=/app/account-state/accounts_state.json
```

Add this migration section to `README.md`:

````markdown
### Account state directory migration

Before the first deployment that uses the directory-level account-state mount:

```bash
mkdir -p account-state
if [ -f accounts_state.json ]; then
  cp -p accounts_state.json account-state/accounts_state.json
fi
```

Keep the old file until the bot has started successfully and `make accounts-status`
shows the expected quarantines and failure counters.
````

- [ ] **Step 6: Run settings and account tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q \
  tests/test_settings.py \
  tests/test_account_manager.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Validate Compose configuration**

Run:

```bash
docker compose config --quiet
```

Expected: exit code 0.

- [ ] **Step 8: Commit Task 4**

```bash
git add \
  src/instagram_video_bot/config/settings.py \
  src/instagram_video_bot/utils/account_manager.py \
  tests/test_settings.py \
  tests/test_account_manager.py \
  .env.example \
  .gitignore \
  .dockerignore \
  docker-compose.yml \
  README.md
git commit -m "fix: persist account state atomically"
```

---

### Task 5: Full Verification

**Files:**

- No planned file changes.

**Interfaces:**

- Consumes: all behavior from Tasks 1-4.
- Produces: fresh evidence that the complete stabilization slice is releasable.

- [ ] **Step 1: Run the full suite once**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests
```

Expected: all tests pass; no real Instagram/yt-dlp requests appear.

- [ ] **Step 2: Run the full suite a second time**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests
```

Expected: the same passing count, demonstrating executor cleanup and test-order
stability.

- [ ] **Step 3: Check bytecode compilation and diff hygiene**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m compileall -q src
git diff --check HEAD~4..HEAD
```

Expected: both commands exit 0.

- [ ] **Step 4: Build the production image**

```bash
docker build -t inst-video-downloader-tg-bot:reliability .
```

Expected: exit code 0.

- [ ] **Step 5: Review the final diff**

```bash
git status --short
git log -5 --oneline
```

Expected: only the pre-existing untracked `.codebase-memory/` remains and the
four implementation commits are present.

- [ ] **Step 6: Record verification outcome**

If verification required no code corrections, do not create an empty commit.
Record exact passing counts and build status in the final handoff.
