# Chaos Mode Design

Date: 2026-04-23
Repo: `/Users/ozaytsev/Softs/Inst_video_downloader_tg_bot`
Status: Approved for planning

## Summary

Add an opt-in, admin-controlled `Chaos Mode` that makes the Telegram bot feel funny, loud, and distinctly Russian without changing the downloader core. The feature targets both private chats and group chats, but it is enabled per chat by admins. When enabled, the bot becomes actively chatty around real events such as queueing, duplicate suppression, cache hits, successful downloads, failures, and streak milestones.

The current bot already has the right primitives for this: provider-aware request parsing, shared-job queueing, duplicate suppression, recent-result caching, lightweight group stats, and chat-level settings. The design extends those primitives rather than introducing a parallel game system.

## Goals

- Make the bot feel memorable and entertaining in both private and group chats.
- Keep all user-facing bot text in Russian.
- Preserve the current downloader behavior and queue semantics.
- Reuse existing chat-level settings, stats, cache, and job lifecycle events.
- Keep the first implementation slice small enough to ship safely.

## Non-Goals

- Rewriting downloader/provider logic for Instagram, Twitter/X, or YouTube Shorts.
- Building a deep lore engine, weekly seasons, or complicated rivalry mechanics in v1.
- Translating internal code, logs, identifiers, or tests into Russian.
- Adding timer-based random chatter unrelated to real bot events.

## Current Codebase Fit

The feature is intentionally layered on top of the current architecture:

- `src/instagram_video_bot/services/request_parser.py` already identifies provider type and normalizes supported links.
- `src/instagram_video_bot/services/telegram_bot.py` already owns the command surface and request lifecycle messages.
- `src/instagram_video_bot/services/job_manager.py` already provides the event boundaries that matter for chatty UX: queued, running, completed, failed, cancelled, joined-existing-job, and delivery ownership handoff.
- `src/instagram_video_bot/services/state_store.py` already persists per-chat settings, request/job state, cached results, and lightweight stats.

This means Chaos Mode should be implemented as a presentation and social-state layer, not as a new execution subsystem.

## Product Rules

- Chaos Mode is disabled by default.
- Chaos Mode is enabled per chat, not per user.
- In group chats, admins control the mode.
- In private chats, the user in that dialog can toggle the mode for that private chat.
- All Telegram-facing text is written directly in Russian.
- Humor is opt-in, actively chatty, and event-driven.
- Existing useful behavior remains intact when Chaos Mode is off.

## User Experience

### Default Mode

When Chaos Mode is off, the bot stays functionally the same as today: practical, lower-noise, and utility-first.

### Chaos Mode

When Chaos Mode is on:

- queue and status messages become playful and Russian-language
- duplicate suppression becomes visible and funny instead of silently functional
- cache hits feel like instant "summons"
- successful downloads can trigger short celebration text
- failures become informative but dramatic instead of flat
- stats gain funny Russian titles and labels

The bot should feel energetic, but not random. It speaks when something real happened.

### Tone

The target tone is Russian-first, Telegram-native, short, readable, and intentionally mischievous. It should avoid direct translation of English meme slang, overlong copypasta, or hostility that turns the bot into a nuisance.

Tone differences by chat type:

- Group chat: louder, more teasing, more arena-like.
- Private chat: still funny, but more personal and less confrontational.

## Commands

### V1 Commands

- `/chaos on` - enable Chaos Mode for the current chat.
- `/chaos off` - disable Chaos Mode for the current chat.
- `/chaos status` - report whether Chaos Mode is active for the current chat.
- `/help` - rewritten in Russian and updated to mention Chaos Mode.
- `/formats` - rewritten in Russian and updated examples if needed.
- `/stats` - keeps current utility, but adds Russian chaotic labels when Chaos Mode is active.

### Deferred Commands

These are explicitly out of scope for v1:

- `/streak`
- `/legend`
- `/lore`
- `/mood`
- reply-based "summon" commands

They can be added later if v1 tone and noise levels work well in real usage.

## Event Model

Chaos Mode text is generated only for real request lifecycle events. There is no timer-based chatter.

### Event Classes

- request queued
- request joined an existing shared job
- queue or per-user limit reached
- cache hit
- download started or promoted into visible progress
- successful delivery
- failure
- cancellation
- stats response

### Provider Awareness

The renderer should be aware of:

- Instagram
- Twitter/X
- YouTube Shorts

Each provider gets slightly different Russian phrasing so the bot feels intentional instead of using one generic joke pool for everything.

## Data Model

### Chat Settings

Extend `group_settings` with:

- `chaos_mode_enabled`

Possible future fields, but not required for v1:

- `chaos_roasts_enabled`
- `chaos_stats_enabled`
- `chaos_level`

For v1, a single boolean is enough and keeps rollout simple.

### Social Stats

Add lightweight per-chat, per-user counters. These should be stored as event counters or derived stats, not as generated prose.

Recommended counters for v1:

- successful download count
- cache-hit count
- duplicate-join count
- current simple streak
- preferred provider count distribution

This data supports funny stats without binding the system to any specific wording.

### Storage Principle

Store facts, not jokes. Russian text should be rendered at response time from templates based on event type, provider, chat type, and a small amount of social context.

## Message Generation Rules

### Core Principle

The bot must be actively chatty, but still event-bounded.

### Message Strategy

- Prefer editing an existing status message instead of sending a new message.
- Use short lines for normal flow.
- Use bigger, more animated phrasing only for meaningful moments.
- Keep a small template pool per event class to avoid repetition.
- Apply provider-specific variants where useful.
- Apply chat-type variants where useful.

### Anti-Noise Rules

- Respect existing `/quiet` behavior as a stronger limiter than Chaos Mode.
- Do not emit extra messages purely for flavor if an edit covers the event.
- Add cooldowns for repeated joke categories so the same roast does not appear constantly.
- Do not create a second stream of commentary detached from request execution.

This keeps the feature consistent with the repo's recent status-noise cleanup direction while still delivering a noticeably louder personality when enabled.

## Russian Language Rule

All user-facing text must be authored directly in Russian, including:

- help text
- formats text
- queue/status text
- success/failure text
- stats labels
- duplicate/cache/streak callouts

Internal implementation can remain in English:

- variable names
- function names
- class names
- logs
- tests

This keeps maintenance practical while ensuring the product experience feels native.

## Error Handling

Chaos Mode must not change the underlying success/failure behavior of downloads.

- If the download fails, users still get a truthful failure outcome.
- If duplicate suppression merges a request into an existing job, semantics stay the same; only presentation changes.
- If cached media is reused, semantics stay the same; Chaos Mode only changes how the hit is announced.
- If Chaos Mode data is unavailable or a template path fails, the bot should fall back to plain Russian utility text rather than fail the request.

The personality layer must degrade gracefully.

## Testing Strategy

Add or extend tests in four areas:

- persistence tests for the new `chaos_mode_enabled` setting
- command tests for `/chaos on`, `/chaos off`, and `/chaos status`
- message-generation tests that verify Russian output selection for major event classes
- behavior tests that confirm normal mode remains low-noise and queue semantics are unchanged

Important invariants:

- downloader/provider behavior does not change
- duplicate suppression behavior does not change
- cache-hit behavior does not change
- command permissions stay correct for admin-controlled toggles

## V1 Scope

V1 includes:

- per-chat `chaos_mode_enabled`
- Russian rewrite of current public command/help/format text
- Russian event templates for queued, duplicate join, cache hit, success, failure, cancellation, and limit-reached states
- provider-aware flavor for Instagram, Twitter/X, and YouTube Shorts
- lightweight social stats enrichment for funny `/stats` output
- repetition control and basic anti-noise rules

V1 explicitly excludes:

- deep lore systems
- weekly titles or seasons
- rivalry engines
- battle events spanning multiple concurrent users
- large command surface expansion
- downloader-core refactors

## Recommended Implementation Order

1. Add persistent chat-level Chaos Mode setting.
2. Rewrite existing user-facing command/help/formats text into Russian.
3. Introduce a message-rendering layer for Russian event templates.
4. Attach Chaos Mode rendering to existing request lifecycle events.
5. Add lightweight social stats used by `/stats`.
6. Add test coverage for settings, commands, and message selection.

## First-Slice Success Criteria

The first release is successful if:

- admins can enable or disable Chaos Mode per chat
- the bot feels noticeably more alive when the mode is on
- all user-facing bot text reads naturally in Russian
- the bot remains usable in both private and group chats
- no downloader behavior regresses
- no large increase in noisy message spam appears outside approved event boundaries

## Planning Note

The next step after this design is a concrete implementation plan. That plan should keep v1 narrow and avoid mixing personality work with provider/downloader refactors unless tests reveal a hard dependency.
