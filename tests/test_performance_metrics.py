import sqlite3
from datetime import datetime, timedelta, timezone

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
    assert summary["providers"]["instagram"]["avg_queue_wait_ms"] >= 0
    assert summary["avg_queue_wait_ms"] >= 0
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


def test_summary_failure_classes_only_include_failed_jobs(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.start_job_metrics(
        job_id="recovered-job",
        chat_id=77,
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/recovered/",
    )
    store.mark_job_metrics_started("recovered-job")
    store.record_download_metrics(
        "recovered-job",
        download_duration_ms=900,
        instagram_fast_status="failed",
        instagram_fallback_attempted=True,
        instagram_success_path="fallback",
        failure_class="fast_path_failed",
    )
    store.finalize_job_metrics("recovered-job", status="completed")

    store.start_job_metrics(
        job_id="failed-job",
        chat_id=77,
        provider="instagram",
        normalized_url="https://www.instagram.com/reel/failed/",
    )
    store.mark_job_metrics_started("failed-job")
    store.record_download_metrics(
        "failed-job",
        download_duration_ms=100,
        failure_class="no_instagram_accounts",
    )
    store.finalize_job_metrics("failed-job", status="failed")

    summary = store.get_performance_summary(77, limit=50)

    assert summary["failure_classes"] == ["no_instagram_accounts"]


def test_summary_includes_queue_wait_averages(tmp_path):
    store = StateStore(tmp_path / "state.db")
    base_time = datetime(2026, 5, 16, tzinfo=timezone.utc)
    rows = [
        ("job-instagram", "instagram", base_time, base_time + timedelta(seconds=1)),
        ("job-twitter", "twitter", base_time, base_time + timedelta(seconds=3)),
    ]
    for job_id, provider, created_at, started_at in rows:
        store.start_job_metrics(
            job_id=job_id,
            chat_id=77,
            provider=provider,
            normalized_url=f"https://example.com/{job_id}",
        )
        store.mark_job_metrics_started(job_id)
        store.record_download_metrics(job_id, download_duration_ms=100)
        store.finalize_job_metrics(job_id, status="completed")
        with store._lock, store._conn:
            store._conn.execute(
                """
                UPDATE performance_metrics
                SET created_at = ?, started_at = ?
                WHERE job_id = ?
                """,
                (created_at.isoformat(), started_at.isoformat(), job_id),
            )

    summary = store.get_performance_summary(77, limit=50)

    assert summary["avg_queue_wait_ms"] == 2000
    assert summary["providers"]["instagram"]["avg_queue_wait_ms"] == 1000
    assert summary["providers"]["twitter"]["avg_queue_wait_ms"] == 3000
