# UV Migration Design

Date: 2026-04-23
Repo: `/Users/ozaytsev/Softs/Inst_video_downloader_tg_bot`
Status: Approved design, pending user review before implementation

## Summary

Migrate the repository from `pip` + `requirements.txt` to a fully `uv`-native Python project. After migration, `pyproject.toml` and `uv.lock` are the only dependency source of truth. Local development, test execution, documentation, and Docker builds all use `uv`.

## Goals

- Replace `requirements.txt` with `pyproject.toml` and `uv.lock`.
- Keep `uv` artifacts as the only dependency source of truth.
- Preserve the existing `src/instagram_video_bot` application layout.
- Update local developer workflow to `uv sync` and `uv run`.
- Update Docker build/install flow to use the lockfile-driven `uv` workflow.
- Update Makefile and README instructions so they match the new workflow.

## Non-Goals

- No application feature changes.
- No bot runtime behavior changes beyond dependency/install management.
- No Compose topology changes.
- No opportunistic refactors unrelated to packaging, install flow, or command entrypoints.

## Current State

The repository currently uses:

- `requirements.txt` as the dependency source of truth.
- direct `pip install -r requirements.txt` in the Dockerfile.
- local setup instructions based on `python -m venv` plus `pip install`.
- README and Makefile commands that assume direct Python execution rather than `uv run`.

This creates multiple problems:

- dependency resolution is separated from execution tooling.
- Docker and local installs are more likely to drift over time.
- there is no lockfile controlling reproducible installs across environments.
- `requirements.txt` is a weaker long-term fit for grouped dependencies and tool configuration.

## Chosen Approach

Use a standard `uv` project layout with:

- `pyproject.toml` for project metadata, dependencies, tool configuration, and Python version constraints.
- `uv.lock` for locked dependency resolution.
- runtime dependencies defined in the main dependency set.
- development-only tools defined in a `dev` dependency group.

This is the preferred middle path:

- more durable than a minimal script-only migration.
- lower churn than a wheel-first packaging redesign.
- aligned with the user's request for a full `uv` migration with `uv` artifacts as the single source of truth.

## Target Project Shape

### Python Project Metadata

Add a new `pyproject.toml` that defines:

- project name and version metadata suitable for the current repo.
- supported Python version, aligned with the repo's stated Python 3.11+ support.
- runtime dependencies currently sourced from `requirements.txt`.
- a `dev` dependency group for formatting, linting, typing, and test tools.

The existing `src/instagram_video_bot` layout remains unchanged. The project becomes installable from that layout rather than from an external requirements file.

### Lockfile

Generate and commit `uv.lock`.

The lockfile becomes mandatory for:

- local developer syncs.
- CI-style test execution in the local repo.
- Docker image builds.

### Removal of Legacy Dependency Artifacts

Remove `requirements.txt` after `pyproject.toml` and `uv.lock` are in place.

No secondary generated compatibility file will remain in the repository. This avoids split-brain dependency ownership.

## Dependency Model

### Runtime Dependencies

Move current application dependencies from `requirements.txt` into `project.dependencies`, including:

- `python-telegram-bot`
- `instagrapi`
- `pydantic`
- `pydantic-settings`
- `python-dotenv`
- `yt-dlp`
- `pyotp`
- `requests`
- `aiofiles`
- `tabulate`
- `pillow`

### Development Dependencies

Move current development tools into a `dev` group, including:

- `black`
- `isort`
- `mypy`
- `pylint`
- `pytest`
- `pytest-asyncio`

This keeps runtime installs lean while preserving the existing toolchain.

## Local Workflow

### Developer Setup

The local developer workflow becomes:

```bash
uv sync
uv run pytest -q
uv run python -m src.instagram_video_bot
```

Add `.python-version` so local interpreter selection is explicit and consistent with the project metadata.

### Command Execution

Commands documented for local development should use `uv run` rather than direct interpreter calls when they rely on project-managed dependencies.

Examples:

- `uv run pytest -q`
- `uv run python -m src.instagram_video_bot`
- `uv run python manage_accounts.py status`

## Docker And Compose

### Dockerfile

The Dockerfile is updated to:

- install `uv`.
- copy `pyproject.toml` and `uv.lock` before application code for caching.
- run a frozen sync for runtime dependencies only.
- preserve the existing non-root user, working directory, temp/session directory creation, and module entrypoint semantics.

The Docker build should not fall back to `pip` or `requirements.txt`.

### Compose

Compose structure stays the same. Only command/install assumptions change through the Docker image build and runtime entrypoint.

No volume or environment topology changes are intended. The runtime command and health-check command may be updated to use `uv run` so the container executes within the `uv`-managed environment.

## Makefile Changes

Update the Makefile so repo-facing commands reflect the new source of truth.

Expected categories of change:

- local setup/help text updated from `pip` to `uv`.
- local developer-facing Python commands updated to `uv run ...`.
- container-facing Python commands updated only where the `uv`-managed environment requires it.
- no unrelated target redesign.

Docker Compose commands themselves can remain intact unless a target is specifically tied to the old install path.

## Documentation Changes

Update README and any obviously impacted setup docs so they reflect:

- `uv sync` instead of `python -m venv` + `pip install -r requirements.txt`
- `uv run ...` instead of direct Python commands where project dependencies are needed
- removal of references to `requirements.txt` as the dependency source of truth

The documentation changes should be narrow and limited to the migration surface.

## Validation Plan

Implementation will be considered complete when all of the following pass:

1. `uv sync`
2. `uv run pytest -q`
3. Docker image build using the updated Dockerfile

Validation focus:

- lockfile-driven dependency installation works locally
- test execution works through `uv run`
- container build works without `pip` or `requirements.txt`

## Risks And Mitigations

### Risk: Docker install semantics differ from the current `pip` path

Mitigation:

- keep the Docker runtime layout stable.
- use `uv sync --frozen --no-dev` or an equivalent `uv` production install path.
- solve any container path issues inside the `uv` flow rather than reintroducing `pip`.

### Risk: Documentation and helper commands drift from actual workflow

Mitigation:

- update README and Makefile in the same migration.
- validate commands using the actual `uv` workflow before finalizing.

### Risk: Hidden dependency assumptions appear only after lockfile generation

Mitigation:

- treat `uv.lock` as authoritative.
- verify with a full local sync and test run after the migration.

## Implementation Boundaries

Files expected to change during implementation:

- `pyproject.toml`
- `uv.lock`
- `.python-version`
- `Dockerfile`
- `Makefile`
- `README.md`
- any small, directly impacted Docker/setup docs
- removal of `requirements.txt`

Files not expected to change:

- application logic under `src/`
- test logic except where a command reference is documentation-only
- Compose service architecture

## Expected Outcome

After implementation:

- the repository is `uv`-native.
- `pyproject.toml` and `uv.lock` are the only dependency source of truth.
- local development and tests run through `uv`.
- Docker builds install from the same locked dependency graph.
- legacy `pip` and `requirements.txt` workflow references are removed.
