from src.instagram_video_bot.services.telegram_bot import TelegramBot


def test_performance_summary_format_includes_queue_wait_metrics():
    text = TelegramBot._format_performance_summary(
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
            "instagram": {},
            "failure_classes": [],
        }
    )

    assert "Queue wait avg: 2000мс" in text
    assert "queue avg 1000мс" in text
