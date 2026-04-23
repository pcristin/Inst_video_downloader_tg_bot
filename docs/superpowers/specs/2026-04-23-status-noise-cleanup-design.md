# Status Noise Cleanup Design

Date: 2026-04-23
Repo: `/Users/ozaytsev/Softs/Inst_video_downloader_tg_bot`
Status: Approved for planning, pending implementation approval on written spec

## Summary

Perform a narrow UX cleanup pass for Telegram chat noise after the group-first bot rollout.

Primary goals:

- remove unnecessary success-path bot messages
- eliminate duplicate or doubled request-status messages
- keep failures and cancellations visible
- review nearby request-lifecycle behavior for similar low-value chat noise
- implement and review the cleanup in a dedicated worktree using subagents for bounded UX-focused analysis

## Goals

- Delete transient status messages on successful completion.
- Prevent normal success and cache-hit flows from producing duplicate chat messages.
- Keep request-lifecycle feedback understandable without being noisy.
- Use a dedicated worktree so the cleanup remains isolated from the deployed checkout.
- Use subagents only for bounded review slices directly related to this cleanup.

## Non-Goals

- No new provider support.
- No downloader/infra redesign unless a direct cause of duplicate status behavior is found.
- No broad architecture rewrite of the Telegram bot.
- No unrelated command or admin UX polish outside this narrow request-lifecycle scope.

## User-Facing Behavior

### Success Path

On successful media delivery:

- the transient status message should be deleted
- the user should see the delivered media as the primary outcome
- no extra `Done` or `Done (cache hit)` success message should remain in chat

### Cache Hit Success

On successful cache-hit delivery:

- the transient status message should also be deleted
- cache-hit handling should remain quieter than or equal to the normal success path
- users should not receive extra celebratory or informational success chatter just because the result came from cache

### Failure Path

On failure:

- a useful visible error message should remain
- the bot must not silently delete all request feedback
- provider-specific or actionable wording should be preserved where it already exists

### Cancellation Path

On cancellation:

- a visible cancellation status may remain
- the message should be concise and unambiguous

## Known Problem Class

The current request lifecycle has multiple opportunities for chat-noise duplication:

- initial status creation through `reply_text`
- later status edits
- fallback behavior that may reply instead of edit if Telegram rejects an edit
- cache-hit completion messages that add success text even though the media itself already signals completion

The cleanup should specifically target these normal-path duplications without weakening failure visibility.

## Scope Of Review

### In Scope

- request status message creation, updates, and deletion
- edit-versus-reply fallback behavior
- cache-hit success-path UX
- coalesced/shared-job watcher UX where one underlying job serves multiple requesters
- obvious low-value command reply noise directly adjacent to this flow

### Out Of Scope

- provider extraction logic that does not affect chat noise
- account leasing changes unless they directly affect duplicate messaging
- VPS deployment mechanics
- admin features unrelated to request lifecycle noise

## Dedicated Worktree

Implementation must happen in a fresh dedicated worktree created from current `main`.

Reasons:

- keep the cleanup isolated from the already-deployed checkout
- allow safe review and experimentation without mutating the working deployment path
- make it easier to run bounded subagent work against a stable branch base

## Subagent Plan

Subagents are authorized because the user explicitly asked for a careful review with subagents.

### Subagent 1: Telegram Request Lifecycle

Focus:

- transient status creation
- edit fallback behavior
- delete-on-success flow
- duplicated visible messages in normal success path

Expected output:

- concrete findings about why duplicate messages happen
- recommended minimal behavioral fixes

### Subagent 2: Cache And Coalescing UX

Focus:

- cache-hit request behavior
- shared-job watcher behavior
- duplicate suppression outcomes
- whether attached requesters receive redundant updates or low-value noise

Expected output:

- findings about cache/coalesced flow noise
- recommended minimal cleanup changes

### Optional Subagent 3: Command Noise

Use only if needed.

Focus:

- nearby command confirmation chatter
- whether `/cancel` or related request-facing command flows add avoidable noise

Expected output:

- only direct, scoped findings relevant to this cleanup

## Implementation Strategy

1. Create the dedicated worktree from current `main`.
2. Run the bounded subagent reviews.
3. Inspect their findings and confirm the minimal patch set.
4. Implement the cleanup in the worktree.
5. Add or update tests for success, cache-hit, failure, and cancellation paths.
6. Run the relevant tests plus the full suite before publish.

## Expected Code Changes

Likely touch points:

- `src/instagram_video_bot/services/telegram_bot.py`
- tests covering Telegram request lifecycle and cache-hit behavior

Potentially touched only if directly necessary:

- `job_manager.py`
- `state_store.py`

## Validation

### Required Tests

- success deletes transient status message
- cache-hit success deletes transient status message
- failure leaves useful user-visible error feedback
- cancellation leaves concise user-visible cancellation feedback
- normal happy path does not produce doubled request-status messages

### Manual Verification Intent

If practical after implementation:

- simulate cache-hit success
- simulate non-cache success
- simulate failure
- confirm the visible chat transcript is quieter and still understandable

## Success Criteria

- The user no longer sees `Done`-style success messages after media delivery.
- Cache hits do not create extra visible completion chatter.
- The normal request lifecycle does not produce doubled messages in chat.
- Failures and cancellations remain visible and understandable.
- The cleanup stays narrow, localized, and easy to validate.
