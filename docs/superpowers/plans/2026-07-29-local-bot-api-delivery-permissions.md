# Local Bot API Delivery Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore reliable Local Bot API path uploads while preserving a 500 MiB one-file limit and correct ambiguous-delivery handling.

**Architecture:** Keep the bot and Local Bot API on separate primary UIDs, grant the API only the bot's supplementary media GID, and prepare the shared directory with group-only traversal. Reuse the existing inline storage chat for direct delivery, then distinguish deterministic `BadRequest` responses from genuinely ambiguous transport failures.

**Tech Stack:** Python 3.11, python-telegram-bot 22.8, pytest, Docker Compose, GNU Make, C11 runtime helper.

## Global Constraints

- Local Bot API mode must accept one media file up to exactly `524288000` bytes.
- Cloud Bot API mode must remain capped at 50 MiB.
- `instagram-video-bot` remains UID/GID `1000:1000`.
- `telegram-bot-api` remains UID/GID `10001:10001` with a read-only `/app/temp` mount.
- Do not retry genuinely ambiguous final sends to user chats.
- Do not use `0777` or merge the services onto one UID.

---

### Task 1: Least-privilege shared media directory

**Files:**
- Modify: `docker-compose.local-api.yml`
- Modify: `Makefile`
- Modify: `scripts/telegram_bot_api_entrypoint.c`
- Test: `tests/test_local_api_compose_security.py`
- Test: `tests/test_telegram_bot_api_entrypoint.py`

**Interfaces:**
- Consumes: host `./temp`, bot GID `1000`, `TELEGRAM_MEDIA_DIR` environment variable.
- Produces: Local Bot API startup guarantee that `/app/temp` is a readable, traversable directory.

- [ ] **Step 1: Write failing Compose and Makefile tests**

Add assertions that the API service contains `group_add: ["1000"]`, retains `user: "10001:10001"`, and retains `./temp:/app/temp:ro`. Assert that `local-config`, `local-build`, and `local-up` depend on `local-prepare`, whose command is:

```make
local-prepare:
	install -d -o 1000 -g 1000 -m 0750 temp
```

- [ ] **Step 2: Run the deployment tests and verify RED**

Run:

```bash
uv run pytest tests/test_local_api_compose_security.py -q
```

Expected: failures for missing `group_add` and `local-prepare` behavior.

- [ ] **Step 3: Write a failing native-entrypoint access test**

Update `_environment` to accept a media directory and set `TELEGRAM_MEDIA_DIR`. Add a test that supplies a missing media directory and asserts a non-zero exit without echoing the path. Existing success tests must supply `tmp_path` as the readable media directory.

- [ ] **Step 4: Run the entrypoint test and verify RED**

Run:

```bash
uv run pytest tests/test_telegram_bot_api_entrypoint.py -q
```

Expected: the missing media directory is currently ignored, so the new test fails.

- [ ] **Step 5: Implement the minimal shared-access changes**

Add:

```yaml
group_add:
  - "1000"
```

to `telegram-bot-api`, add the Make dependencies and `local-prepare` command, and add a C11 check equivalent to:

```c
if (stat(media_dir, &metadata) != 0 || !S_ISDIR(metadata.st_mode) ||
    access(media_dir, R_OK | X_OK) != 0) {
    fputs("Unable to access shared media directory\n", stderr);
    return 1;
}
```

Default `TELEGRAM_MEDIA_DIR` to `/app/temp` without logging the configured path.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_local_api_compose_security.py tests/test_telegram_bot_api_entrypoint.py -q
```

Expected: all focused tests pass.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.local-api.yml Makefile scripts/telegram_bot_api_entrypoint.c tests/test_local_api_compose_security.py tests/test_telegram_bot_api_entrypoint.py
git commit -m "fix: grant local Bot API shared media access"
```

### Task 2: Reuse inline storage for direct delivery

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Test: `tests/test_telegram_bot_media_send.py`

**Interfaces:**
- Consumes: `TELEGRAM_MEDIA_STORAGE_CHAT_ID: int | None`, `INLINE_STORAGE_CHAT_ID: int | None`.
- Produces: `TelegramBot.media_stager: TelegramMediaStager | None`, preferring the explicit direct setting and falling back to inline storage.

- [ ] **Step 1: Write failing storage-selection tests**

Add tests equivalent to:

```python
def test_direct_media_stager_reuses_inline_storage_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TELEGRAM_MEDIA_STORAGE_CHAT_ID", None)
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -1002)
    bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    assert bot.media_stager is not None
    assert bot.media_stager.storage_chat_id == -1002


def test_direct_media_stager_prefers_explicit_storage_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TELEGRAM_MEDIA_STORAGE_CHAT_ID", -1001)
    monkeypatch.setattr(settings, "INLINE_STORAGE_CHAT_ID", -1002)
    bot = TelegramBot(state_store=StateStore(tmp_path / "state.db"))
    assert bot.media_stager is not None
    assert bot.media_stager.storage_chat_id == -1001
```

- [ ] **Step 2: Run the tests and verify RED**

Run both new node IDs with `uv run pytest ... -q`. Expected: the inline fallback test sees `media_stager is None`.

- [ ] **Step 3: Implement minimal storage selection**

Select the explicit direct storage ID when non-`None`; otherwise select `INLINE_STORAGE_CHAT_ID`. Construct `TelegramMediaStager` only when the selected ID is non-`None`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_telegram_bot_media_send.py -q
```

Expected: the storage-selection tests and existing delivery tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/instagram_video_bot/services/telegram_bot.py tests/test_telegram_bot_media_send.py
git commit -m "fix: reuse inline storage for direct delivery"
```

### Task 3: Correct deterministic delivery classification

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_media_retry.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Test: `tests/test_telegram_media_retry.py`
- Test: `tests/test_telegram_bot_media_send.py`

**Interfaces:**
- Produces: `is_ambiguous_telegram_delivery_error(error: Exception) -> bool`.
- Consumes: `BadRequest`, `NetworkError`, and `TimedOut` from python-telegram-bot.

- [ ] **Step 1: Write failing predicate tests**

Add assertions:

```python
assert is_ambiguous_telegram_delivery_error(BadRequest("Can't get stat about the file")) is False
assert is_ambiguous_telegram_delivery_error(TimedOut("timed out")) is True
assert is_ambiguous_telegram_delivery_error(NetworkError("httpx.ReadError")) is True
```

- [ ] **Step 2: Write a failing state-machine regression test**

Use a fake bot whose final `send_video` raises `BadRequest("Can't get stat about the file")`. Drive `_await_request` and assert the persisted delivery attempt is `status == "failed"`, the request failure reason is `telegram_delivery`, and no ambiguous-delivery failure is recorded.

- [ ] **Step 3: Run the new tests and verify RED**

Run the new retry test and the new media-send test by node ID. Expected: the predicate is missing and current delivery status is `unknown`.

- [ ] **Step 4: Implement the predicate and use it at the delivery boundary**

Implement:

```python
def is_ambiguous_telegram_delivery_error(error: Exception) -> bool:
    return isinstance(error, NetworkError) and not isinstance(error, BadRequest)
```

Use the predicate together with the existing `telegram_user_send_ambiguous` marker. Preserve `storage_upload_attempted` handling and all timeout semantics.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_telegram_media_retry.py tests/test_job_states.py tests/test_telegram_bot_media_send.py -q
```

Expected: all tests pass, including genuine timeout ambiguity.

- [ ] **Step 6: Commit**

```bash
git add src/instagram_video_bot/services/telegram_media_retry.py src/instagram_video_bot/services/telegram_bot.py tests/test_telegram_media_retry.py tests/test_telegram_bot_media_send.py
git commit -m "fix: distinguish rejected from ambiguous Telegram sends"
```

### Task 4: Lock the 500 MiB regression contract

**Files:**
- Test: `tests/test_telegram_media_files.py`
- Verify: `docker-compose.local-api.yml`

**Interfaces:**
- Consumes: `media_input(path, local_mode, max_upload_bytes)`.
- Produces: regression proof for the exact one-file ceiling.

- [ ] **Step 1: Add sparse-file boundary tests**

Create sparse files with `Path.truncate` at `50 * 1024 * 1024 + 1`, `500 * 1024 * 1024`, and `500 * 1024 * 1024 + 1`. Assert local mode accepts the first two as `Path` values and rejects the last; assert cloud mode rejects the first.

- [ ] **Step 2: Run boundary tests**

Run:

```bash
uv run pytest tests/test_telegram_media_files.py -q
```

Expected: all boundary tests pass without allocating 500 MiB of memory.

- [ ] **Step 3: Verify Compose preserves the exact ceiling**

Run:

```bash
rg -n 'TELEGRAM_MAX_UPLOAD_BYTES: "524288000"' docker-compose.local-api.yml
```

Expected: exactly one Local Bot API override.

- [ ] **Step 4: Commit**

```bash
git add tests/test_telegram_media_files.py
git commit -m "test: lock local Bot API 500 MiB boundary"
```

### Task 5: Full verification and deployment

**Files:**
- Modify: `docs/guides/README-Docker.md`
- Runtime: local Compose stack and private storage chat.

**Interfaces:**
- Consumes: approved production Compose configuration and existing Telegram credentials/storage chat.
- Produces: healthy deployed containers and a verified upload above 50 MiB.

- [ ] **Step 1: Document shared-group preparation and storage fallback**

Document `make local-prepare`, mode `0750`, supplementary GID `1000`, the read-only mount, and reuse of `INLINE_STORAGE_CHAT_ID` when the direct storage ID is empty.

- [ ] **Step 2: Run static and full automated verification**

Run:

```bash
git diff --check
uv run pytest -q
make local-config
```

Expected: clean diff, full suite with zero failures, valid Compose configuration.

- [ ] **Step 3: Build and recreate the local stack**

Run:

```bash
make local-build
make local-up
```

Expected: both containers become healthy.

- [ ] **Step 4: Verify identities and shared access**

Verify primary UIDs remain `1000` and `10001`; verify Local Bot API has supplementary GID `1000`; verify it can stat/read a bot-created sentinel file below `/app/temp`; verify write creation fails on the read-only mount.

- [ ] **Step 5: Smoke-test a valid MP4 above 50 MiB**

Generate a valid approximately 62 MiB MP4 without exceeding `524288000` bytes, upload it through the running Local Bot API to the resolved private storage chat, capture a non-empty Telegram `file_id`, delete the Telegram smoke-test message, and remove the local file.

- [ ] **Step 6: Inspect post-deploy health and errors**

Check container health/restarts and recent logs. Expected: zero new `Can't get stat about the file` errors and no smoke-test ambiguous delivery record.

- [ ] **Step 7: Commit documentation**

```bash
git add docs/guides/README-Docker.md
git commit -m "docs: explain local Bot API shared media access"
```
