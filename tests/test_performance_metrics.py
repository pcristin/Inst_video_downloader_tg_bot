import sqlite3

from src.instagram_video_bot.services.state_store import StateStore


def test_metrics_lifecycle_records_timings_and_summary(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.start_job_metrics(
        job_id="job-1",
        chat_id=77,
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/a/",
    )
    store.mark_job_metrics_started("job-1")
    store.record_cache_hit("job-1")
    store.record_download_metrics(
        "job-1",
        download_duration_ms=1250,
        retry_count=2,
        instagram_fast_status="failed",
        instagram_fast_duration_ms=300,
        instagram_fallback_attempted=True,
        instagram_account_attempts=2,
        instagram_account_retries=1,
        instagram_auth_failures=1,
        instagram_success_path="fallback",
    )
    store.record_delivery_metrics("job-1", delivery_duration_ms=800)
    store.finalize_job_metrics("job-1", status="completed")

    summary = store.get_performance_summary(77, limit=50)

    assert summary["total_jobs"] == 1
    assert summary["cache_hits"] == 1
    assert summary["cache_hit_rate"] == 1.0
    assert summary["providers"]["instagram"]["jobs"] == 1
    assert summary["providers"]["instagram"]["avg_download_ms"] == 1250
    assert summary["avg_delivery_ms"] == 800
    assert summary["instagram"]["fast_failed"] == 1
    assert summary["instagram"]["fallback_count"] == 1
    assert summary["instagram"]["account_retries"] == 1
    assert summary["instagram"]["auth_failures"] == 1


def test_metrics_schema_migrates_existing_database(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            normalized_url TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error_class TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    store.start_job_metrics(
        job_id="job-old",
        chat_id=77,
        provider="twitter",
        normalized_url="https://x.com/a/status/1",
    )
    store.finalize_job_metrics("job-old", status="completed")

    summary = store.get_performance_summary(77, limit=50)

    assert summary["total_jobs"] == 1
    assert summary["providers"]["twitter"]["jobs"] == 1
