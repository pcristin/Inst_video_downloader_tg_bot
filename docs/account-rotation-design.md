# Account rotation and session prewarm

## Decision

Use instagrapi 3.0's direct CAA login to create sessions before changing the bot's account roster. Preserve each account's proxy assignment and session file across bot restarts. A successful login plus a real public media download with that saved session is the readiness gate. New accounts then enter a limited traffic ramp.

The 2026-09-27 preflight tried all 40 supplied accounts. Thirty-one created CAA sessions on the first pass and 34 after a targeted retry. A saved session downloaded a public Instagram photo and passed image validation. No captcha or challenge appeared in the failures, so a captcha solver is not justified.

## Operator flow

1. Put a candidate file outside Git, owned by the operator with mode `0600` (`chmod 600 /path/to/file`). The CLI rejects broader permissions. Each non-comment row is `username<TAB>password<TAB>TOTP seed` or `username|password|TOTP seed`. Seeds may contain display spaces; an empty seed is recorded as unavailable.
2. Run `make accounts-prewarm CANDIDATES=/absolute/path/to/file`. The command validates every row, compares identities and credentials with the current roster, then tries each account lacking an authenticated staged session. It records a secret-free result manifest and saves successful session settings in a private staging directory. Imported sessions are checked with Instagram's authenticated account endpoint and their account identity must match the candidate. Re-running rechecks staged sessions and retries failures.
3. Run `make accounts-canary CANDIDATES=... CANARY_URL=https://www.instagram.com/p/<public-photo-shortcode>/` with a stable public photo or video post. The command uses each saved session and its assigned proxy to verify account identity, fetch media metadata, and download one real file. It records redacted results bound to the exact session file. Re-run it after a temporary failure or any renewed login. Accounts remain outside the bot during this stage. A canary proves this download path works now; it does not build account trust through synthetic activity.
4. Inspect the result counts. Once every candidate has a prewarm result and every successful login has a passing canary, run `make accounts-activate CANDIDATES=...`. For a deployment using the Local Bot API override, add `COMPOSE='docker compose -f docker-compose.yml -f docker-compose.local-api.yml'`. The target checks runtime access, stops the bot, rechecks every successful session and its identity, installs the new roster and state, removes old and failed sessions and old fast-auth credentials, and recreates the bot container so its file bind mount sees the new roster. Failed candidates stay listed but unavailable; `accounts-prewarm` and `accounts-canary` can retry them, followed by activation to promote successes.
5. Check container health and a public media download. Keep the staging directory private until all candidates are usable or deliberately retired.

The active roster is never replaced during prewarm or canary. Activation accepts only manifests bound to the exact candidate file and session files for every account marked successful. The state file carries no passwords or TOTP seeds. The fast-auth fallback is cleared rather than carrying old account cookies into the new roster. New accounts are limited to one bot lease per five minutes during their first 24 hours and one per minute through hour 72. The limit then expires. Legacy accounts without an activation timestamp are unaffected. If every account is cooling down, authenticated downloads may wait for the configured lease window and then fail; this favors account health over immediate use.

## Implementation plan

1. Upgrade the pinned instagrapi dependency and lockfile.
2. Add validation, bounded login prewarm, redacted results, and guarded activation with focused tests.
3. Fix account Make targets that refer to tools absent from the runtime image.
4. Run unit and integration tests, open a PR, handle Codex and Cubic reviews, then merge, deploy, rotate and verify.
