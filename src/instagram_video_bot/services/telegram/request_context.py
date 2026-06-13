"""Request state shared across Telegram workflow services."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import Message


@dataclass
class RequestContext:
    """Telegram state for one user request tied to a shared job."""

    request_id: str
    chat_id: int
    user_id: int
    provider_label: str
    normalized_url: str
    original_url: str
    original_message_id: int
    status_message: Message
    quiet_mode: bool
    joined_existing: bool
    chaos_enabled: bool = False
    language_code: str = "ru"

