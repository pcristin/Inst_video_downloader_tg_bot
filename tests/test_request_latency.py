from datetime import datetime, timezone

from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.services.telegram_performance import (
    format_performance_summary,
)


def test_latency_summary_includes_upload_and_counts_failures_separately(tmp_path):
    store = StateStore(tmp_path / "state.db")
    for index, status in enumerate(("delivered", "delivered", "failed")):
        job_id = f"job-{index}"
        store.start_job_metrics(
            job_id=job_id, chat_id=77, provider="instagram", normalized_url="test"
        )
        store.record_request_received(
            request_id=job_id, job_id=job_id, received_at=datetime.now(timezone.utc)
        )
        store.record_request_outcome(
            job_id,
            status=status,
            first_media_ms=72000 if status == "delivered" else None,
            total_duration_ms=73000 + index * 1000,
        )
        store.record_delivery_attempt(
            job_id=job_id,
            request_id=job_id,
            stage="storage_upload",
            status="delivered",
            duration_ms=43000,
            media_bytes=43000000,
            media_count=2,
        )
    summary = store.get_performance_summary(77)
    assert summary["latency"]["delivered"] == 2
    assert summary["latency"]["failed"] == 1
    assert summary["latency"]["p50_ms"] == 73500
    assert summary["latency"]["p95_ms"] == 74000
    assert summary["avg_storage_upload_ms"] == 43000
    assert summary["storage_upload_bytes_per_second"] == 1000000
    text = format_performance_summary(summary)
    assert "73500" in text and "74000" in text and "43000" in text


def test_acquisition_completion_does_not_count_as_delivered(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.start_job_metrics(
        job_id="j", chat_id=77, provider="instagram", normalized_url="test"
    )
    store.finalize_job_metrics("j", status="completed")
    store.record_request_received(
        request_id="r", job_id="j", received_at=datetime.now(timezone.utc)
    )
    summary = store.get_performance_summary(77)
    assert summary["latency"]["delivered"] == 0
    assert summary["latency"]["pending"] == 1
    assert summary["latency"]["p50_ms"] is None


def test_provider_phase_timings_survive_persistence(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.start_job_metrics(
        job_id="j", chat_id=77, provider="instagram", normalized_url="test"
    )
    store.record_download_metrics(
        "j",
        download_duration_ms=1000,
        provider_extraction_ms=200,
        provider_download_ms=500,
        media_normalization_ms=300,
    )
    row = store._conn.execute(
        "SELECT * FROM performance_metrics WHERE job_id='j'"
    ).fetchone()
    assert (
        row["provider_extraction_ms"],
        row["provider_download_ms"],
        row["media_normalization_ms"],
    ) == (200, 500, 300)
