# Inline Session Actions and Group URL Recognition Design

**Date:** 2026-07-25

**Status:** Approved

## Objective

Add safe Cancel and Retry actions to true inline deliveries, including paid one-time deliveries, while preserving the existing payment, authorization, and duplicate-delivery guarantees. Confirm ordinary supported URLs are processed in Telegram groups and supergroups, and surface Telegram privacy-mode configuration when it prevents the bot from receiving those messages.

## Scope

This change covers true inline delivery sessions created by inline queries. It does not replace or modify the existing `job:*` action protocol used by directly pasted links. It covers the `free`, `promo`, `subscription`, and `one_time` inline access kinds, with additional entitlement rules for one-time Stars payments.

Group URL recognition covers ordinary, unedited text messages in groups and supergroups. Telegram BotFather privacy mode is an external delivery constraint: the application will diagnose and document it, but cannot override it.

## Existing Behavior and Root Cause

Direct link requests create persisted request events and visible status messages. Their status messages receive `job:cancel:<request_id>` and, for safely retryable failures, `job:retry:<request_id>` keyboards.

True inline delivery uses a separate `inline_sessions` lifecycle. The selected article initially contains a `Preparing` fallback button, and `_deliver_inline_session` later replaces the placeholder with media. It neither creates a standard request action record nor supplies a session-action keyboard to the inline edits. Consequently, the recent direct-request Cancel and Retry feature does not apply to inline delivery.

The group request-intake code already accepts effective messages from any chat type, and the application registers a general non-command text handler. Telegram privacy mode can prevent ordinary, unmentioned group messages from reaching that handler, which is indistinguishable from missing URL recognition without an explicit diagnostic.

## Chosen Architecture

Use a session-native inline action layer. Do not create synthetic standard jobs and do not generalize both action systems in this change.

A focused `telegram/inline_actions.py` module will own:

- A compact inline action enum or equivalent typed representation.
- Strict callback construction and parsing within Telegram's 64-byte limit.
- Localized single-button Cancel and Retry keyboards.

The callback namespace will be distinct from `job:*`, and a dedicated callback handler will be registered before the generic text handler. The original `inline:<session_token>` and `inline_once:<session_token>` callbacks remain the fallback that starts a newly selected inline result when chosen-result feedback is unavailable. Once delivery is claimed, the visible keyboard changes from the fallback `Preparing` action to Cancel.

The standard direct-request job action implementation remains unchanged.

## Persisted Session State

Inline action decisions must be correct after a restart and under concurrent callback taps. The `inline_sessions` schema will persist enough state to make every transition conditional and atomic:

- Current session status.
- Current live delivery stage.
- Whether the latest failure is safely retryable.
- Delivery attempt count.
- Existing failure class, failure stage, and error class metadata.

The state store will expose narrowly scoped transactional operations rather than read-then-write decisions in handlers. Required operations include:

- Claiming the initial delivery for the session owner and exact inline message.
- Advancing the live delivery stage.
- Atomically claiming a retry only from a retryable failed session.
- Atomically cancelling only before final inline handoff.
- Marking success, retryable failure, terminal failure, ambiguous delivery, or cancellation exactly once.
- Looking up a claimed one-time payment by session token.

Repeated or concurrent action callbacks return a stale/already-handled result and do not create another task, refund, or delivery attempt.

## Authorization

Only the Telegram user who created the inline session may Cancel or Retry it. This remains true when the inline result is posted into a group or supergroup where any member can see and tap the button.

The callback must also refer to the inline message already attached to the session. A user mismatch, inline-message mismatch, malformed callback, expired session, or disallowed state transition is rejected without changing session or payment state.

The standard group authorization rules for direct `job:*` actions are not reused because inline payments and entitlements belong to the originating inline user.

## Delivery State Machine

The intended state flow is:

1. `created`: the inline query result exists but has not been claimed.
2. `delivering`: chosen-result feedback or the existing fallback callback attaches the inline message and starts an attempt.
3. Live stages advance through `preflight`, `download`, `storage_upload`, and `inline_edit`.
4. A successful media edit transitions to `delivered` and removes all action buttons.
5. A safely retryable failure transitions to `failed` with `failure_retryable = true` and replaces the message with a Retry button.
6. Retry atomically transitions the same session back to `delivering`, increments the attempt count, clears current failure metadata, and starts one new task.
7. A certain non-retryable failure transitions to terminal `failed`, removes actions, and performs any required one-time refund.
8. A safe user cancellation transitions to `cancelled`, removes actions, stops local work, and performs any required one-time refund.
9. An ambiguous final Telegram edit transitions to `delivery_unknown`, removes actions, and is left for owner review.

The bot will maintain an in-memory mapping from session token to active delivery task for prompt cooperative cancellation. Persisted stages remain authoritative, allowing callbacks after a restart to make safe decisions even when no local task exists.

## Cancellation Boundary

Cancel is available while the attempt is in preflight, download, or storage upload. The cancellation transition is committed before the local task is cancelled, making duplicate callback taps harmless.

Immediately before calling Telegram's final `editMessageMedia`, the state store atomically advances the session to `inline_edit`. At and after this boundary, Cancel is rejected. Telegram may accept the edit even if the client later receives a timeout, so cancelling or refunding at that point could give the user both media and returned Stars.

Cleanup of temporary download files remains unconditional.

## Failure and Retry Safety

Retry is shown only when replay cannot create an ambiguous duplicate in the destination chat.

- Transient provider/download failures may be retryable according to the existing failure classification policy.
- A storage upload failure may be retried when the resulting user-chat delivery has not begun. Duplicate staging objects in the private storage chat are acceptable; duplicate user deliveries are not.
- A certain validation, configuration, unsupported-media, expired-session, authorization, or payment failure is terminal.
- A network or timeout error during `inline_edit` is treated as `delivery_unknown`. It receives no Retry button and no automatic refund.

Failure presentation will distinguish retryable, terminal, cancelled, and unknown outcomes instead of using the current blanket text that says every one-time failure was refunded.

## One-Time Stars Entitlement

One Stars charge purchases exactly one successful delivery of the normalized URL to which the payment is bound. The payment claim is resolved from SQLite by session token for every action; it is never inferred solely from an in-memory argument.

- Initial delivery claims the paid entitlement for its session.
- Retryable failures retain that claim and do not refund it.
- All retries reuse the same session, normalized URL, and payment claim.
- The first confirmed successful delivery marks the payment delivered and permanently consumes it.
- A safe user cancellation refunds the payment exactly once.
- A certain terminal failure refunds the payment exactly once.
- `delivery_unknown` neither marks the payment delivered nor refunds it automatically. The owner can inspect and use the existing administrative refund mechanism when appropriate.
- A refunded, refund-failed, or delivered payment cannot be used to start or retry a delivery.
- Stale-claim recovery must not release a session that is actively delivering or awaiting retry. It may recover an abandoned safe-stage session according to the configured recovery window, but must preserve ambiguous and terminal outcomes.

These rules prevent retry-after-refund, multiple deliveries from one charge, duplicate refunds, and double consumption.

## Subscription, Promo, and Free Access

Subscription sessions may retry safely without a new charge. Subscription delivery accounting records one final success or failure for the session; intermediate retryable attempt failures do not inflate the failure rate used by the existing subscription refund policy. A cancelled session is not counted as a provider delivery failure.

Promo access remains success-based: the lifetime promo credit is consumed only after confirmed delivery. Retrying or cancelling does not consume it.

Whitelisted/free access follows the same action and retry safety rules without payment transitions.

## Inline Message Updates

Inline text and media edit helpers will accept an explicit replacement keyboard:

- The claimed preparing state shows Cancel.
- A retryable certain failure shows Retry.
- Success, cancellation, terminal failure, expiration, and unknown delivery remove the keyboard.

The final `editMessageMedia` call will explicitly set the intended reply markup rather than depending on Telegram's behavior when `reply_markup` is omitted.

## Group and Supergroup URL Recognition

Regression tests will exercise ordinary supported URLs from both `group` and `supergroup` chats through the registered message handler and request intake. They will verify that the effective chat ID, effective user or sender-chat identity, group settings, status reply, and Cancel keyboard are preserved.

At application startup, the bot will inspect Telegram's bot identity capability when available. If `can_read_all_group_messages` is false, it will log an actionable warning that ordinary group URLs may be withheld and that BotFather `/setprivacy` must be disabled (or the bot granted the necessary administrative visibility). The README troubleshooting section will include the same instruction and note that the bot may need to be removed and re-added after the setting changes.

No application-side filter can compensate for an update Telegram never sends.

## Error Handling and Recovery

Callbacks always answer promptly with an authorized result, already-handled notice, expired notice, unsafe-action notice, or permission denial. User-facing edits are best effort; persisted state and payment transitions do not roll back merely because a cosmetic edit fails.

Refund API errors continue to use the persisted refund-failed state and remain visible to owner tooling. Delivery tasks always discard their active-token bookkeeping and remove temporary files in `finally` blocks. Task cancellation is handled separately from ordinary exceptions so it cannot be misclassified as a retryable provider failure.

On process restart, persisted session status and live stage determine whether the user can cancel, retry, or must request owner review. No action relies on the old process retaining a task object.

## Testing Strategy

Tests will be written before production changes and will cover:

- Callback construction, parsing, malformed input, localization, and the 64-byte limit.
- State-store migrations and atomic initial claim, retry, cancel, success, terminal, and unknown transitions.
- Repeated and concurrent Cancel/Retry callbacks.
- Session-owner authorization and inline-message binding in private chats, groups, and supergroups.
- Cancel during download and storage upload.
- Rejected Cancel once final inline edit begins.
- Retryable provider/download and storage failures.
- Terminal failures without Retry.
- Ambiguous Telegram inline-edit timeout with no Retry and no automatic refund.
- One-time payment retained across retries, consumed once on success, and refunded once on safe cancellation or terminal failure.
- Rejection of Retry for refunded, refund-failed/pending, delivered, expired, or wrong-link entitlements.
- Retry after constructing a new bot instance from the same state database.
- Final-only subscription accounting and success-only promo consumption.
- Explicit keyboard replacement/removal on every visible outcome.
- Registered callback ordering and group/supergroup URL recognition.
- Startup behavior with privacy capability true, false, or absent.

Focused tests will be followed by the complete test suite and a whitespace/diff validation.

## Non-Goals

- Changing inline pricing or subscription policy.
- Replacing the direct-request `job:*` action implementation.
- Automatically refunding ambiguous Telegram deliveries.
- Remotely changing BotFather privacy settings.
- Processing edited group messages or unrelated media captions as part of this fix.

## Success Criteria

- Inline users see Cancel while a claimed delivery is safely cancellable.
- Safely retryable inline failures show Retry and start at most one new attempt.
- Buttons disappear after success or any terminal/unknown outcome.
- Only the inline session owner can act, including in groups.
- One-time payment state cannot yield a free retry, duplicate delivery, duplicate refund, or multiple successful deliveries.
- Subscription and promo accounting retain their intended semantics across retries.
- Ordinary supported URLs are proven to enter request intake in groups and supergroups when Telegram supplies the update.
- Operators receive a clear diagnostic when Telegram privacy mode may withhold those group messages.
