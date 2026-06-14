import sqlite3

from src.instagram_video_bot.services import state_schema
from src.instagram_video_bot.services.state_schema import \
    initialize_state_schema


def test_initialize_state_schema_creates_core_and_inline_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    with conn:
        initialize_state_schema(conn)

    table_names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "jobs",
        "request_events",
        "recent_results",
        "performance_metrics",
        "inline_sessions",
        "inline_one_time_payments",
        "user_settings",
    }.issubset(table_names)

    inline_payment_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(inline_one_time_payments)"
        ).fetchall()
    }
    assert {"provider", "normalized_url", "refund_reason"}.issubset(
        inline_payment_columns
    )


def test_initialize_state_schema_supports_default_row_factory():
    conn = sqlite3.connect(":memory:")

    with conn:
        initialize_state_schema(conn)

    assert conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchone() is None


def test_initialize_state_schema_migrates_existing_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE group_settings (
            chat_id INTEGER PRIMARY KEY,
            quiet_mode INTEGER NOT NULL DEFAULT 0,
            duplicate_suppression INTEGER NOT NULL DEFAULT 1,
            stats_enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE request_events (
            request_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_label TEXT NOT NULL,
            provider TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            status TEXT NOT NULL,
            cache_hit INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE performance_metrics (
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
            instagram_fallback_attempted INTEGER NOT NULL DEFAULT 0,
            instagram_account_attempts INTEGER NOT NULL DEFAULT 0,
            instagram_account_retries INTEGER NOT NULL DEFAULT 0,
            instagram_auth_failures INTEGER NOT NULL DEFAULT 0,
            instagram_success_path TEXT
        );
        CREATE TABLE inline_sessions (
            session_token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            original_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_label TEXT NOT NULL,
            inline_message_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE inline_one_time_payments (
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            session_token TEXT NOT NULL,
            telegram_payment_charge_id TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT,
            refund_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE inline_subscriptions (
            user_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            telegram_payment_charge_id TEXT NOT NULL,
            provider_payment_charge_id TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    with conn:
        initialize_state_schema(conn)

    migrated_columns = {
        table_name: {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for table_name in (
            "group_settings",
            "request_events",
            "performance_metrics",
            "inline_sessions",
            "inline_one_time_payments",
            "inline_subscriptions",
        )
    }
    assert {
        "chat_max_concurrent_jobs",
        "user_max_active_jobs",
        "chaos_mode_enabled",
    }.issubset(migrated_columns["group_settings"])
    assert "joined_existing" in migrated_columns["request_events"]
    assert {
        "failure_class",
        "instagram_fast_budget_exhausted",
        "instagram_fast_endpoint_timings_json",
        "instagram_fallback_path",
        "instagram_metadata_reused",
    }.issubset(migrated_columns["performance_metrics"])
    assert "access_kind" in migrated_columns["inline_sessions"]
    assert {"provider", "normalized_url"}.issubset(
        migrated_columns["inline_one_time_payments"]
    )
    assert {"started_at", "auto_refund_checked_at", "refund_reason"}.issubset(
        migrated_columns["inline_subscriptions"]
    )


def test_add_column_if_missing_ignores_duplicate_column_race():
    class _EmptyCursor:
        def fetchall(self):
            return []

    class _RaceConnection:
        def execute(self, query):
            if query.startswith("PRAGMA table_info"):
                return _EmptyCursor()
            raise sqlite3.OperationalError("duplicate column name: new_column")

    state_schema.add_column_if_missing(
        _RaceConnection(),
        "example_table",
        "new_column",
        "new_column TEXT",
    )
