from src.instagram_video_bot.services.download_models import \
    ProviderExecutionMetrics
from src.instagram_video_bot.services.telegram_provider_metrics import \
    record_provider_metrics


class _FakeStateStore:
    def __init__(self):
        self.calls = []

    def record_download_metrics(self, job_id: str, **kwargs):
        self.calls.append((job_id, kwargs))


def test_record_provider_metrics_forwards_provider_fields():
    store = _FakeStateStore()
    provider_metrics = ProviderExecutionMetrics(
        provider="instagram",
        retry_count=2,
        failure_class="provider_timeout",
        instagram_fast_status="failed",
        instagram_fast_duration_ms=123,
        instagram_fast_budget_exhausted=True,
        instagram_fast_endpoint_timings_json='{"api": 111}',
        instagram_fallback_attempted=True,
        instagram_account_attempts=3,
        instagram_account_retries=1,
        instagram_auth_failures=2,
        instagram_success_path="fallback",
        instagram_fallback_path="web",
        instagram_metadata_reused=True,
    )

    record_provider_metrics(
        store,
        "job-1",
        provider_metrics,
        download_duration_ms=456,
        failure_class="DownloadError",
    )

    assert store.calls == [
        (
            "job-1",
            {
                "download_duration_ms": 456,
                "retry_count": 2,
                "instagram_fast_status": "failed",
                "instagram_fast_duration_ms": 123,
                "instagram_fast_budget_exhausted": True,
                "instagram_fast_endpoint_timings_json": '{"api": 111}',
                "instagram_fallback_attempted": True,
                "instagram_account_attempts": 3,
                "instagram_account_retries": 1,
                "instagram_auth_failures": 2,
                "instagram_success_path": "fallback",
                "instagram_fallback_path": "web",
                "instagram_metadata_reused": True,
                "failure_class": "provider_timeout",
            },
        )
    ]


def test_record_provider_metrics_uses_defaults_without_provider_metrics():
    store = _FakeStateStore()

    record_provider_metrics(
        store,
        "job-2",
        None,
        download_duration_ms=12,
        failure_class="DownloadError",
    )

    assert store.calls == [
        (
            "job-2",
            {
                "download_duration_ms": 12,
                "retry_count": 0,
                "instagram_fast_status": None,
                "instagram_fast_duration_ms": None,
                "instagram_fast_budget_exhausted": False,
                "instagram_fast_endpoint_timings_json": None,
                "instagram_fallback_attempted": False,
                "instagram_account_attempts": 0,
                "instagram_account_retries": 0,
                "instagram_auth_failures": 0,
                "instagram_success_path": None,
                "instagram_fallback_path": None,
                "instagram_metadata_reused": False,
                "failure_class": "DownloadError",
            },
        )
    ]
