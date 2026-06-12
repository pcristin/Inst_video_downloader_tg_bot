from src.instagram_video_bot.services.telegram_performance import (
    build_admin_performance_summary, format_performance_summary)


class _FakeStateStore:
    def get_performance_summary(self, chat_id, *, limit):
        assert chat_id == 77
        assert limit == 50
        return {
            "total_jobs": 2,
            "cache_hits": 1,
            "cache_hit_rate": 0.5,
            "avg_queue_wait_ms": 2000,
            "avg_delivery_ms": 300,
            "providers": {
                "instagram": {
                    "jobs": 1,
                    "avg_queue_wait_ms": 1000,
                    "avg_download_ms": 500,
                    "avg_delivery_ms": 300,
                }
            },
            "instagram": {"fast_failed": 1},
            "failure_classes": ["provider_timeout"],
        }


def test_format_performance_summary_includes_provider_and_failure_metrics():
    text = format_performance_summary(
        {
            "total_jobs": 2,
            "cache_hits": 0,
            "cache_hit_rate": 0.0,
            "duplicate_joins": 0,
            "avg_queue_wait_ms": 2000,
            "avg_delivery_ms": 300,
            "providers": {
                "instagram": {
                    "jobs": 1,
                    "avg_queue_wait_ms": 1000,
                    "avg_download_ms": 500,
                    "avg_delivery_ms": 300,
                }
            },
            "instagram": {"fast_failed": 1, "fallback_count": 2},
            "failure_classes": ["unknown", "provider_timeout"],
        }
    )

    assert "Queue wait avg: 2000мс" in text
    assert "Instagram: 1 задач, queue avg 1000мс" in text
    assert "ошибок 1, fallback 2" in text
    assert "Классы ошибок: provider_timeout" in text


def test_build_admin_performance_summary_adds_duplicate_joins_and_recent_failures():
    summary = build_admin_performance_summary(
        _FakeStateStore(),
        chat_id=77,
        duplicate_joins=3,
        recent_failures=[
            ("instagram", "https://example.test/a", "auth_failed", "2026-06-12")
        ],
    )

    assert summary["duplicate_joins"] == 3
    assert summary["failure_classes"] == ["provider_timeout", "auth_failed"]
