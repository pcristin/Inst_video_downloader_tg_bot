# Inline Promo, Rate Limits, and Refund Protection Design

## Summary

Add three safeguards around inline mode while preserving the existing free direct-chat behavior:

- A per-user abuse rate limit for accepted direct and inline request submissions.
- A lifetime promo granting each user their first three successful inline deliveries for free.
- An expiry-time subscription refund check that automatically refunds a subscription when bot/provider failures are at least 30% of that subscription period's completed inline attempts.

The implementation must keep payment decisions auditable in SQLite and must not treat invalid/unsupported user input as a bot failure.

## Goals

- Protect the bot from a single user submitting too many jobs too quickly.
- Let new users experience true inline delivery before paying.
- Build trust in paid inline mode by refunding subscriptions when the bot/provider side performs poorly.
- Send one one-time announcement to existing users about the promo and refund protection.

## Non-Goals

- Direct bot-chat usage stays free.
- No per-chat billing.
- No manual dashboard or web admin UI.
- No immediate mid-period auto-refund. Refund eligibility is checked only once the subscription period has expired.

## Rate Limit Policy

Rate limiting applies to both direct bot-chat links and inline delivery starts. The default policy is:

- `USER_RATE_LIMIT_REQUESTS=10`
- `USER_RATE_LIMIT_WINDOW_SECONDS=600`

The limiter counts accepted submissions, not failed downloads:

- Direct chat: each supported link that is accepted for queue submission consumes one event.
- Inline mode: consuming a free promo/subscription/one-time entitlement and starting delivery consumes one event.
- Plain inline search/typing does not consume rate limit events.
- Unsupported links and invalid input do not consume rate limit events.

When the limit is exceeded:

- Direct chat replies with a short "try again later" message.
- Inline selected-result/callback flow edits the inline placeholder to a "try again later" message.

## Inline Promo Policy

Each Telegram user receives three lifetime free successful inline deliveries.

- Only successful inline deliveries consume a promo credit.
- Failed deliveries do not consume a promo credit.
- Whitelisted users bypass payment and promo accounting.
- Users with an active subscription bypass promo accounting because they already paid.
- One-time payment deliveries bypass promo accounting because they already paid for that link.
- After three successful free inline deliveries, non-whitelisted users must have an active subscription or one-time payment entitlement if enabled.

## Subscription Refund Policy

Each successful subscription payment creates one subscription period record.

During that period, the bot records inline delivery outcomes:

- `success`: the inline placeholder was replaced with media.
- `failed`: the bot/provider/storage/Telegram delivery path failed after a valid inline delivery attempt started.

Invalid or user-side events are not counted as failures:

- Unsupported links.
- Expired inline sessions.
- Missing one-time entitlement.
- Payment payload mismatch.
- User rate-limit rejection.

At or after subscription expiry, the bot evaluates the period:

- `attempts = success + failed`
- `failure_rate = failed / attempts`
- If `attempts > 0` and `failure_rate >= 0.30`, the bot calls `refund_star_payment` for the subscription charge and marks the subscription `auto_refunded`.
- If Telegram refund fails, the bot marks the subscription `auto_refund_failed` and records the failure reason for admin inspection.
- If the threshold is not met, the bot marks the period `completed`.

Evaluation runs opportunistically during bot startup and when inline activity occurs. This avoids requiring a separate scheduler while still ensuring expired subscriptions are eventually checked.

## State Model

SQLite stores the new data:

- `user_rate_limit_events`: accepted direct/inline request events for sliding-window checks.
- `inline_promo_usage`: lifetime successful free inline delivery count per user.
- `inline_delivery_events`: one row per counted inline delivery outcome.
- `inline_subscriptions`: extended with `started_at`, `auto_refund_checked_at`, and `refund_reason`.

Existing databases migrate in place using `ALTER TABLE` for new subscription columns and `CREATE TABLE IF NOT EXISTS` for new tables.

## Announcement

Add a new one-time notification key and text:

- Three successful inline deliveries are free for every user.
- Inline mode remains paid after the promo.
- If at least 30% of completed inline deliveries fail during a subscription period, the subscription is automatically refunded after the period ends.

The announcement uses the existing `user_notifications` table and the same send-once behavior as the prior inline-mode announcement.

## Admin Help

`/admin_help` should mention:

- `USER_RATE_LIMIT_REQUESTS`
- `USER_RATE_LIMIT_WINDOW_SECONDS`
- Lifetime three-success inline promo.
- Expiry-time 30% subscription refund protection.

## Testing Strategy

- State-store tests for rate-limit window pruning, promo counts, delivery event recording, and expired subscription refund eligibility.
- Telegram bot tests for direct rate-limit rejection, inline rate-limit rejection, free promo access before/after three successes, promo count only on successful delivery, subscription delivery event success/failure tracking, and auto-refund behavior.
- Announcement tests for send-once behavior with the new promo notification key.
- Full test suite before commit/deploy.
