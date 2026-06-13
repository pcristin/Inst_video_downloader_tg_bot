"""SQLite schema creation and migrations for bot state."""

from __future__ import annotations

import sqlite3


def initialize_state_schema(conn: sqlite3.Connection) -> None:
    """Create and migrate all tables required by StateStore."""

    conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS performance_metrics (
            job_id TEXT PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            cache_hit INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            download_duration_ms INTEGER,
            delivery_duration_ms INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            instagram_fast_status TEXT,
            instagram_fast_duration_ms INTEGER,
            instagram_fast_budget_exhausted INTEGER NOT NULL DEFAULT 0,
            instagram_fast_endpoint_timings_json TEXT,
            instagram_fallback_attempted INTEGER NOT NULL DEFAULT 0,
            instagram_account_attempts INTEGER NOT NULL DEFAULT 0,
            instagram_account_retries INTEGER NOT NULL DEFAULT 0,
            instagram_auth_failures INTEGER NOT NULL DEFAULT 0,
            instagram_success_path TEXT,
            instagram_fallback_path TEXT,
            instagram_metadata_reused INTEGER NOT NULL DEFAULT 0,
            failure_class TEXT
        );

        CREATE TABLE IF NOT EXISTS inline_sessions (
            session_token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            original_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_label TEXT NOT NULL,
            access_kind TEXT NOT NULL DEFAULT 'free',
            inline_message_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_whitelist (
            user_id INTEGER PRIMARY KEY,
            added_by_user_id INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_subscriptions (
            user_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL,
            telegram_payment_charge_id TEXT NOT NULL,
            provider_payment_charge_id TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            auto_refund_checked_at TEXT,
            refund_reason TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_one_time_payments (
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            session_token TEXT NOT NULL,
            provider TEXT,
            normalized_url TEXT,
            telegram_payment_charge_id TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT,
            refund_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_cached_media (
            cache_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            media_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_rate_limit_events (
            event_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_promo_usage (
            user_id INTEGER PRIMARY KEY,
            success_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inline_delivery_events (
            event_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            session_token TEXT NOT NULL,
            access_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_notifications (
            notification_key TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_class TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (notification_key, user_id)
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            language_code TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_chat_status
            ON jobs (chat_id, status);
        CREATE INDEX IF NOT EXISTS idx_requests_chat_status
            ON request_events (chat_id, status);
        CREATE INDEX IF NOT EXISTS idx_cache_chat_url
            ON recent_results (chat_id, normalized_url);
        CREATE INDEX IF NOT EXISTS idx_perf_chat_finished
            ON performance_metrics (chat_id, finished_at);
        CREATE INDEX IF NOT EXISTS idx_user_notifications_key_status
            ON user_notifications (notification_key, status);
        CREATE INDEX IF NOT EXISTS idx_user_rate_limit_user_created
            ON user_rate_limit_events (user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_inline_delivery_user_period
            ON inline_delivery_events (user_id, access_kind, occurred_at);
        """)

    add_column_if_missing(
        conn, "group_settings", "chat_max_concurrent_jobs", "chat_max_concurrent_jobs INTEGER"
    )
    add_column_if_missing(
        conn, "group_settings", "user_max_active_jobs", "user_max_active_jobs INTEGER"
    )
    add_column_if_missing(
        conn,
        "group_settings",
        "chaos_mode_enabled",
        "chaos_mode_enabled INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(
        conn,
        "request_events",
        "joined_existing",
        "joined_existing INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(conn, "performance_metrics", "failure_class", "failure_class TEXT")
    add_column_if_missing(
        conn,
        "performance_metrics",
        "instagram_fast_budget_exhausted",
        "instagram_fast_budget_exhausted INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(
        conn,
        "performance_metrics",
        "instagram_fast_endpoint_timings_json",
        "instagram_fast_endpoint_timings_json TEXT",
    )
    add_column_if_missing(
        conn,
        "performance_metrics",
        "instagram_fallback_path",
        "instagram_fallback_path TEXT",
    )
    add_column_if_missing(
        conn,
        "performance_metrics",
        "instagram_metadata_reused",
        "instagram_metadata_reused INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(
        conn, "inline_one_time_payments", "provider", "provider TEXT"
    )
    add_column_if_missing(
        conn, "inline_one_time_payments", "normalized_url", "normalized_url TEXT"
    )
    add_column_if_missing(
        conn,
        "inline_sessions",
        "access_kind",
        "access_kind TEXT NOT NULL DEFAULT 'free'",
    )

    had_subscription_started_at = "started_at" in _column_names(
        conn, "inline_subscriptions"
    )
    add_column_if_missing(
        conn,
        "inline_subscriptions",
        "started_at",
        "started_at TEXT NOT NULL DEFAULT ''",
    )
    if not had_subscription_started_at:
        conn.execute("""
            UPDATE inline_subscriptions
            SET started_at = updated_at
            WHERE started_at = ''
            """)
    add_column_if_missing(
        conn,
        "inline_subscriptions",
        "auto_refund_checked_at",
        "auto_refund_checked_at TEXT",
    )
    add_column_if_missing(
        conn, "inline_subscriptions", "refund_reason", "refund_reason TEXT"
    )


def add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    """Add a column once, tolerating a concurrent duplicate-column migration race."""

    if column_name in _column_names(conn, table_name):
        return
    try:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")
    except sqlite3.OperationalError as error:
        if "duplicate column name" in str(error).lower():
            return
        raise


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
