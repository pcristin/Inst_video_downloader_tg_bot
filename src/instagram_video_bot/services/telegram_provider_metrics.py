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
    effective_failure_class = getattr(metrics, "failure_class", None) or failure_class
    state_store.record_download_metrics(
        job_id,
        download_duration_ms=download_duration_ms,
        retry_count=int(getattr(metrics, "retry_count", 0) or 0),
        instagram_fast_status=getattr(metrics, "instagram_fast_status", None),
        instagram_fast_duration_ms=getattr(metrics, "instagram_fast_duration_ms", None),
        instagram_fast_budget_exhausted=bool(
            getattr(metrics, "instagram_fast_budget_exhausted", False)
        ),
        instagram_fast_endpoint_timings_json=getattr(
            metrics, "instagram_fast_endpoint_timings_json", None
        ),
        instagram_fallback_attempted=bool(
            getattr(metrics, "instagram_fallback_attempted", False)
        ),
        instagram_account_attempts=int(
            getattr(metrics, "instagram_account_attempts", 0) or 0
        ),
        instagram_account_retries=int(
            getattr(metrics, "instagram_account_retries", 0) or 0
        ),
        instagram_auth_failures=int(getattr(metrics, "instagram_auth_failures", 0) or 0),
        instagram_success_path=getattr(metrics, "instagram_success_path", None),
        instagram_fallback_path=getattr(metrics, "instagram_fallback_path", None),
        instagram_metadata_reused=bool(
            getattr(metrics, "instagram_metadata_reused", False)
        ),
        failure_class=effective_failure_class,
    )
