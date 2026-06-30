"""SQLite-backed bot state, cache, and lightweight stats."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config.settings import settings
from .state_schema import initialize_state_schema

logger = logging.getLogger(__name__)
USER_NOTIFICATION_STATUSES = frozenset(
    {"attempted", "sent", "failed", "retryable_failed"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CachedMediaEntry:
    """Cached media lookup result."""

    title: str
    media_items: list[dict[str, Any]]
    created_at: datetime
    expires_at: datetime


class StateStore:
    """Persistent storage for jobs, requests, recent results, and settings."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.STATE_DB_PATH
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            initialize_state_schema(self._conn)

    def get_user_language(self, user_id: int) -> str | None:
        """Return a user's explicit language preference, if one exists."""
        with self._lock:
            row = self._conn.execute(
                "SELECT language_code FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None or not row["language_code"]:
            return None
        return str(row["language_code"])

    def set_user_language(self, user_id: int, language_code: str) -> None:
        """Persist a user's explicit language preference."""
        if language_code not in {"en", "ru"}:
            raise ValueError("language_code must be 'en' or 'ru'")
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_settings (user_id, language_code, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    language_code = excluded.language_code,
                    updated_at = excluded.updated_at
                """,
                (user_id, language_code, now),
            )

    def ensure_group_settings(self, chat_id: int) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO group_settings (chat_id, quiet_mode, duplicate_suppression, stats_enabled)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO NOTHING
                """,
                (
                    chat_id,
                    0,
                    1 if settings.DUPLICATE_SUPPRESSION_ENABLED else 0,
                    1 if settings.GROUP_STATS_ENABLED else 0,
                ),
            )
            row = self._conn.execute(
                """
                SELECT quiet_mode, duplicate_suppression, stats_enabled, chaos_mode_enabled,
                       chat_max_concurrent_jobs, user_max_active_jobs
                FROM group_settings
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        assert row is not None
        return {
            "quiet_mode": bool(row["quiet_mode"]),
            "duplicate_suppression": bool(row["duplicate_suppression"]),
            "stats_enabled": bool(row["stats_enabled"]),
            "chaos_mode_enabled": bool(row["chaos_mode_enabled"]),
            "chat_max_concurrent_jobs": (
                int(row["chat_max_concurrent_jobs"])
                if row["chat_max_concurrent_jobs"] is not None
                else settings.CHAT_MAX_CONCURRENT_JOBS
            ),
            "user_max_active_jobs": (
                int(row["user_max_active_jobs"])
                if row["user_max_active_jobs"] is not None
                else settings.USER_MAX_ACTIVE_JOBS
            ),
        }

    def update_group_settings(self, chat_id: int, **updates: Any) -> dict[str, Any]:
        """Persist a subset of group settings and return the resulting row."""
        allowed = {
            "quiet_mode": "quiet_mode",
            "duplicate_suppression": "duplicate_suppression",
            "stats_enabled": "stats_enabled",
            "chaos_mode_enabled": "chaos_mode_enabled",
            "chat_max_concurrent_jobs": "chat_max_concurrent_jobs",
            "user_max_active_jobs": "user_max_active_jobs",
        }
        assignments = []
        values: list[Any] = []
        for key, value in updates.items():
            column = allowed.get(key)
            if not column:
                continue
            assignments.append(f"{column} = ?")
            if key in {
                "quiet_mode",
                "duplicate_suppression",
                "stats_enabled",
                "chaos_mode_enabled",
            }:
                values.append(1 if value else 0)
            else:
                values.append(value)
        if not assignments:
            return self.ensure_group_settings(chat_id)

        self.ensure_group_settings(chat_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE group_settings SET {', '.join(assignments)} WHERE chat_id = ?",
                (*values, chat_id),
            )
        return self.ensure_group_settings(chat_id)

    def get_queue_limits(self, chat_id: int) -> dict[str, int]:
        """Return effective queue limits for a chat."""
        settings_row = self.ensure_group_settings(chat_id)
        return {
            "chat_max_concurrent_jobs": settings_row["chat_max_concurrent_jobs"],
            "user_max_active_jobs": settings_row["user_max_active_jobs"],
        }

    def reconcile_interrupted_jobs(self, *, reason: str = "process_restarted") -> int:
        """Cancel persisted active jobs that cannot survive a process restart."""
        now = _utc_now().isoformat()
        active_statuses = ("queued", "running")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    error_class = COALESCE(error_class, ?),
                    finished_at = COALESCE(finished_at, ?)
                WHERE status IN (?, ?)
                """,
                (reason, now, *active_statuses),
            )
            interrupted_count = cursor.rowcount
            self._conn.execute(
                """
                UPDATE request_events
                SET status = 'cancelled',
                    updated_at = ?
                WHERE status IN (?, ?)
                """,
                (now, *active_statuses),
            )
            self._conn.execute(
                """
                UPDATE performance_metrics
                SET status = 'cancelled',
                    failure_class = COALESCE(failure_class, ?),
                    finished_at = COALESCE(finished_at, ?)
                WHERE status IN (?, ?)
                """,
                (reason, now, *active_statuses),
            )
        return int(interrupted_count)

    def get_stale_active_job_count(
        self,
        *,
        older_than_seconds: float,
        chat_id: int | None = None,
    ) -> int:
        """Return active persisted jobs older than the allowed runtime window."""
        cutoff = (
            _utc_now() - timedelta(seconds=max(0.0, older_than_seconds))
        ).isoformat()
        with self._lock:
            if chat_id is None:
                row = self._conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM jobs
                    WHERE status = 'running'
                      AND COALESCE(started_at, created_at) < ?
                    """,
                    (cutoff,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM jobs
                    WHERE chat_id = ?
                      AND status = 'running'
                      AND COALESCE(started_at, created_at) < ?
                    """,
                    (chat_id, cutoff),
                ).fetchone()
        return int(row["count"] or 0)

    def get_recent_provider_timeout_count(
        self,
        *,
        chat_id: int | None = None,
        window_seconds: float = 60 * 60,
    ) -> int:
        """Return recent provider timeout failures from performance metrics."""
        cutoff = (_utc_now() - timedelta(seconds=max(0.0, window_seconds))).isoformat()
        with self._lock:
            if chat_id is None:
                row = self._conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM performance_metrics
                    WHERE status = 'failed'
                      AND failure_class = 'provider_timeout'
                      AND finished_at >= ?
                    """,
                    (cutoff,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM performance_metrics
                    WHERE chat_id = ?
                      AND status = 'failed'
                      AND failure_class = 'provider_timeout'
                      AND finished_at >= ?
                    """,
                    (chat_id, cutoff),
                ).fetchone()
        return int(row["count"] or 0)

    def create_job(
        self, job_id: str, chat_id: int, normalized_url: str, provider: str, status: str
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO jobs (job_id, chat_id, normalized_url, provider, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, chat_id, normalized_url, provider, status, now),
            )

    def update_job_status(
        self, job_id: str, status: str, error_class: str | None = None
    ) -> None:
        now = _utc_now().isoformat()
        started_at = now if status == "running" else None
        finished_at = now if status in {"completed", "failed", "cancelled"} else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    error_class = COALESCE(?, error_class),
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at)
                WHERE job_id = ?
                """,
                (status, error_class, started_at, finished_at, job_id),
            )

    def create_request(
        self,
        request_id: str,
        job_id: str,
        chat_id: int,
        user_id: int,
        user_label: str,
        provider: str,
        normalized_url: str,
        status: str,
        joined_existing: bool = False,
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO request_events
                    (request_id, job_id, chat_id, user_id, user_label, provider, normalized_url, status, joined_existing, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    job_id,
                    chat_id,
                    user_id,
                    user_label,
                    provider,
                    normalized_url,
                    status,
                    1 if joined_existing else 0,
                    now,
                    now,
                ),
            )

    def update_request_status(
        self, request_id: str, status: str, cache_hit: bool = False
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE request_events
                SET status = ?,
                    cache_hit = CASE WHEN ? THEN 1 ELSE cache_hit END,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (status, 1 if cache_hit else 0, now, request_id),
            )

    def list_distinct_request_user_ids(self) -> list[int]:
        """Return users who have submitted at least one request."""

        with self._lock:
            rows = self._conn.execute("""
                SELECT DISTINCT user_id
                FROM request_events
                ORDER BY user_id
                """).fetchall()
        return [int(row["user_id"]) for row in rows]

    def notification_was_attempted(self, notification_key: str, user_id: int) -> bool:
        """Return whether this notification has already been attempted for the user."""

        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM user_notifications
                WHERE notification_key = ?
                  AND user_id = ?
                """,
                (notification_key, user_id),
            ).fetchone()
        return row is not None

    def notification_should_skip(self, notification_key: str, user_id: int) -> bool:
        """Return whether this notification has a terminal or in-flight attempt."""

        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM user_notifications
                WHERE notification_key = ?
                  AND user_id = ?
                  AND status IN ('attempted', 'sent', 'failed')
                """,
                (notification_key, user_id),
            ).fetchone()
        return row is not None

    def notification_was_sent(self, notification_key: str, user_id: int) -> bool:
        """Return whether this notification was sent successfully."""

        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM user_notifications
                WHERE notification_key = ?
                  AND user_id = ?
                  AND status = 'sent'
                """,
                (notification_key, user_id),
            ).fetchone()
        return row is not None

    def record_user_notification(
        self,
        *,
        notification_key: str,
        user_id: int,
        status: str,
        error_class: str | None = None,
    ) -> None:
        """Record a one-time notification attempt."""

        if status not in USER_NOTIFICATION_STATUSES:
            raise ValueError(f"Invalid user notification status: {status}")

        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_notifications (
                    notification_key, user_id, status, error_class, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(notification_key, user_id) DO UPDATE SET
                    status = excluded.status,
                    error_class = excluded.error_class,
                    updated_at = excluded.updated_at
                """,
                (notification_key, user_id, status, error_class, now, now),
            )

    def start_job_metrics(
        self,
        job_id: str,
        chat_id: int,
        provider: str,
        normalized_url: str,
    ) -> None:
        now = _utc_now().isoformat()
        self._safe_metrics_write(
            """
            INSERT INTO performance_metrics (
                job_id, chat_id, provider, normalized_url, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                provider = excluded.provider,
                normalized_url = excluded.normalized_url
            """,
            (job_id, chat_id, provider, normalized_url, "queued", now),
        )

    def mark_job_metrics_started(self, job_id: str) -> None:
        now = _utc_now().isoformat()
        self._safe_metrics_write(
            """
            UPDATE performance_metrics
            SET status = 'running',
                started_at = COALESCE(started_at, ?)
            WHERE job_id = ?
            """,
            (now, job_id),
        )

    def record_cache_hit(self, job_id: str) -> None:
        self._safe_metrics_write(
            """
            UPDATE performance_metrics
            SET cache_hit = 1
            WHERE job_id = ?
            """,
            (job_id,),
        )

    def record_download_metrics(
        self,
        job_id: str,
        *,
        download_duration_ms: int,
        retry_count: int = 0,
        instagram_fast_status: str | None = None,
        instagram_fast_duration_ms: int | None = None,
        instagram_fast_budget_exhausted: bool = False,
        instagram_fast_endpoint_timings_json: str | None = None,
        instagram_fallback_attempted: bool = False,
        instagram_account_attempts: int = 0,
        instagram_account_retries: int = 0,
        instagram_auth_failures: int = 0,
        instagram_success_path: str | None = None,
        instagram_fallback_path: str | None = None,
        instagram_metadata_reused: bool = False,
        failure_class: str | None = None,
    ) -> None:
        self._safe_metrics_write(
            """
            UPDATE performance_metrics
            SET download_duration_ms = ?,
                retry_count = ?,
                instagram_fast_status = ?,
                instagram_fast_duration_ms = ?,
                instagram_fast_budget_exhausted = ?,
                instagram_fast_endpoint_timings_json = ?,
                instagram_fallback_attempted = ?,
                instagram_account_attempts = ?,
                instagram_account_retries = ?,
                instagram_auth_failures = ?,
                instagram_success_path = ?,
                instagram_fallback_path = ?,
                instagram_metadata_reused = ?,
                failure_class = COALESCE(?, failure_class)
            WHERE job_id = ?
            """,
            (
                download_duration_ms,
                retry_count,
                instagram_fast_status,
                instagram_fast_duration_ms,
                1 if instagram_fast_budget_exhausted else 0,
                instagram_fast_endpoint_timings_json,
                1 if instagram_fallback_attempted else 0,
                instagram_account_attempts,
                instagram_account_retries,
                instagram_auth_failures,
                instagram_success_path,
                instagram_fallback_path,
                1 if instagram_metadata_reused else 0,
                failure_class,
                job_id,
            ),
        )

    def record_delivery_metrics(
        self, job_id: str, *, delivery_duration_ms: int
    ) -> None:
        self._safe_metrics_write(
            """
            UPDATE performance_metrics
            SET delivery_duration_ms = ?
            WHERE job_id = ?
            """,
            (delivery_duration_ms, job_id),
        )

    def finalize_job_metrics(self, job_id: str, *, status: str) -> None:
        now = _utc_now().isoformat()
        self._safe_metrics_write(
            """
            UPDATE performance_metrics
            SET status = ?,
                finished_at = COALESCE(finished_at, ?)
            WHERE job_id = ?
            """,
            (status, now, job_id),
        )

    def get_performance_summary(
        self, chat_id: int | None, limit: int = 50
    ) -> dict[str, Any]:
        with self._lock:
            if chat_id is None:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM performance_metrics
                    ORDER BY finished_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM performance_metrics
                    WHERE chat_id = ?
                    ORDER BY finished_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (chat_id, limit),
                ).fetchall()

        total_jobs = len(rows)
        cache_hits = sum(1 for row in rows if row["cache_hit"])
        providers: dict[str, dict[str, Any]] = {}
        for row in rows:
            provider = row["provider"]
            provider_summary = providers.setdefault(
                provider,
                {
                    "jobs": 0,
                    "avg_queue_wait_ms": 0,
                    "avg_download_ms": 0,
                    "avg_delivery_ms": 0,
                    "_queue_wait_durations": [],
                    "_download_durations": [],
                    "_delivery_durations": [],
                },
            )
            provider_summary["jobs"] += 1
            queue_wait_ms = self._duration_between_ms(
                row["created_at"], row["started_at"]
            )
            if queue_wait_ms is not None:
                provider_summary["_queue_wait_durations"].append(queue_wait_ms)
            if row["download_duration_ms"] is not None:
                provider_summary["_download_durations"].append(
                    row["download_duration_ms"]
                )
            if row["delivery_duration_ms"] is not None:
                provider_summary["_delivery_durations"].append(
                    row["delivery_duration_ms"]
                )

        for provider_summary in providers.values():
            provider_summary["avg_queue_wait_ms"] = self._safe_average(
                provider_summary.pop("_queue_wait_durations")
            )
            provider_summary["avg_download_ms"] = self._safe_average(
                provider_summary.pop("_download_durations")
            )
            provider_summary["avg_delivery_ms"] = self._safe_average(
                provider_summary.pop("_delivery_durations")
            )

        delivery_durations = [
            row["delivery_duration_ms"]
            for row in rows
            if row["delivery_duration_ms"] is not None
        ]
        queue_wait_durations = [
            queue_wait_ms
            for row in rows
            if (
                queue_wait_ms := self._duration_between_ms(
                    row["created_at"], row["started_at"]
                )
            )
            is not None
        ]
        instagram_rows = [row for row in rows if row["provider"] == "instagram"]
        failure_classes = sorted(
            {
                row["failure_class"]
                for row in rows
                if row["failure_class"] and row["status"] == "failed"
            }
        )
        return {
            "total_jobs": total_jobs,
            "cache_hits": cache_hits,
            "cache_hit_rate": cache_hits / total_jobs if total_jobs else 0.0,
            "providers": providers,
            "avg_queue_wait_ms": self._safe_average(queue_wait_durations),
            "avg_delivery_ms": self._safe_average(delivery_durations),
            "failure_classes": failure_classes,
            "instagram": {
                "fast_failed": sum(
                    1
                    for row in instagram_rows
                    if row["instagram_fast_status"] == "failed"
                ),
                "fallback_count": sum(
                    1 for row in instagram_rows if row["instagram_fallback_attempted"]
                ),
                "fast_budget_exhausted": sum(
                    1
                    for row in instagram_rows
                    if row["instagram_fast_budget_exhausted"]
                ),
                "fallback_paths": {
                    path: sum(
                        1
                        for row in instagram_rows
                        if row["instagram_fallback_path"] == path
                    )
                    for path in sorted(
                        {
                            row["instagram_fallback_path"]
                            for row in instagram_rows
                            if row["instagram_fallback_path"]
                        }
                    )
                },
                "metadata_reused": sum(
                    1 for row in instagram_rows if row["instagram_metadata_reused"]
                ),
                "account_retries": sum(
                    int(row["instagram_account_retries"] or 0) for row in instagram_rows
                ),
                "auth_failures": sum(
                    int(row["instagram_auth_failures"] or 0) for row in instagram_rows
                ),
            },
        }

    def create_inline_session(
        self,
        *,
        session_token: str,
        user_id: int,
        original_url: str,
        normalized_url: str,
        provider: str,
        provider_label: str,
        expires_at: datetime,
        access_kind: str = "free",
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_sessions (
                    session_token, user_id, original_url, normalized_url, provider,
                    provider_label, access_kind, status, created_at, expires_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_token) DO UPDATE SET
                    user_id = excluded.user_id,
                    original_url = excluded.original_url,
                    normalized_url = excluded.normalized_url,
                    provider = excluded.provider,
                    provider_label = excluded.provider_label,
                    access_kind = excluded.access_kind,
                    status = excluded.status,
                    failure_class = NULL,
                    failure_stage = NULL,
                    error_class = NULL,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    session_token,
                    user_id,
                    original_url,
                    normalized_url,
                    provider,
                    provider_label,
                    access_kind,
                    "created",
                    now,
                    self._normalize_utc_datetime(expires_at).isoformat(),
                    now,
                ),
            )

    def get_inline_session(
        self,
        session_token: str,
        *,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if user_id is None:
                row = self._conn.execute(
                    "SELECT * FROM inline_sessions WHERE session_token = ?",
                    (session_token,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT *
                    FROM inline_sessions
                    WHERE session_token = ? AND user_id = ?
                    """,
                    (session_token, user_id),
                ).fetchone()
        return dict(row) if row is not None else None

    def attach_inline_message(
        self, session_token: str, *, inline_message_id: str
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_sessions
                SET inline_message_id = ?,
                    status = 'chosen',
                    updated_at = ?
                WHERE session_token = ?
                """,
                (inline_message_id, now, session_token),
            )

    def mark_inline_session_status(self, session_token: str, status: str) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_sessions
                SET status = ?,
                    updated_at = ?
                WHERE session_token = ?
                """,
                (status, now, session_token),
            )

    def mark_inline_session_failed(
        self,
        session_token: str,
        *,
        failure_class: str,
        failure_stage: str,
        error_class: str | None,
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_sessions
                SET status = 'failed',
                    failure_class = ?,
                    failure_stage = ?,
                    error_class = ?,
                    updated_at = ?
                WHERE session_token = ?
                """,
                (
                    failure_class,
                    failure_stage,
                    error_class,
                    now,
                    session_token,
                ),
            )

    def add_inline_whitelist_user(
        self,
        user_id: int,
        *,
        added_by_user_id: int,
        note: str | None = None,
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_whitelist (user_id, added_by_user_id, note, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    added_by_user_id = excluded.added_by_user_id,
                    note = excluded.note,
                    created_at = excluded.created_at
                """,
                (user_id, added_by_user_id, note, now),
            )

    def remove_inline_whitelist_user(self, user_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM inline_whitelist WHERE user_id = ?", (user_id,)
            )

    def list_inline_whitelist_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("""
                SELECT *
                FROM inline_whitelist
                ORDER BY created_at DESC
                """).fetchall()
        return [dict(row) for row in rows]

    def is_inline_whitelisted(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM inline_whitelist WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def record_inline_subscription(
        self,
        *,
        user_id: int,
        expires_at: datetime,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str,
        total_amount: int,
        started_at: datetime | None = None,
    ) -> None:
        now = _utc_now().isoformat()
        normalized_started_at = self._normalize_utc_datetime(
            started_at or _utc_now()
        ).isoformat()
        normalized_expires_at = self._normalize_utc_datetime(expires_at).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_subscriptions (
                    user_id, status, started_at, expires_at, telegram_payment_charge_id,
                    provider_payment_charge_id, total_amount, auto_refund_checked_at,
                    refund_reason, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    status = excluded.status,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at,
                    telegram_payment_charge_id = excluded.telegram_payment_charge_id,
                    provider_payment_charge_id = excluded.provider_payment_charge_id,
                    total_amount = excluded.total_amount,
                    auto_refund_checked_at = excluded.auto_refund_checked_at,
                    refund_reason = excluded.refund_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    "active",
                    normalized_started_at,
                    normalized_expires_at,
                    telegram_payment_charge_id,
                    provider_payment_charge_id,
                    total_amount,
                    None,
                    None,
                    now,
                ),
            )

    def get_inline_subscription(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inline_subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_inline_subscription_by_charge_id(
        self,
        telegram_payment_charge_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM inline_subscriptions
                WHERE telegram_payment_charge_id = ?
                LIMIT 1
                """,
                (telegram_payment_charge_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_inline_subscription_refunded(self, user_id: int) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_subscriptions
                SET status = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                ("refunded", now, user_id),
            )

    def mark_inline_subscription_auto_refunded(
        self, user_id: int, *, reason: str
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_subscriptions
                SET status = ?,
                    auto_refund_checked_at = ?,
                    refund_reason = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                ("auto_refunded", now, reason, now, user_id),
            )

    def mark_inline_subscription_auto_refund_failed(
        self, user_id: int, *, reason: str
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_subscriptions
                SET status = ?,
                    auto_refund_checked_at = ?,
                    refund_reason = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                ("auto_refund_failed", now, reason, now, user_id),
            )

    def mark_inline_subscription_refund_checked(
        self, user_id: int, *, reason: str
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_subscriptions
                SET status = ?,
                    auto_refund_checked_at = ?,
                    refund_reason = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                ("completed", now, reason, now, user_id),
            )

    def list_expired_unchecked_inline_subscriptions(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now_value = self._normalize_utc_datetime(now or _utc_now()).isoformat()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM inline_subscriptions
                WHERE status = 'active'
                  AND expires_at <= ?
                  AND auto_refund_checked_at IS NULL
                ORDER BY expires_at ASC
                """,
                (now_value,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_active_inline_subscription(self, user_id: int) -> bool:
        row = self.get_inline_subscription(user_id)
        if row is None or row["status"] != "active":
            return False
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            return False
        expires_at = self._normalize_utc_datetime(expires_at)
        return expires_at > _utc_now()

    def user_has_inline_access(self, user_id: int) -> bool:
        return self.is_inline_whitelisted(
            user_id
        ) or self.has_active_inline_subscription(user_id)

    def check_user_rate_limit(
        self,
        user_id: int,
        *,
        limit: int,
        window_seconds: int,
        now: datetime | None = None,
        source: str = "request",
    ) -> dict[str, Any]:
        now_dt = self._normalize_utc_datetime(now or _utc_now())
        window_start = now_dt - timedelta(seconds=max(0, window_seconds))
        now_iso = now_dt.isoformat()
        window_start_iso = window_start.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM user_rate_limit_events WHERE created_at <= ?",
                (window_start_iso,),
            )
            rows = self._conn.execute(
                """
                SELECT created_at
                FROM user_rate_limit_events
                WHERE user_id = ? AND created_at > ?
                ORDER BY created_at ASC
                """,
                (user_id, window_start_iso),
            ).fetchall()
            if len(rows) >= max(1, limit):
                oldest = datetime.fromisoformat(str(rows[0]["created_at"]))
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=timezone.utc)
                retry_after = max(
                    1,
                    int(
                        (
                            oldest + timedelta(seconds=window_seconds) - now_dt
                        ).total_seconds()
                    ),
                )
                return {"allowed": False, "retry_after_seconds": retry_after}
            self._conn.execute(
                """
                INSERT INTO user_rate_limit_events (event_id, user_id, source, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, user_id, source, now_iso),
            )
        return {"allowed": True, "retry_after_seconds": 0}

    def record_inline_promo_success(self, user_id: int) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_promo_usage (user_id, success_count, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    success_count = success_count + 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, 1, now),
            )

    def get_inline_promo_success_count(self, user_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT success_count FROM inline_promo_usage WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["success_count"]) if row is not None else 0

    def record_inline_delivery_event(
        self,
        *,
        user_id: int,
        session_token: str,
        access_kind: str,
        status: str,
        occurred_at: datetime | None = None,
    ) -> None:
        now = self._normalize_utc_datetime(occurred_at or _utc_now()).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_delivery_events (
                    event_id, user_id, session_token, access_kind, status, occurred_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, user_id, session_token, access_kind, status, now),
            )

    def get_subscription_delivery_stats(
        self,
        *,
        user_id: int,
        started_at: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        started_iso = self._normalize_utc_datetime(started_at).isoformat()
        expires_iso = self._normalize_utc_datetime(expires_at).isoformat()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM inline_delivery_events
                WHERE user_id = ?
                  AND access_kind = 'subscription'
                  AND occurred_at >= ?
                  AND occurred_at <= ?
                  AND status IN ('success', 'failed')
                GROUP BY status
                """,
                (user_id, started_iso, expires_iso),
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        success = counts.get("success", 0)
        failed = counts.get("failed", 0)
        attempts = success + failed
        failure_rate = failed / attempts if attempts else 0.0
        return {
            "success": success,
            "failed": failed,
            "attempts": attempts,
            "failure_rate": failure_rate,
        }

    def get_inline_runtime_settings(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "subscription_stars": settings.INLINE_SUBSCRIPTION_STARS,
            "one_time_enabled": settings.INLINE_ONE_TIME_ENABLED,
            "one_time_stars": settings.INLINE_ONE_TIME_STARS,
        }
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM inline_runtime_settings"
            ).fetchall()
        for row in rows:
            if row["key"] in {"subscription_stars", "one_time_stars"}:
                try:
                    values[row["key"]] = int(row["value"])
                except (TypeError, ValueError):
                    continue
            elif row["key"] == "one_time_enabled":
                parsed = self._parse_runtime_bool(row["value"])
                if parsed is not None:
                    values[row["key"]] = parsed
        return values

    def update_inline_runtime_settings(
        self,
        *,
        subscription_stars: int | None = None,
        one_time_enabled: bool | None = None,
        one_time_stars: int | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, str] = {}
        if subscription_stars is not None:
            updates["subscription_stars"] = str(subscription_stars)
        if one_time_enabled is not None:
            updates["one_time_enabled"] = "1" if one_time_enabled else "0"
        if one_time_stars is not None:
            updates["one_time_stars"] = str(one_time_stars)

        now = _utc_now().isoformat()
        with self._lock, self._conn:
            for key, value in updates.items():
                self._conn.execute(
                    """
                    INSERT INTO inline_runtime_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )
        return self.get_inline_runtime_settings()

    def record_inline_one_time_payment(
        self,
        *,
        user_id: int,
        session_token: str,
        telegram_payment_charge_id: str,
        total_amount: int,
        provider: str | None = None,
        normalized_url: str | None = None,
    ) -> str:
        payment_id = uuid.uuid4().hex
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_one_time_payments (
                    payment_id, user_id, session_token, provider, normalized_url, telegram_payment_charge_id,
                    total_amount, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id,
                    user_id,
                    session_token,
                    provider,
                    normalized_url,
                    telegram_payment_charge_id,
                    total_amount,
                    "paid",
                    now,
                    now,
                ),
            )
        return payment_id

    def get_inline_one_time_payment(self, payment_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inline_one_time_payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_inline_one_time_payment_by_charge_id(
        self,
        telegram_payment_charge_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM inline_one_time_payments
                WHERE telegram_payment_charge_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (telegram_payment_charge_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_available_inline_one_time_payment(
        self,
        *,
        user_id: int,
        provider: str,
        normalized_url: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT p.*
                FROM inline_one_time_payments p
                LEFT JOIN inline_sessions s
                    ON s.session_token = p.session_token
                WHERE p.user_id = ?
                  AND p.status = 'paid'
                  AND p.request_id IS NULL
                  AND (
                    (p.provider = ? AND p.normalized_url = ?)
                    OR (
                        p.provider IS NULL
                        AND p.normalized_url IS NULL
                        AND s.provider = ?
                        AND s.normalized_url = ?
                    )
                  )
                ORDER BY p.created_at ASC
                LIMIT 1
                """,
                (user_id, provider, normalized_url, provider, normalized_url),
            ).fetchone()
        return dict(row) if row is not None else None

    def claim_inline_one_time_payment(
        self, payment_id: str, *, request_id: str
    ) -> bool:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE inline_one_time_payments
                SET request_id = ?,
                    updated_at = ?
                WHERE payment_id = ?
                  AND status = 'paid'
                  AND request_id IS NULL
                """,
                (request_id, now, payment_id),
            )
        return cursor.rowcount == 1

    def release_stale_inline_one_time_claims(self, *, older_than: datetime) -> int:
        cutoff = self._normalize_utc_datetime(older_than).isoformat()
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE inline_one_time_payments
                SET request_id = NULL,
                    updated_at = ?
                WHERE status = 'paid'
                  AND request_id IS NOT NULL
                  AND updated_at <= ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM inline_sessions s
                    WHERE s.session_token = substr(inline_one_time_payments.request_id, length('inline:') + 1)
                      AND s.status = 'delivering'
                      AND s.updated_at > ?
                  )
                """,
                (now, cutoff, cutoff),
            )
        return cursor.rowcount

    def mark_inline_one_time_payment_delivered(
        self,
        payment_id: str,
        *,
        request_id: str,
    ) -> None:
        self._mark_inline_one_time_payment(
            payment_id,
            status="delivered",
            request_id=request_id,
            refund_reason=None,
        )

    def mark_inline_one_time_payment_refunded(
        self, payment_id: str, *, reason: str
    ) -> None:
        self._mark_inline_one_time_payment(
            payment_id,
            status="refunded",
            request_id=None,
            refund_reason=reason,
        )

    def mark_inline_one_time_payment_refund_failed(
        self,
        payment_id: str,
        *,
        reason: str,
    ) -> None:
        self._mark_inline_one_time_payment(
            payment_id,
            status="refund_failed",
            request_id=None,
            refund_reason=reason,
        )

    def save_inline_cached_media(
        self,
        *,
        cache_key: str,
        provider: str,
        normalized_url: str,
        media_items: list[dict[str, Any]],
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO inline_cached_media (
                    cache_key, provider, normalized_url, media_json, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    normalized_url = excluded.normalized_url,
                    media_json = excluded.media_json,
                    created_at = excluded.created_at
                """,
                (cache_key, provider, normalized_url, json.dumps(media_items), now),
            )

    def get_inline_cached_media(self, cache_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inline_cached_media WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        cached = dict(row)
        cached["media_items"] = json.loads(cached.pop("media_json"))
        return cached

    def get_cached_result(
        self, chat_id: int, normalized_url: str
    ) -> CachedMediaEntry | None:
        now = _utc_now().isoformat()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT title, media_json, created_at, expires_at
                FROM recent_results
                WHERE cache_key = ? AND expires_at > ?
                """,
                (self._cache_key(chat_id, normalized_url), now),
            ).fetchone()
        if row is None:
            return None

        media_items = json.loads(row["media_json"])
        for media_item in media_items:
            if not Path(media_item["file_path"]).exists():
                return None
        return CachedMediaEntry(
            title=row["title"],
            media_items=media_items,
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    def save_cached_result(
        self,
        chat_id: int,
        normalized_url: str,
        provider: str,
        title: str,
        media_items: list[dict[str, Any]],
        ttl_seconds: int,
    ) -> None:
        created_at = _utc_now()
        expires_at = created_at + timedelta(seconds=ttl_seconds)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO recent_results (cache_key, chat_id, normalized_url, provider, title, media_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    title = excluded.title,
                    media_json = excluded.media_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    self._cache_key(chat_id, normalized_url),
                    chat_id,
                    normalized_url,
                    provider,
                    title,
                    json.dumps(media_items),
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )

    def update_cached_telegram_file_ids(
        self,
        chat_id: int,
        normalized_url: str,
        telegram_file_ids: list[str | None],
    ) -> None:
        """Persist Telegram file IDs for cached media so repeats can skip uploads."""
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT media_json
                FROM recent_results
                WHERE cache_key = ?
                """,
                (self._cache_key(chat_id, normalized_url),),
            ).fetchone()
            if row is None:
                return

            media_items = json.loads(row["media_json"])
            for media_item, file_id in zip(media_items, telegram_file_ids):
                if file_id:
                    media_item["telegram_file_id"] = file_id

            self._conn.execute(
                """
                UPDATE recent_results
                SET media_json = ?
                WHERE cache_key = ?
                """,
                (json.dumps(media_items), self._cache_key(chat_id, normalized_url)),
            )

    def purge_expired_results(self) -> list[Path]:
        now = _utc_now().isoformat()
        expired_paths: list[Path] = []
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT media_json FROM recent_results WHERE expires_at <= ?",
                (now,),
            ).fetchall()
            for row in rows:
                for media_item in json.loads(row["media_json"]):
                    expired_paths.append(Path(media_item["file_path"]))
            self._conn.execute(
                "DELETE FROM recent_results WHERE expires_at <= ?", (now,)
            )
        return expired_paths

    def get_public_status(self, chat_id: int) -> dict[str, int]:
        with self._lock:
            completed = self._conn.execute(
                "SELECT COUNT(*) AS count FROM request_events WHERE chat_id = ? AND status = 'completed'",
                (chat_id,),
            ).fetchone()["count"]
            failed = self._conn.execute(
                "SELECT COUNT(*) AS count FROM request_events WHERE chat_id = ? AND status = 'failed'",
                (chat_id,),
            ).fetchone()["count"]
            cache_hits = self._conn.execute(
                "SELECT COUNT(*) AS count FROM request_events WHERE chat_id = ? AND cache_hit = 1",
                (chat_id,),
            ).fetchone()["count"]
        return {"completed": completed, "failed": failed, "cache_hits": cache_hits}

    def get_group_stats(self, chat_id: int) -> dict[str, Any]:
        with self._lock:
            totals = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled
                FROM request_events
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            top_users = self._conn.execute(
                """
                SELECT user_label, COUNT(*) AS count
                FROM request_events
                WHERE chat_id = ? AND status = 'completed'
                GROUP BY user_id, user_label
                ORDER BY count DESC, user_label ASC
                LIMIT 5
                """,
                (chat_id,),
            ).fetchall()
            top_providers = self._conn.execute(
                """
                SELECT provider, COUNT(*) AS count
                FROM request_events
                WHERE chat_id = ? AND status = 'completed'
                GROUP BY provider
                ORDER BY count DESC, provider ASC
                LIMIT 5
                """,
                (chat_id,),
            ).fetchall()
            cache_hits = self._conn.execute(
                "SELECT COUNT(*) AS count FROM request_events WHERE chat_id = ? AND cache_hit = 1",
                (chat_id,),
            ).fetchone()["count"]
            duplicate_joins = self._conn.execute(
                "SELECT COUNT(*) AS count FROM request_events WHERE chat_id = ? AND joined_existing = 1",
                (chat_id,),
            ).fetchone()["count"]
        return {
            "completed": int(totals["completed"] or 0),
            "failed": int(totals["failed"] or 0),
            "cancelled": int(totals["cancelled"] or 0),
            "cache_hits": int(cache_hits),
            "duplicate_joins": int(duplicate_joins),
            "top_users": [(row["user_label"], row["count"]) for row in top_users],
            "top_providers": [(row["provider"], row["count"]) for row in top_providers],
        }

    def get_admin_status(self, chat_id: int) -> dict[str, Any]:
        """Return owner-facing operational state for a chat."""
        settings_row = self.ensure_group_settings(chat_id)
        with self._lock:
            cache_entries = self._conn.execute(
                "SELECT COUNT(*) AS count FROM recent_results WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()["count"]
            queued = self._conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE chat_id = ? AND status = 'queued'",
                (chat_id,),
            ).fetchone()["count"]
            running = self._conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE chat_id = ? AND status = 'running'",
                (chat_id,),
            ).fetchone()["count"]
            failed_jobs = self._conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE chat_id = ? AND status = 'failed'",
                (chat_id,),
            ).fetchone()["count"]
            provider_job_counts = self._conn.execute(
                """
                SELECT provider, status, COUNT(*) AS count
                FROM jobs
                WHERE chat_id = ?
                GROUP BY provider, status
                ORDER BY provider ASC, status ASC
                """,
                (chat_id,),
            ).fetchall()
            recent_failures = self._conn.execute(
                """
                SELECT provider, normalized_url, error_class, finished_at
                FROM jobs
                WHERE chat_id = ? AND status = 'failed'
                ORDER BY finished_at DESC
                LIMIT 5
                """,
                (chat_id,),
            ).fetchall()
        return {
            "settings": settings_row,
            "cache_entries": int(cache_entries),
            "queued_jobs": int(queued),
            "running_jobs": int(running),
            "stale_active_jobs": self.get_stale_active_job_count(
                older_than_seconds=settings.INSTAGRAM_PROVIDER_TIMEOUT_SECONDS * 2,
                chat_id=chat_id,
            ),
            "recent_provider_timeouts": self.get_recent_provider_timeout_count(
                chat_id=chat_id
            ),
            "failed_jobs": int(failed_jobs),
            "provider_job_counts": [
                (row["provider"], row["status"], int(row["count"]))
                for row in provider_job_counts
            ],
            "recent_failures": [
                (
                    row["provider"],
                    row["normalized_url"],
                    row["error_class"] or "unknown",
                    row["finished_at"] or "unknown",
                )
                for row in recent_failures
            ],
        }

    def get_global_admin_status(self) -> dict[str, Any]:
        """Return owner-facing operational state across all chats."""
        with self._lock:
            cache_entries = self._conn.execute(
                "SELECT COUNT(*) AS count FROM recent_results",
            ).fetchone()["count"]
            queued = self._conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status = 'queued'",
            ).fetchone()["count"]
            running = self._conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status = 'running'",
            ).fetchone()["count"]
            failed_jobs = self._conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status = 'failed'",
            ).fetchone()["count"]
            chats_with_jobs = self._conn.execute(
                "SELECT COUNT(DISTINCT chat_id) AS count FROM jobs",
            ).fetchone()["count"]
            users_with_requests = self._conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS count FROM request_events",
            ).fetchone()["count"]
            duplicate_joins = self._conn.execute(
                "SELECT COUNT(*) AS count FROM request_events WHERE joined_existing = 1",
            ).fetchone()["count"]
            provider_job_counts = self._conn.execute("""
                SELECT provider, status, COUNT(*) AS count
                FROM jobs
                GROUP BY provider, status
                ORDER BY provider ASC, status ASC
                """).fetchall()
            recent_failures = self._conn.execute("""
                SELECT provider, normalized_url, error_class, finished_at
                FROM jobs
                WHERE status = 'failed'
                ORDER BY finished_at DESC
                LIMIT 5
                """).fetchall()
        return {
            "cache_entries": int(cache_entries),
            "queued_jobs": int(queued),
            "running_jobs": int(running),
            "stale_active_jobs": self.get_stale_active_job_count(
                older_than_seconds=settings.INSTAGRAM_PROVIDER_TIMEOUT_SECONDS * 2
            ),
            "recent_provider_timeouts": self.get_recent_provider_timeout_count(),
            "failed_jobs": int(failed_jobs),
            "chats_with_jobs": int(chats_with_jobs),
            "users_with_requests": int(users_with_requests),
            "duplicate_joins": int(duplicate_joins),
            "provider_job_counts": [
                (row["provider"], row["status"], int(row["count"]))
                for row in provider_job_counts
            ],
            "recent_failures": [
                (
                    row["provider"],
                    row["normalized_url"],
                    row["error_class"] or "unknown",
                    row["finished_at"] or "unknown",
                )
                for row in recent_failures
            ],
        }

    @staticmethod
    def _cache_key(chat_id: int, normalized_url: str) -> str:
        return f"{chat_id}:{normalized_url}"

    def _mark_inline_one_time_payment(
        self,
        payment_id: str,
        *,
        status: str,
        request_id: str | None,
        refund_reason: str | None,
    ) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE inline_one_time_payments
                SET status = ?,
                    request_id = ?,
                    refund_reason = ?,
                    updated_at = ?
                WHERE payment_id = ? AND status = 'paid'
                """,
                (status, request_id, refund_reason, now, payment_id),
            )

    @staticmethod
    def _normalize_utc_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_runtime_bool(value: Any) -> bool | None:
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return None

    def _safe_metrics_write(self, query: str, params: tuple[Any, ...]) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(query, params)
        except sqlite3.Error as exc:
            logger.warning("Performance metrics write failed: %s", exc)

    @staticmethod
    def _safe_average(values: list[int]) -> int:
        if not values:
            return 0
        return round(sum(values) / len(values))

    @staticmethod
    def _duration_between_ms(start: str | None, end: str | None) -> int | None:
        if not start or not end:
            return None
        try:
            start_at = datetime.fromisoformat(start)
            end_at = datetime.fromisoformat(end)
        except ValueError:
            return None
        return max(0, round((end_at - start_at).total_seconds() * 1000))
