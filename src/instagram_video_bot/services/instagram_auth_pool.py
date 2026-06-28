"""Instagram authenticated fast-extractor credential pool."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Literal, Optional

from ..config.settings import settings

logger = logging.getLogger(__name__)

AuthContextKind = Literal["cookie", "bearer"]
_SAFE_HTTP_REASON = re.compile(r"^http_[0-9]{3}$")
_ConfiguredPoolCacheKey = tuple[str | None, int, float, int]
_configured_pool_cache_lock = threading.Lock()
_configured_pool_cache: dict[_ConfiguredPoolCacheKey, "InstagramAuthPool"] = {}


class InstagramAuthConfigError(ValueError):
    """Raised when an Instagram auth credential file is invalid."""


@dataclass(frozen=True)
class InstagramAuthContext:
    """One redacted Instagram auth context."""

    context_id: str
    kind: AuthContextKind
    value: str = field(repr=False)

    def as_headers(self) -> dict[str, str]:
        """Return HTTP headers for this context."""
        if self.kind == "cookie":
            return {"Cookie": self.value}
        return {"Authorization": f"Bearer {self.value}"}


@dataclass(frozen=True)
class _Cooldown:
    expires_at: float
    reason: str = field(repr=False)


class InstagramAuthPool:
    """Thread-safe round-robin pool of Instagram auth contexts."""

    def __init__(
        self,
        contexts: list[InstagramAuthContext] | None = None,
        *,
        max_contexts_per_attempt: int = 2,
        cooldown_seconds: float = 900.0,
        now_fn: Callable[[], float] = monotonic,
        disabled_reason: Optional[str] = None,
    ) -> None:
        self._contexts = tuple(contexts or ())
        self._max_contexts_per_attempt = max(1, int(max_contexts_per_attempt or 1))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._now_fn = now_fn
        self._disabled_reason = disabled_reason
        self._lock = threading.Lock()
        self._cursor = 0
        self._cooldowns: dict[str, _Cooldown] = {}

    @classmethod
    def disabled(cls, reason: str = "disabled") -> "InstagramAuthPool":
        """Return a disabled pool with no usable contexts."""
        return cls([], disabled_reason=reason)

    @classmethod
    def from_file(
        cls,
        path: Path | str,
        *,
        max_contexts_per_attempt: int = 2,
        cooldown_seconds: float = 900.0,
        now_fn: Callable[[], float] = monotonic,
    ) -> "InstagramAuthPool":
        """Load a pool from a cobalt-compatible Instagram auth JSON file."""
        auth_path = Path(path)
        try:
            raw_payload = auth_path.read_text(encoding="utf-8")
            payload = json.loads(raw_payload)
        except OSError as exc:
            raise InstagramAuthConfigError("unable to read Instagram auth file") from exc
        except json.JSONDecodeError:
            raise InstagramAuthConfigError("invalid Instagram auth JSON") from None

        if not isinstance(payload, dict):
            raise InstagramAuthConfigError("Instagram auth JSON must be an object")

        contexts = _parse_contexts(payload)
        return cls(
            contexts,
            max_contexts_per_attempt=max_contexts_per_attempt,
            cooldown_seconds=cooldown_seconds,
            now_fn=now_fn,
            disabled_reason=None if contexts else "empty",
        )

    @property
    def available(self) -> bool:
        """Whether at least one context is configured and currently usable."""
        with self._lock:
            return bool(self._usable_contexts_locked())

    @property
    def disabled_reason(self) -> Optional[str]:
        """Redacted reason the pool is disabled."""
        return self._disabled_reason

    def get_contexts_for_attempt(self) -> list[InstagramAuthContext]:
        """Return a bounded, immutable-context snapshot for one extraction attempt."""
        with self._lock:
            usable = self._usable_contexts_locked()
            if not usable:
                return []

            limit = min(self._max_contexts_per_attempt, len(usable))
            selected = [
                usable[(self._cursor + offset) % len(usable)]
                for offset in range(limit)
            ]
            self._cursor = (self._cursor + limit) % len(usable)
            return list(selected)

    def mark_cooldown(self, context: InstagramAuthContext, reason: str) -> None:
        """Place one context on cooldown using only its non-secret id."""
        with self._lock:
            if context.context_id not in {item.context_id for item in self._contexts}:
                return
            expires_at = self._now_fn() + self._cooldown_seconds
            self._cooldowns[context.context_id] = _Cooldown(
                expires_at=expires_at,
                reason=_redact_reason(reason),
            )

    def _usable_contexts_locked(self) -> list[InstagramAuthContext]:
        now = self._now_fn()
        expired = [
            context_id
            for context_id, cooldown in self._cooldowns.items()
            if cooldown.expires_at <= now
        ]
        for context_id in expired:
            self._cooldowns.pop(context_id, None)

        cooling_down = set(self._cooldowns)
        return [
            context
            for context in self._contexts
            if context.context_id not in cooling_down
        ]


def load_instagram_auth_pool(
    path: Path | str | None,
    *,
    max_contexts_per_attempt: int = 2,
    cooldown_seconds: float = 900.0,
    now_fn: Callable[[], float] = monotonic,
) -> InstagramAuthPool:
    """Load an auth pool, returning a disabled pool for missing/invalid config."""
    if not path:
        return InstagramAuthPool.disabled("not_configured")

    try:
        return InstagramAuthPool.from_file(
            path,
            max_contexts_per_attempt=max_contexts_per_attempt,
            cooldown_seconds=cooldown_seconds,
            now_fn=now_fn,
        )
    except InstagramAuthConfigError as exc:
        logger.warning(
            "Instagram auth pool disabled: %s",
            exc.__class__.__name__,
            extra={"failure_class": "instagram_auth_config_invalid"},
        )
        return InstagramAuthPool.disabled("invalid_config")


def load_configured_instagram_auth_pool(
    *,
    now_fn: Callable[[], float] = monotonic,
) -> InstagramAuthPool:
    """Load the auth pool from global settings, sharing state across jobs."""
    path = settings.IG_AUTH_COOKIES_FILE
    max_contexts_per_attempt = int(settings.IG_AUTH_MAX_CONTEXTS_PER_ATTEMPT)
    cooldown_seconds = float(settings.IG_AUTH_CONTEXT_COOLDOWN_SECONDS)
    cache_key = (
        str(Path(path)) if path else None,
        max_contexts_per_attempt,
        cooldown_seconds,
        id(now_fn),
    )
    with _configured_pool_cache_lock:
        cached_pool = _configured_pool_cache.get(cache_key)
        if cached_pool is not None:
            return cached_pool

        pool = load_instagram_auth_pool(
            path,
            max_contexts_per_attempt=max_contexts_per_attempt,
            cooldown_seconds=cooldown_seconds,
            now_fn=now_fn,
        )
        _configured_pool_cache[cache_key] = pool
        return pool


def _parse_contexts(payload: dict[str, Any]) -> list[InstagramAuthContext]:
    contexts: list[InstagramAuthContext] = []
    cookies = _optional_string_list(payload, "instagram")
    bearers = _optional_string_list(payload, "instagram_bearer")

    for index, value in enumerate(cookies):
        contexts.append(
            InstagramAuthContext(
                context_id=f"cookie:{index}",
                kind="cookie",
                value=_clean_header_value(value, "instagram"),
            )
        )

    for index, value in enumerate(bearers):
        contexts.append(
            InstagramAuthContext(
                context_id=f"bearer:{index}",
                kind="bearer",
                value=_normalize_bearer_token(value),
            )
        )

    return contexts


def _optional_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise InstagramAuthConfigError(f"{key} must be a list")
    for item in value:
        if not isinstance(item, str):
            raise InstagramAuthConfigError(f"{key} entries must be strings")
    return value


def _normalize_bearer_token(raw_value: str) -> str:
    value = _clean_header_value(raw_value, "instagram_bearer")
    lower_value = value.lower()
    if lower_value.startswith("token="):
        value = value[6:].strip()
    elif lower_value.startswith("bearer "):
        value = value[7:].strip()
    return _clean_header_value(value, "instagram_bearer")


def _clean_header_value(raw_value: str, field_name: str) -> str:
    value = raw_value.strip()
    if not value:
        raise InstagramAuthConfigError(f"{field_name} contains an empty value")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InstagramAuthConfigError(f"{field_name} contains unsafe characters")
    return value


def _redact_reason(reason: str) -> str:
    if not reason:
        return "unknown"
    if _SAFE_HTTP_REASON.fullmatch(reason):
        return reason
    if reason in {"login_required", "challenge_required", "checkpoint", "rate_limited"}:
        return reason
    return "classified_failure"
