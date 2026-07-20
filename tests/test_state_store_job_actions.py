import sqlite3

from src.instagram_video_bot.services.state_store import StateStore


def test_request_failure_metadata_and_retry_link_are_persisted(tmp_path):
    store = StateStore(tmp_path / "state.db")
    normalized_url = "https://x.com/example/status/123"
    store.create_job("job-1", 10, normalized_url, "twitter", "queued")
    store.create_request(
        request_id="request-1",
        job_id="job-1",
        chat_id=10,
        user_id=20,
        user_label="User",
        provider="twitter",
        normalized_url=normalized_url,
        status="queued",
    )

    store.update_request_status(
        "request-1",
        "failed",
        failure_reason="provider_timeout",
        retryable=True,
    )

    failed = store.get_request_for_action("request-1")
    assert failed is not None
    assert failed["failure_reason"] == "provider_timeout"
    assert failed["retryable"] == 1
    assert failed["job_normalized_url"] == normalized_url
    assert failed["job_provider"] == "twitter"

    store.create_request(
        request_id="request-2",
        job_id="job-1",
        chat_id=10,
        user_id=20,
        user_label="User",
        provider="twitter",
        normalized_url=normalized_url,
        status="queued",
        retry_of_request_id="request-1",
    )

    retry = store.get_request_for_action("request-2")
    assert retry is not None
    assert retry["retry_of_request_id"] == "request-1"


def test_get_request_for_action_returns_none_for_unknown_request(tmp_path):
    store = StateStore(tmp_path / "state.db")

    assert store.get_request_for_action("missing") is None


def test_request_action_columns_are_added_to_legacy_database(tmp_path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
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
            )
            """)

    store = StateStore(db_path)
    columns = {
        row[1]
        for row in store._conn.execute("PRAGMA table_info(request_events)").fetchall()
    }

    assert {"failure_reason", "retryable", "retry_of_request_id"} <= columns
