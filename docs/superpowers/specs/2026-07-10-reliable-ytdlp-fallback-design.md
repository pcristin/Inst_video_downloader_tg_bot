# Reliable yt-dlp Fallback Design

## Goal

Upgrade yt-dlp to stable `2026.7.4` and ensure the Instagram fallback always invokes the project's pinned yt-dlp module.

## Scope

- Change the pinned dependency from `yt-dlp==2026.6.9` to `yt-dlp==2026.7.4` and regenerate the lockfile.
- Replace the hard-coded `yt-dlp` executable invocation with `sys.executable -m yt_dlp`.
- Preserve the existing URL, format, proxy, cookie, timeout, error, and partial-file cleanup behavior.
- Add a regression test that proves the fallback invokes the active Python environment's yt-dlp module.

## Design

The Instagram fallback will construct its subprocess command from `sys.executable`, `-m`, and `yt_dlp`. This uses the version installed in the active project environment and removes PATH dependence. No standalone binary will be installed, and the fallback will not be converted to the yt-dlp Python API.

The dependency remains pinned to the latest stable release, `2026.7.4`, for reproducible deployments. The lockfile will record its exact resolved artifact and hashes.

## Error Handling

The fallback keeps its current behavior: failures, timeouts, and invalid result files return `None`; stderr is recorded when available; and any partial output is removed. The change is limited to executable resolution, so it does not reinterpret Instagram access failures or alter account-quarantine logic.

## Testing

The regression test will mock subprocess execution and assert the command begins with `[sys.executable, "-m", "yt_dlp"]`. Existing Instagram client/download flow tests will verify surrounding fallback behavior remains intact.

## Success Criteria

- The project declares and locks yt-dlp `2026.7.4`.
- The fallback works without a `yt-dlp` executable on PATH when the `yt_dlp` package is installed in the active Python environment.
- Targeted tests and the full test suite pass.
