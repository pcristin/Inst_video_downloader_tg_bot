# Maximum Container Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove avoidable Telegram credential, logging, image, process, and writable-execution exposure from the repository-managed bot deployment.

**Architecture:** The bot reads its token from a service-exclusive file while the Local Bot API keeps its service-exclusive application credentials. Runtime logging becomes aggregate-only, both images use immutable inputs and exec-form helpers, and every filesystem/process restriction is proven by unit, image, and live checks before deployment.

**Tech Stack:** Python 3.11, pydantic-settings, pytest, Docker Compose, POSIX C helpers, Telegram Local Bot API, Trivy.

## Global Constraints

- Do not use the Codex Security plugin.
- Do not print, log, stage, or commit any credential value.
- Keep the upload ceiling at exactly 524,288,000 bytes (500 MiB).
- Preserve unauthenticated-first public Instagram extraction.
- Keep Telegram `api_id`/`api_hash` exclusive to the Local Bot API and `BOT_TOKEN` exclusive to the bot.
- Treat host root and Docker-daemon access as trusted boundaries.
- Do not remove package metadata to hide vulnerability results.
- Do not rewrite or force-push Git history until exposed Instagram credentials have been rotated.
- Use TDD for every source or configuration behavior change.

---

### Task 1: Bot Token File Boundary

**Files:**
- Modify: `src/instagram_video_bot/config/settings.py:20-27,130-145`
- Modify: `docker-compose.yml:12-40,56-58`
- Modify: `.env.example:1-12`
- Modify: `docs/guides/README-Docker.md:1-100`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_local_api_compose_security.py`
- Modify: `tests/test_main_environment.py`

**Interfaces:**
- Consumes: `Settings.BOT_TOKEN: str` used by startup, health checks, and `ApplicationBuilder.token`.
- Produces: `Settings.BOT_TOKEN_FILE: Optional[Path]` and resolved `Settings.BOT_TOKEN: str`.

- [ ] **Step 1: Write failing settings tests**

```python
def test_bot_token_loads_from_secret_file(tmp_path):
    token_file = tmp_path / "telegram_bot_token"
    token_file.write_text("123456789:TEST_TOKEN_VALUE\n")
    configured = Settings(BOT_TOKEN_FILE=token_file, _env_file=None)
    assert configured.BOT_TOKEN == "123456789:TEST_TOKEN_VALUE"


def test_bot_token_file_must_be_readable_and_nonempty(tmp_path):
    with pytest.raises(ValueError, match="BOT_TOKEN_FILE"):
        Settings(BOT_TOKEN_FILE=tmp_path / "missing", _env_file=None)


def test_bot_token_rejects_two_sources(tmp_path):
    token_file = tmp_path / "telegram_bot_token"
    token_file.write_text("123456789:FILE_TOKEN\n")
    with pytest.raises(ValueError, match="only one of BOT_TOKEN and BOT_TOKEN_FILE"):
        Settings(BOT_TOKEN="123456789:ENV_TOKEN", BOT_TOKEN_FILE=token_file, _env_file=None)
```

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_settings.py -k bot_token`

Expected: FAIL because `BOT_TOKEN_FILE` is not defined or read.

- [ ] **Step 3: Implement strict file loading**

```python
BOT_TOKEN: str = ""
BOT_TOKEN_FILE: Optional[Path] = None

def __init__(self, **kwargs):
    super().__init__(**kwargs)
    if self.BOT_TOKEN_FILE is not None:
        if self.BOT_TOKEN:
            raise ValueError("Configure only one of BOT_TOKEN and BOT_TOKEN_FILE")
        try:
            token = self.BOT_TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError("BOT_TOKEN_FILE is not readable") from error
        if not token or any(character.isspace() for character in token):
            raise ValueError("BOT_TOKEN_FILE must contain one non-empty token")
        self.BOT_TOKEN = token
```

Resolve the token before existing directory initialization. Error text may contain the setting name but never file contents.

- [ ] **Step 4: Verify settings GREEN**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_settings.py tests/test_main_environment.py tests/test_health_check.py`

Expected: PASS.

- [ ] **Step 5: Write a failing Compose boundary test**

```python
def test_bot_token_is_mounted_as_a_service_exclusive_file() -> None:
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text()
    bot_service = compose.split("  instagram-video-bot:\n", 1)[1].split("\nsecrets:\n", 1)[0]
    assert "BOT_TOKEN_FILE=/run/secrets/telegram_bot_token" in bot_service
    assert "source: telegram_bot_token" in bot_service
    assert "telegram_bot_token:\n    file: ./secrets/telegram_bot_token" in compose
```

- [ ] **Step 6: Verify Compose RED**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_local_api_compose_security.py::test_bot_token_is_mounted_as_a_service_exclusive_file`

Expected: FAIL because the secret is not declared.

- [ ] **Step 7: Add the bot-only secret mount and docs**

```yaml
secrets:
  - source: telegram_bot_token
    target: telegram_bot_token
  - source: instagram_auth
    target: instagram_auth.json
environment:
  - BOT_TOKEN_FILE=/run/secrets/telegram_bot_token
```

Declare `telegram_bot_token.file: ./secrets/telegram_bot_token`. Document `.env` token input as development-only and the file as the Compose production path.

- [ ] **Step 8: Validate and test**

```bash
docker compose -f docker-compose.yml -f docker-compose.local-api.yml config --quiet
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_settings.py tests/test_main_environment.py tests/test_health_check.py tests/test_local_api_compose_security.py
```

Expected: both exit 0.

- [ ] **Step 9: Commit Task 1**

```bash
git add .env.example docker-compose.yml docs/guides/README-Docker.md src/instagram_video_bot/config/settings.py tests/test_settings.py tests/test_main_environment.py tests/test_local_api_compose_security.py
git commit -m "fix: isolate bot token in a file secret"
```

---

### Task 2: Aggregate-Only Security Logging

**Files:**
- Modify: `src/instagram_video_bot/config/settings.py:147-165`
- Modify: `src/instagram_video_bot/utils/account_manager.py:21-637`
- Modify: `src/instagram_video_bot/__main__.py:58-78`
- Modify: `tests/test_account_manager.py`
- Modify: `tests/test_main_environment.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Consumes: account state and detailed status used by `manage_accounts.py`.
- Produces: logs containing counts, attempt numbers, exception classes, and normalized failure categories only.

- [ ] **Step 1: Write failing confidentiality tests**

```python
def test_account_loading_logs_only_aggregate_counts(monkeypatch, tmp_path, caplog):
    username = "SECRET_ACCOUNT_USERNAME"
    proxy = "http://SECRET_USER:SECRET_PASS@secret-proxy.example:8123"
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text(f"{username}|SECRET_PASSWORD|SECRET_TOTP\n")
    monkeypatch.setattr(settings, "get_proxy_list", lambda: [proxy])
    with caplog.at_level(logging.INFO):
        manager = AccountManager(accounts_file, tmp_path / "state.json")
    assert len(manager.accounts) == 1
    for secret in (username, proxy, "SECRET_USER", "SECRET_PASS", "secret-proxy.example"):
        assert secret not in caplog.text
    assert "Loaded 1 accounts total" in caplog.text
```

Add equivalent sentinel tests for setup, rotate, unavailable, reset-old, application startup, and invalid proxy settings. Assert raw exception messages never enter logs.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_account_manager.py tests/test_settings.py tests/test_main_environment.py -k 'log or proxy or unavailable or reset_old or startup'`

Expected: FAIL on username, proxy host, detailed status, raw proxy, and exception text.

- [ ] **Step 3: Implement categories and aggregates**

Use records shaped like:

```python
logger.info("Loaded %d accounts total", len(self.accounts))
logger.info("Setting up Instagram account")
logger.info("Instagram account login succeeded")
logger.error("Instagram account setup failed", extra={"error_class": error.__class__.__name__, "failure_category": category})
logger.info("Kept %d replacement-required accounts unavailable", replacement_required_count)
```

Never interpolate username, proxy, raw reason, or `str(error)`. Normalize reasons to `auth`, `challenge`, `rate_limit`, `login`, `replacement_required`, or `other`. Keep detailed status data for explicit local admin CLI use. Replace the raw invalid-proxy log in `Settings.get_proxy_list` with `Skipping invalid proxy definition`. Remove `logger.info(manager.get_detailed_status())` from startup.

- [ ] **Step 4: Verify GREEN and run all account tests**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_account_manager.py tests/test_main_environment.py tests/test_settings.py
```

Expected: PASS with no sentinel in logs.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/instagram_video_bot/__main__.py src/instagram_video_bot/config/settings.py src/instagram_video_bot/utils/account_manager.py tests/test_account_manager.py tests/test_main_environment.py tests/test_settings.py
git commit -m "fix: remove account metadata from runtime logs"
```

---

### Task 3: Immutable and Tool-Minimized Runtime Images

**Files:**
- Create: `scripts/telegram_bot_api_entrypoint.c`
- Create: `scripts/http_healthcheck.c`
- Remove: `scripts/telegram_bot_api_entrypoint.sh`
- Modify: `Dockerfile`
- Modify: `Dockerfile.telegram-bot-api`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.local-api.yml`
- Modify: `tests/test_dockerfile_security.py`
- Modify: `tests/test_telegram_bot_api_entrypoint.py`
- Modify: `tests/test_local_api_compose_security.py`

**Interfaces:**
- Consumes: `/run/secrets/telegram_api_id`, `/run/secrets/telegram_api_hash`, and API arguments.
- Produces: native `telegram-bot-api-entrypoint` and `http-healthcheck` executables without shell or curl dependencies.

- [ ] **Step 1: Write failing native-helper tests**

Compile with:

```python
subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", source, "-o", output], check=True)
```

Test correct child inheritance without stdout/stderr disclosure plus missing, empty, oversized, symlink, and multiline secret rejection. Test the probe against a temporary TCP server returning `HTTP/1.1 404 Not Found`; a closed port must fail.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_telegram_bot_api_entrypoint.py`

Expected: FAIL because the C sources do not exist.

- [ ] **Step 3: Implement native helpers**

Entrypoint signatures:

```c
static int read_secret(const char *path, char *buffer, size_t capacity);
static void clear_buffer(char *buffer, size_t length);
int main(int argc, char **argv);
```

Open secrets with `O_RDONLY | O_CLOEXEC | O_NOFOLLOW`, require a regular file of 1-4096 bytes, trim one newline, reject multiline values, never write values, set the two required environment variables, clear local buffers, and `execvp("telegram-bot-api", child_argv)`. The health probe connects to `127.0.0.1:8081` with a three-second timeout, sends `GET /`, and accepts a response beginning `HTTP/`.

- [ ] **Step 4: Verify helper GREEN**

Run the Step 2 command again. Expected: PASS.

- [ ] **Step 5: Write failing Docker/Compose policy tests**

Assert pinned `python`, `uv`, and `debian` references; no final-stage curl; C helper compilation; explicit users `1000:1000` and `10001:10001`; and `noexec,nosuid,nodev` tmpfs options. Require final built-image probes for absent shell/package-manager executables.

- [ ] **Step 6: Verify policy RED**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_dockerfile_security.py tests/test_local_api_compose_security.py`

Expected: FAIL on current tags, shell helper, curl, users, and tmpfs flags.

- [ ] **Step 7: Pin images and compile helpers**

```bash
docker pull python:3.11-slim
docker pull debian:bookworm-slim
docker pull ghcr.io/astral-sh/uv:0.12.0
docker image inspect python:3.11-slim debian:bookworm-slim ghcr.io/astral-sh/uv:0.12.0 --format '{{json .RepoDigests}}'
```

Use tag plus immutable digest in every external reference. Compile with `-std=c11 -O2 -D_FORTIFY_SOURCE=2 -fstack-protector-strong -Wformat -Werror=format-security -fPIE -pie`. Remove curl from the API final stage and use exec-form native healthcheck.

- [ ] **Step 8: Reduce command surfaces and tighten tmpfs**

After all build commands, remove final-image `/bin/sh`, `/bin/dash`, and executable `apt`/`dpkg` frontends while retaining `/var/lib/dpkg` metadata. Apply explicit Compose users and:

```yaml
tmpfs:
  - /tmp:size=256m,mode=1770,uid=1000,gid=1000,noexec,nosuid,nodev
```

Use UID/GID `10001` for API tmpfs. Restore only `/bin/sh` if direct runtime tests prove a required dependency; retain every other reduction.

- [ ] **Step 9: Verify policy GREEN, Compose, and images**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_dockerfile_security.py tests/test_telegram_bot_api_entrypoint.py tests/test_local_api_compose_security.py
docker compose -f docker-compose.yml -f docker-compose.local-api.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local-api.yml build --pull
docker run --rm --entrypoint /app/.venv/bin/python inst_video_downloader_tg_bot-instagram-video-bot:latest -c 'import src.instagram_video_bot.__main__; print("application_import=ok")'
```

Use direct entrypoints to prove shell, apt, dpkg, global pip, and curl absence; then prove bot health, ffmpeg invocation, API health, and clean SIGTERM shutdown.

- [ ] **Step 10: Commit Task 3**

```bash
git add Dockerfile Dockerfile.telegram-bot-api docker-compose.yml docker-compose.local-api.yml scripts/telegram_bot_api_entrypoint.c scripts/http_healthcheck.c scripts/telegram_bot_api_entrypoint.sh tests/test_dockerfile_security.py tests/test_local_api_compose_security.py tests/test_telegram_bot_api_entrypoint.py
git commit -m "fix: minimize and pin runtime containers"
```

---

### Task 4: Atomic Live Migration and Final Audit

**Files:**
- Modify outside Git: `.env`
- Create outside Git: `secrets/telegram_bot_token`
- Update outside Git: `/tmp/local-security-audit.md`

**Interfaces:**
- Consumes: current `.env` `BOT_TOKEN` and final Compose configuration.
- Produces: root-protected token file, `.env` without `BOT_TOKEN`, healthy services, and secret-free local audit evidence.

- [ ] **Step 1: Run the complete pre-migration suite**

```bash
BASE_DIR=/tmp/codex-test-runtime \
TEMP_DIR=/tmp/codex-test-runtime/temp \
CACHE_DIR=/tmp/codex-test-runtime/cache \
STATE_DB_PATH=/tmp/codex-test-runtime/bot_state.sqlite3 \
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests
```

Expected: all tests pass with only documented upstream warnings.

- [ ] **Step 2: Migrate atomically without printing the token**

Parse only the first `BOT_TOKEN=` assignment without sourcing `.env`; reject missing, empty, or multiline data; write and rename a temporary file below root-owned `0700` `secrets/`; set the token file `0444 root:root`; then remove the `.env` assignment through a same-directory temporary file. Preserve `.env` as `0600 root:root`. Failure before final rename leaves the original `.env` intact.

- [ ] **Step 3: Verify sanitized effective configuration**

Render Compose to JSON and output only environment key names and secret source paths. Confirm the bot has `BOT_TOKEN_FILE`, lacks `BOT_TOKEN`, and exclusively mounts `telegram_bot_token`.

- [ ] **Step 4: Reconcile without deleting state**

Run: `docker compose -f docker-compose.yml -f docker-compose.local-api.yml up -d --force-recreate`

Do not run `down -v`, delete `telegram-bot-api-data`, or call cloud `logOut`.

- [ ] **Step 5: Verify live isolation and behavior**

Inspect only sanitized users, health, restarts, read-only roots, privileges, capabilities, security options, PID limits, ports, environment key names, mounts, and networks. Require healthy services, zero restarts, no `BOT_TOKEN` metadata, token-safe `getMe` HTTP 200/`ok=true`, and the public Instagram regression proving no account acquisition on public recovery.

- [ ] **Step 6: Scan exported images without Docker socket access**

```bash
mkdir -p /tmp/dependency-audit
docker save -o /tmp/dependency-audit/bot.tar inst_video_downloader_tg_bot-instagram-video-bot:latest
docker save -o /tmp/dependency-audit/api.tar inst_video_downloader_tg_bot-telegram-bot-api:latest
docker run --rm \
  -v inst_video_downloader_tg_bot_trivy-cache:/root/.cache/trivy \
  -v /tmp/dependency-audit:/reports:rw \
  aquasec/trivy:0.66.0 image --quiet --format json \
  --output /reports/bot.json --input /reports/bot.tar
docker run --rm \
  -v inst_video_downloader_tg_bot_trivy-cache:/root/.cache/trivy \
  -v /tmp/dependency-audit:/reports:rw \
  aquasec/trivy:0.66.0 image --skip-db-update --skip-java-db-update \
  --quiet --format json --output /reports/api.json --input /reports/api.tar
```

Summarize severity and fixability with `jq`. Delete only `/tmp/dependency-audit` and `inst_video_downloader_tg_bot_trivy-cache`; keep Telegram API state untouched.

- [ ] **Step 7: Re-run Git credential checks**

Require zero exact current bot-token/API-hash matches in every Git blob, zero private-key headers, and zero high-confidence staged-patch matches. Confirm `.env`, accounts, sessions, state, and `secrets/` remain ignored/untracked.

- [ ] **Step 8: Final verification and audit update**

Update `/tmp/local-security-audit.md`; run `git diff --check`, C compiler checks, full pytest, Compose validation, live health, and post-commit secret scanning. Report any unavailable lint tool rather than claiming it ran.

## Residual External Actions

1. Recover or replace the 183 challenged Instagram accounts for authenticated media.
2. Rotate Instagram password, email credential, TOTP seed, cookies, and sessions exposed in commits `8a97370` and `728bbe4`.
3. After rotation, obtain explicit destructive approval for `git filter-repo` and force push.
4. Schedule separate host maintenance for rootless Docker and destination-restricted egress firewall rules.
