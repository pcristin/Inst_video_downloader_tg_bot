"""Request state shared across Telegram workflow services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from collections.abc import Callable

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
    received_monotonic: float = field(default_factory=perf_counter)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    first_media_sent_monotonic: float | None = None
    all_media_sent_monotonic: float | None = None
    on_all_media_sent: Callable[[], None] | None = field(default=None, repr=False)

    def mark_first_media_sent(self) -> None:
        if self.first_media_sent_monotonic is None:
            self.first_media_sent_monotonic = perf_counter()

    def mark_all_media_sent(self) -> None:
        self.all_media_sent_monotonic = perf_counter()
        if self.on_all_media_sent is not None:
            self.on_all_media_sent()
