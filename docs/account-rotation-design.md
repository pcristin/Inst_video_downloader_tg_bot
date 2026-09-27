# Account rotation and session prewarm

## Decision

Use instagrapi 3.0's direct CAA login to create sessions before changing the bot's account roster. Preserve each account's proxy assignment and session file across bot restarts. A successful login is the readiness gate; a public media download is an additional deployment smoke check.

The 2026-09-27 preflight tried all 40 supplied accounts. Thirty-one created CAA sessions. A saved session downloaded a public Instagram photo and passed image validation. No captcha or challenge appeared in the nine failures, so a captcha solver is not justified.

## Operator flow

1. Put a candidate file outside Git. Each non-comment row is `username<TAB>password<TAB>TOTP seed` or `username|password|TOTP seed`. Seeds may contain display spaces.
2. Run `make accounts-prewarm CANDIDATES=/absolute/path/to/file`. The command validates every row, compares identities and credentials with the current roster, then tries each account lacking a usable staged session. It records a secret-free result manifest and saves successful session settings in a private staging directory. Re-running retries only missing or failed accounts.
3. Inspect the result counts. Once every candidate has a result and at least one login succeeded, run `make accounts-activate CANDIDATES=...`. The target stops the bot, installs the new roster and state, removes old sessions and old fast-auth credentials, and starts the bot. Failed candidates stay listed but unavailable; `accounts-prewarm` can retry them, followed by activation to promote successes.
4. Check container health and a public media download. Keep the staging directory private until all candidates are usable or deliberately retired.

The active roster is never replaced during prewarm. Activation accepts only a manifest bound to the exact candidate file and session files for every account marked successful. The state file carries no passwords or TOTP seeds. The fast-auth fallback is cleared rather than carrying old account cookies into the new roster.

## Implementation plan

1. Upgrade the pinned instagrapi dependency and lockfile.
2. Add validation, bounded login prewarm, redacted results, and guarded activation with focused tests.
3. Fix account Make targets that refer to tools absent from the runtime image.
4. Run unit and integration tests, open a PR, handle Codex and Cubic reviews, then merge, deploy, rotate and verify.
