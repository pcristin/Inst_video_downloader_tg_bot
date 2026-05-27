# Inline Promo, Rate Limits, and Refund Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user rate limits, three lifetime free successful inline deliveries, expiry-time subscription auto-refunds, and a one-time promo announcement.

**Architecture:** Add auditable SQLite primitives in `StateStore`, then integrate them at Telegram request boundaries. Direct messages and inline delivery starts share the same sliding-window limiter, while inline promo/subscription accounting remains separate from direct usage.

**Tech Stack:** Python 3.11, python-telegram-bot, SQLite, pytest, Docker Compose.

---

## File Map

- Modify `src/instagram_video_bot/config/settings.py` for rate-limit and promo/refund thresholds.
- Modify `src/instagram_video_bot/services/state_store.py` for schema, rate-limit events, promo counters, inline delivery events, and subscription refund status.
- Modify `src/instagram_video_bot/services/telegram_bot.py` for direct/inline rate-limit checks, promo access decisions, delivery outcome recording, and auto-refund evaluation.
- Modify `src/instagram_video_bot/services/chaos_text.py` for rate-limit and promo/admin help messages.
- Modify `src/instagram_video_bot/services/post_deploy_notifications.py` for the one-time promo announcement.
- Modify tests in `tests/test_state_store_true_inline.py`, `tests/test_telegram_bot_true_inline.py`, `tests/test_telegram_bot_media_send.py`, and `tests/test_post_deploy_notifications.py`.

## Task 1: State Store Primitives

**Files:**
- Modify: `src/instagram_video_bot/services/state_store.py`
- Test: `tests/test_state_store_true_inline.py`

- [ ] Write failing tests for `check_user_rate_limit`, `record_inline_promo_success`, `get_inline_promo_success_count`, `record_inline_delivery_event`, `get_subscription_delivery_stats`, and `list_expired_unchecked_inline_subscriptions`.
- [ ] Run:
  `uv run --no-sync pytest tests/test_state_store_true_inline.py -k "rate_limit or promo or delivery_event or refund_eligibility"`
  Expected: fails because methods/tables do not exist.
- [ ] Add SQLite tables:
  `user_rate_limit_events`, `inline_promo_usage`, `inline_delivery_events`.
- [ ] Extend `inline_subscriptions` migration with `started_at`, `auto_refund_checked_at`, and `refund_reason`.
- [ ] Implement focused state-store methods:
  `check_user_rate_limit(user_id, limit, window_seconds, now=None)`,
  `record_inline_promo_success(user_id)`,
  `get_inline_promo_success_count(user_id)`,
  `record_inline_delivery_event(...)`,
  `get_subscription_delivery_stats(user_id, started_at, expires_at)`,
  `list_expired_unchecked_inline_subscriptions(now=None)`,
  `mark_inline_subscription_auto_refunded(...)`,
  `mark_inline_subscription_auto_refund_failed(...)`,
  `mark_inline_subscription_refund_checked(...)`.
- [ ] Re-run focused state-store tests and make them pass.

## Task 2: Direct and Inline Rate Limits

**Files:**
- Modify: `src/instagram_video_bot/config/settings.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `src/instagram_video_bot/services/chaos_text.py`
- Test: `tests/test_telegram_bot_media_send.py`
- Test: `tests/test_telegram_bot_true_inline.py`

- [ ] Add failing tests that direct messages reject a supported link after the user exceeds the window.
- [ ] Add failing tests that an inline chosen/callback delivery over the limit edits/replies with the rate-limit message and does not schedule delivery.
- [ ] Add settings:
  `USER_RATE_LIMIT_REQUESTS=10`,
  `USER_RATE_LIMIT_WINDOW_SECONDS=600`.
- [ ] Add `ChaosText.rate_limited(retry_after_seconds)` with a short Russian user-facing message.
- [ ] In direct `handle_message`, check and consume the limiter immediately before `job_manager.submit`.
- [ ] In inline selected-result/callback flow, check and consume the limiter immediately before scheduling delivery.
- [ ] Run focused bot tests and make them pass.

## Task 3: Three Successful Inline Deliveries Free

**Files:**
- Modify: `src/instagram_video_bot/config/settings.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `src/instagram_video_bot/services/chaos_text.py`
- Test: `tests/test_telegram_bot_true_inline.py`

- [ ] Add failing tests that a non-subscribed, non-whitelisted user with 0, 1, or 2 successful promo deliveries gets "Send media here".
- [ ] Add failing test that a user with 3 successful promo deliveries gets paid options.
- [ ] Add failing test that promo count increments only after a free inline delivery succeeds.
- [ ] Add setting `INLINE_FREE_SUCCESSFUL_DELIVERIES=3`.
- [ ] Add an inline access helper in `TelegramBot` that allows access when promo success count is below the threshold.
- [ ] Mark free promo sessions distinctly enough to increment promo usage only for free promo deliveries.
- [ ] Run focused inline tests and make them pass.

## Task 4: Subscription Delivery Outcomes and Auto-Refund

**Files:**
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `src/instagram_video_bot/services/state_store.py`
- Test: `tests/test_telegram_bot_true_inline.py`

- [ ] Add failing tests that successful subscription-backed inline delivery records a success event.
- [ ] Add failing tests that bot/provider delivery failure records a failed event.
- [ ] Add failing tests that expired subscriptions with failure rate >= 30% call `refund_star_payment` and become `auto_refunded`.
- [ ] Add failing tests that expired subscriptions below 30% are marked checked/completed without refund.
- [ ] Add setting `INLINE_SUBSCRIPTION_AUTO_REFUND_FAILURE_THRESHOLD=0.30`.
- [ ] On subscription payment, store `started_at` as now and existing `expires_at`.
- [ ] Record delivery events with access kind `subscription`, `promo`, `whitelist`, or `one_time`.
- [ ] Implement opportunistic expired subscription evaluation on startup and before inline query handling.
- [ ] Run focused inline tests and make them pass.

## Task 5: Promo Announcement and Admin Help

**Files:**
- Modify: `src/instagram_video_bot/services/post_deploy_notifications.py`
- Modify: `src/instagram_video_bot/services/telegram_bot.py`
- Modify: `src/instagram_video_bot/services/chaos_text.py`
- Test: `tests/test_post_deploy_notifications.py`
- Test: `tests/test_telegram_bot_media_send.py`

- [ ] Add failing test for the promo announcement key/text send-once behavior.
- [ ] Add new announcement key `inline_promo_refund_2026_05_27`.
- [ ] Update post-init announcement sender to run both the old inline-mode announcement and the new promo/refund announcement, each send-once.
- [ ] Update `/admin_help` text with rate-limit settings, three-success promo, and 30% expiry refund protection.
- [ ] Run focused tests and make them pass.

## Task 6: Full Verification, Commit, Push, Deploy

**Files:**
- All modified files from prior tasks.

- [ ] Run full test suite:
  `uv run --no-sync pytest`
  Expected: all tests pass.
- [ ] Review diff:
  `git diff --stat`
- [ ] Stage and commit:
  `git add ...`
  `git commit -m "Add inline promo rate limits and refund protection"`
- [ ] Push:
  `git push origin main`
- [ ] Deploy:
  `docker compose up -d --build instagram-video-bot`
- [ ] Verify:
  `docker compose ps instagram-video-bot`
  `docker compose logs --tail 80 instagram-video-bot`
