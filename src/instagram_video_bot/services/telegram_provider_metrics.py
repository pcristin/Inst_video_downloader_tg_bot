"""Persistence helpers for Telegram download provider metrics."""

from __future__ import annotations

from typing import Any, Protocol

from .download_models import ProviderExecutionMetrics


class ProviderMetricsStateStore(Protocol):
    def record_download_metrics(self, job_id: str, **kwargs: Any) -> None:
        """Persist download metrics for an existing job."""
        ...


def record_provider_metrics(
    state_store: ProviderMetricsStateStore,
    job_id: str,
    provider_metrics: ProviderExecutionMetrics | None,
    *,
    download_duration_ms: int,
    failure_class: str | None = None,
) -> None:
    """Persist provider execution metrics without leaking provider internals."""
    metrics = provider_metrics or ProviderExecutionMetrics(provider="unknown")
    effective_failure_class = metrics.failure_class or failure_class
    state_store.record_download_metrics(
        job_id,
        download_duration_ms=download_duration_ms,
        retry_count=int(metrics.retry_count or 0),
        instagram_fast_status=metrics.instagram_fast_status,
        instagram_fast_duration_ms=metrics.instagram_fast_duration_ms,
        instagram_fast_budget_exhausted=bool(metrics.instagram_fast_budget_exhausted),
        instagram_fast_endpoint_timings_json=metrics.instagram_fast_endpoint_timings_json,
        instagram_fallback_attempted=bool(metrics.instagram_fallback_attempted),
        instagram_account_attempts=int(metrics.instagram_account_attempts or 0),
        instagram_account_retries=int(metrics.instagram_account_retries or 0),
        instagram_auth_failures=int(metrics.instagram_auth_failures or 0),
        instagram_success_path=metrics.instagram_success_path,
        instagram_fallback_path=metrics.instagram_fallback_path,
        instagram_metadata_reused=bool(metrics.instagram_metadata_reused),
        failure_class=effective_failure_class,
    )
