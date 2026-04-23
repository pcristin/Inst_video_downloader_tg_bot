from src.instagram_video_bot.services.state_store import StateStore


def test_group_settings_include_disabled_chaos_mode_by_default(tmp_path):
    store = StateStore(tmp_path / "state.db")

    settings = store.ensure_group_settings(77)

    assert settings["chaos_mode_enabled"] is False


def test_group_settings_can_enable_chaos_mode(tmp_path):
    store = StateStore(tmp_path / "state.db")

    settings = store.update_group_settings(77, chaos_mode_enabled=True)

    assert settings["chaos_mode_enabled"] is True
    assert store.ensure_group_settings(77)["chaos_mode_enabled"] is True


def test_group_stats_include_cache_hits_duplicate_joins_and_provider_counts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_job("job-1", 77, "https://x.com/a/status/1", "twitter", "queued")
    store.create_request(
        "req-1",
        "job-1",
        77,
        1001,
        "@alice",
        "twitter",
        "https://x.com/a/status/1",
        "queued",
    )
    store.create_request(
        "req-2",
        "job-1",
        77,
        1002,
        "@bob",
        "twitter",
        "https://x.com/a/status/1",
        "queued",
        joined_existing=True,
    )
    store.update_request_status("req-1", "completed", cache_hit=True)
    store.update_request_status("req-2", "completed")

    stats = store.get_group_stats(77)

    assert stats["completed"] == 2
    assert stats["cache_hits"] == 1
    assert stats["duplicate_joins"] == 1
    assert stats["top_providers"] == [("twitter", 2)]
