"""Formatting and aggregation helpers for Telegram admin performance output."""

from __future__ import annotations

from typing import Any

from .chaos_text import ChaosText


def format_performance_summary(performance: dict[str, Any]) -> str:
    """Format state-store performance metrics for owner-facing admin status."""

    total_jobs = int(performance.get("total_jobs", 0) or 0)
    cache_hits = int(performance.get("cache_hits", 0) or 0)
    cache_rate = float(performance.get("cache_hit_rate", 0.0) or 0.0) * 100
    duplicate_joins = int(performance.get("duplicate_joins", 0) or 0)
    lines = [
        "Производительность:",
        f"- Окно: последние {total_jobs} задач",
        f"- Кэш: {cache_hits} ({cache_rate:.0f}%)",
        f"- Повторы: {duplicate_joins}",
        f"- Queue wait avg: {int(performance.get('avg_queue_wait_ms', 0) or 0)}мс",
        f"- Telegram delivery avg: {int(performance.get('avg_delivery_ms', 0) or 0)}мс",
    ]

    providers = performance.get("providers", {})
    if providers:
        for provider, provider_summary in sorted(providers.items()):
            lines.append(
                "- "
                f"{ChaosText.provider_name(provider)}: "
                f"{provider_summary.get('jobs', 0)} задач, "
                f"queue avg {provider_summary.get('avg_queue_wait_ms', 0)}мс, "
                f"download avg {provider_summary.get('avg_download_ms', 0)}мс, "
                f"delivery avg {provider_summary.get('avg_delivery_ms', 0)}мс"
            )
    else:
        lines.append("- Провайдеры: нет данных")

    instagram = performance.get("instagram", {})
    lines.append(
        "- Instagram fast-path: "
        f"ошибок {int(instagram.get('fast_failed', 0) or 0)}, "
        f"fallback {int(instagram.get('fallback_count', 0) or 0)}, "
        f"retries {int(instagram.get('account_retries', 0) or 0)}, "
        f"auth {int(instagram.get('auth_failures', 0) or 0)}"
    )

    failure_classes = sorted(
        {
            str(error_class)
            for error_class in performance.get("failure_classes", [])
            if error_class and error_class != "unknown"
        }
    )
    lines.append(
        "- Классы ошибок: "
        + (", ".join(failure_classes) if failure_classes else "нет")
    )
    return "\n".join(lines)


def build_admin_performance_summary(
    state_store: Any,
    *,
    chat_id: int | None,
    duplicate_joins: int,
    recent_failures: list[tuple[str, str, str, str]],
) -> dict[str, Any]:
    """Merge base state-store performance metrics with admin-status context."""

    performance = state_store.get_performance_summary(chat_id, limit=50)
    performance["duplicate_joins"] = duplicate_joins
    performance["failure_classes"] = list(performance.get("failure_classes", [])) + [
        error_class
        for _provider, _normalized_url, error_class, _finished_at in recent_failures
    ]
    return performance
