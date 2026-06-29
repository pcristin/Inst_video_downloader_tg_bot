import json
import threading

import pytest

from src.instagram_video_bot.services.instagram_auth_pool import (
    InstagramAuthConfigError,
    InstagramAuthContext,
    InstagramAuthPool,
    load_configured_instagram_auth_pool,
    load_instagram_auth_pool,
)


def _write_auth_file(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_loads_cobalt_compatible_cookie_and_bearer_contexts(tmp_path):
    auth_file = tmp_path / "instagram_auth.json"
    _write_auth_file(
        auth_file,
        {
            "instagram": ["mid=abc; sessionid=session-a"],
            "instagram_bearer": ["token=IGT:2:bearer-a", "Bearer IGT:2:bearer-b"],
        },
    )

    pool = InstagramAuthPool.from_file(
        auth_file,
        max_contexts_per_attempt=3,
        cooldown_seconds=900,
    )

    contexts = pool.get_contexts_for_attempt()
    assert [context.kind for context in contexts] == ["cookie", "bearer", "bearer"]
    assert contexts[0].as_headers() == {"Cookie": "mid=abc; sessionid=session-a"}
    assert contexts[1].as_headers() == {"Authorization": "Bearer IGT:2:bearer-a"}
    assert contexts[2].as_headers() == {"Authorization": "Bearer IGT:2:bearer-b"}


def test_missing_or_empty_config_disables_auth_pool(tmp_path):
    missing = load_instagram_auth_pool(None)
    assert missing.available is False

    empty_file = tmp_path / "empty.json"
    _write_auth_file(empty_file, {})
    empty = load_instagram_auth_pool(empty_file)
    assert empty.available is False
    assert empty.get_contexts_for_attempt() == []


def test_malformed_json_disables_auth_pool_without_secret_in_error(tmp_path):
    auth_file = tmp_path / "bad.json"
    auth_file.write_text('{"instagram": ["mid=abc; sessionid=SECRET"', encoding="utf-8")

    pool = load_instagram_auth_pool(auth_file)

    assert pool.available is False
    assert "SECRET" not in repr(pool)


def test_malformed_json_config_error_has_no_secret_bearing_cause(tmp_path):
    auth_file = tmp_path / "bad.json"
    auth_file.write_text('{"instagram": ["mid=abc; sessionid=SECRET"', encoding="utf-8")

    with pytest.raises(InstagramAuthConfigError) as exc_info:
        InstagramAuthPool.from_file(auth_file)

    assert exc_info.value.__cause__ is None
    assert "SECRET" not in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"instagram": ["mid=abc\nsessionid=secret"]},
        {"instagram_bearer": ["token=abc\rsecret"]},
        {"instagram_bearer": ["Bearer abc\x00secret"]},
        {"instagram": "mid=abc; sessionid=secret"},
        {"instagram_bearer": [123]},
    ],
)
def test_rejects_invalid_or_unsafe_header_values(tmp_path, payload):
    auth_file = tmp_path / "invalid.json"
    _write_auth_file(auth_file, payload)

    with pytest.raises(InstagramAuthConfigError) as exc_info:
        InstagramAuthPool.from_file(auth_file)

    message = str(exc_info.value)
    assert "secret" not in message.lower()
    assert "sessionid" not in message.lower()


def test_context_repr_does_not_include_secret_material():
    context = InstagramAuthContext(
        context_id="cookie:0",
        kind="cookie",
        value="mid=abc; sid=super-secret",
    )

    rendered = repr(context)

    assert "super-secret" not in rendered
    assert "sessionid" not in rendered


def test_context_cooldown_skips_only_matching_context(tmp_path):
    now = [100.0]
    auth_file = tmp_path / "instagram_auth.json"
    _write_auth_file(
        auth_file,
        {
            "instagram": ["mid=abc; sessionid=session-a"],
            "instagram_bearer": ["token=IGT:2:bearer-a"],
        },
    )
    pool = InstagramAuthPool.from_file(
        auth_file,
        max_contexts_per_attempt=2,
        cooldown_seconds=30,
        now_fn=lambda: now[0],
    )
    cookie, bearer = pool.get_contexts_for_attempt()

    pool.mark_cooldown(cookie, "http_403")

    assert pool.get_contexts_for_attempt() == [bearer]

    now[0] = 131.0
    assert pool.get_contexts_for_attempt() == [cookie, bearer]


def test_cooldown_reason_is_redacted():
    context = InstagramAuthContext(
        context_id="cookie:0",
        kind="cookie",
        value="mid=abc; sid=super-secret",
    )
    pool = InstagramAuthPool([context])

    pool.mark_cooldown(context, "http_403 sid=super-secret")

    rendered = repr(pool._cooldowns)  # noqa: SLF001 - regression coverage for repr leakage.
    assert "super-secret" not in rendered
    assert "sessionid" not in rendered
    assert pool._cooldowns["cookie:0"].reason == "classified_failure"  # noqa: SLF001


def test_round_robin_selection_returns_per_attempt_snapshots(tmp_path):
    auth_file = tmp_path / "instagram_auth.json"
    _write_auth_file(
        auth_file,
        {
            "instagram": [
                "mid=abc; sessionid=session-a",
                "mid=def; sessionid=session-b",
                "mid=ghi; sessionid=session-c",
            ]
        },
    )
    pool = InstagramAuthPool.from_file(auth_file, max_contexts_per_attempt=2)

    first = pool.get_contexts_for_attempt()
    second = pool.get_contexts_for_attempt()
    first_ids = [context.context_id for context in first]
    second_ids = [context.context_id for context in second]

    assert first_ids == ["cookie:0", "cookie:1"]
    assert second_ids == ["cookie:2", "cookie:0"]
    assert first_ids == ["cookie:0", "cookie:1"]


def test_available_does_not_advance_round_robin_cursor(tmp_path):
    auth_file = tmp_path / "instagram_auth.json"
    _write_auth_file(
        auth_file,
        {
            "instagram": [
                "mid=abc; sessionid=session-a",
                "mid=def; sessionid=session-b",
                "mid=ghi; sessionid=session-c",
            ]
        },
    )
    pool = InstagramAuthPool.from_file(auth_file, max_contexts_per_attempt=2)

    assert pool.available is True
    assert pool.available is True

    first = pool.get_contexts_for_attempt()
    assert [context.context_id for context in first] == ["cookie:0", "cookie:1"]


def test_selection_and_cooldown_are_thread_safe(tmp_path):
    auth_file = tmp_path / "instagram_auth.json"
    _write_auth_file(
        auth_file,
        {
            "instagram": [
                "mid=abc; sessionid=session-a",
                "mid=def; sessionid=session-b",
                "mid=ghi; sessionid=session-c",
            ],
            "instagram_bearer": ["token=IGT:2:bearer-a"],
        },
    )
    pool = InstagramAuthPool.from_file(auth_file, max_contexts_per_attempt=2)
    barrier = threading.Barrier(4)
    snapshots = []
    errors = []
    lock = threading.Lock()

    def worker():
        try:
            barrier.wait(timeout=1)
            contexts = pool.get_contexts_for_attempt()
            pool.mark_cooldown(contexts[0], "http_429")
            with lock:
                snapshots.append(tuple(context.context_id for context in contexts))
        except Exception as exc:  # pragma: no cover - failure path reported below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert len(snapshots) == 4
    assert all(1 <= len(snapshot) <= 2 for snapshot in snapshots)


def test_configured_loader_uses_settings_values(monkeypatch, tmp_path):
    auth_file = tmp_path / "instagram_auth.json"
    _write_auth_file(
        auth_file,
        {
            "instagram": ["mid=abc; sessionid=session-a"],
            "instagram_bearer": ["token=IGT:2:bearer-a"],
        },
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_auth_pool.settings.IG_AUTH_COOKIES_FILE",
        auth_file,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_auth_pool.settings.IG_AUTH_MAX_CONTEXTS_PER_ATTEMPT",
        1,
    )
    monkeypatch.setattr(
        "src.instagram_video_bot.services.instagram_auth_pool.settings.IG_AUTH_CONTEXT_COOLDOWN_SECONDS",
        10.0,
    )

    pool = load_configured_instagram_auth_pool()

    assert [context.context_id for context in pool.get_contexts_for_attempt()] == ["cookie:0"]
