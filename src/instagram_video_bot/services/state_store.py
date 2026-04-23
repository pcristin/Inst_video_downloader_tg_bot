"""SQLite-backed bot state, cache, and lightweight stats."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config.settings import settings


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
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    normalized_url TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_class TEXT
                );

                CREATE TABLE IF NOT EXISTS request_events (
                    request_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_label TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    joined_existing INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS group_settings (
                    chat_id INTEGER PRIMARY KEY,
                    quiet_mode INTEGER NOT NULL DEFAULT 0,
                    duplicate_suppression INTEGER NOT NULL DEFAULT 1,
                    stats_enabled INTEGER NOT NULL DEFAULT 1,
                    chaos_mode_enabled INTEGER NOT NULL DEFAULT 0,
                    chat_max_concurrent_jobs INTEGER,
                    user_max_active_jobs INTEGER
                );

                CREATE TABLE IF NOT EXISTS recent_results (
                    cache_key TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    normalized_url TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_chat_status
                    ON jobs (chat_id, status);
                CREATE INDEX IF NOT EXISTS idx_requests_chat_status
                    ON request_events (chat_id, status);
                CREATE INDEX IF NOT EXISTS idx_cache_chat_url
                    ON recent_results (chat_id, normalized_url);
                """
            )
            existing_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(group_settings)").fetchall()
            }
            if "chat_max_concurrent_jobs" not in existing_columns:
                self._conn.execute(
                    "ALTER TABLE group_settings ADD COLUMN chat_max_concurrent_jobs INTEGER"
                )
            if "user_max_active_jobs" not in existing_columns:
                self._conn.execute(
                    "ALTER TABLE group_settings ADD COLUMN user_max_active_jobs INTEGER"
                )
            if "chaos_mode_enabled" not in existing_columns:
                self._conn.execute(
                    "ALTER TABLE group_settings ADD COLUMN chaos_mode_enabled INTEGER NOT NULL DEFAULT 0"
                )
            request_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(request_events)").fetchall()
            }
            if "joined_existing" not in request_columns:
                self._conn.execute(
                    "ALTER TABLE request_events ADD COLUMN joined_existing INTEGER NOT NULL DEFAULT 0"
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
            "chat_max_concurrent_jobs": int(row["chat_max_concurrent_jobs"])
            if row["chat_max_concurrent_jobs"] is not None
            else settings.CHAT_MAX_CONCURRENT_JOBS,
            "user_max_active_jobs": int(row["user_max_active_jobs"])
            if row["user_max_active_jobs"] is not None
            else settings.USER_MAX_ACTIVE_JOBS,
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
            if key in {"quiet_mode", "duplicate_suppression", "stats_enabled", "chaos_mode_enabled"}:
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

    def create_job(self, job_id: str, chat_id: int, normalized_url: str, provider: str, status: str) -> None:
        now = _utc_now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO jobs (job_id, chat_id, normalized_url, provider, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, chat_id, normalized_url, provider, status, now),
            )

    def update_job_status(self, job_id: str, status: str, error_class: str | None = None) -> None:
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

    def update_request_status(self, request_id: str, status: str, cache_hit: bool = False) -> None:
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

    def get_cached_result(self, chat_id: int, normalized_url: str) -> CachedMediaEntry | None:
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
            self._conn.execute("DELETE FROM recent_results WHERE expires_at <= ?", (now,))
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
            "failed_jobs": int(failed_jobs),
            "provider_job_counts": [
                (row["provider"], row["status"], int(row["count"])) for row in provider_job_counts
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
