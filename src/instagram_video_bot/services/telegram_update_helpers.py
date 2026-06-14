"""Small helpers for extracting Telegram update identity and command arguments."""

from __future__ import annotations

from typing import Any


def user_label(update: Any) -> str:
    """Return a compact label for an effective Telegram user."""

    user = update.effective_user
    if user is None:
        return "unknown"
    if user.username:
        return f"@{user.username}"
    if user.full_name:
        return user.full_name
    return str(user.id)


def request_user_id(update: Any) -> int | None:
    """Return the requester user ID, falling back to sender-chat ID."""

    if update.effective_user is not None:
        return update.effective_user.id
    message = update.effective_message
    sender_chat = getattr(message, "sender_chat", None) if message else None
    if sender_chat is not None:
        return getattr(sender_chat, "id", None)
    return None


def request_user_label(update: Any) -> str:
    """Return a human-readable requester label for users or sender chats."""

    if update.effective_user is not None:
        return user_label(update)
    message = update.effective_message
    sender_chat = getattr(message, "sender_chat", None) if message else None
    if sender_chat is None:
        return "unknown"
    title = getattr(sender_chat, "title", None)
    if title:
        return str(title)
    username = getattr(sender_chat, "username", None)
    if username:
        return f"@{username}"
    sender_chat_id = getattr(sender_chat, "id", None)
    return str(sender_chat_id) if sender_chat_id is not None else "unknown"


def language_from_profile(language_code: str | None) -> str:
    """Map Telegram profile language codes to supported bot language codes."""

    normalized = (language_code or "").strip().lower()
    if normalized == "ru" or normalized.startswith("ru-"):
        return "ru"
    return "en"


def forwarded_visible_user_id(message: Any) -> int | None:
    """Return a visible forwarded user ID from old or new Telegram message shapes."""

    forward_from = getattr(message, "forward_from", None)
    if getattr(forward_from, "id", None) is not None:
        return int(forward_from.id)
    forward_origin = getattr(message, "forward_origin", None)
    sender_user = getattr(forward_origin, "sender_user", None)
    if getattr(sender_user, "id", None) is not None:
        return int(sender_user.id)
    return None


def parse_toggle_arg(value: str) -> bool | None:
    """Parse the on/off command tokens supported by group-setting commands."""

    normalized = value.strip().lower()
    if normalized in {"on", "enable", "enabled", "true", "1"}:
        return True
    if normalized in {"off", "disable", "disabled", "false", "0"}:
        return False
    return None


def parse_positive_int_arg(value: str) -> int | None:
    """Parse a strictly positive integer command token."""

    stripped = value.strip()
    if not stripped.isascii() or not stripped.isdigit():
        return None
    parsed = int(stripped)
    if parsed <= 0:
        return None
    return parsed
