from src.instagram_video_bot.services.chaos_text import ChaosText, TextContext


def test_normal_submission_text_is_russian():
    text = ChaosText.submission(
        TextContext(provider_label="Instagram", chaos_enabled=False),
        queue_position=1,
        joined_existing=False,
    )

    assert text == "Принял Instagram. Скоро начну скачивать."


def test_chaos_submission_text_is_russian_and_provider_aware():
    text = ChaosText.submission(
        TextContext(provider_label="Twitter/X", chaos_enabled=True),
        queue_position=1,
        joined_existing=False,
    )

    assert "Twitter/X" in text
    assert "шум" in text.lower()


def test_chaos_duplicate_text_is_russian():
    text = ChaosText.submission(
        TextContext(provider_label="Instagram", chaos_enabled=True),
        queue_position=1,
        joined_existing=True,
    )

    assert "уже" in text.lower()
    assert "Instagram" in text


def test_error_text_is_russian_for_rate_limit():
    text = ChaosText.error(RuntimeError("rate limit"), chaos_enabled=False)

    assert "лимит" in text.lower()


def test_error_text_does_not_expose_raw_provider_stderr():
    text = ChaosText.error(
        RuntimeError("Twitter/X download failed: token=secret https://x.com/a/status/1"),
        chaos_enabled=False,
    )

    assert text == "Не смог скачать медиа. Попробуй позже."
    assert "secret" not in text
    assert "https://x.com" not in text


def test_chaos_error_text_does_not_expose_raw_provider_stderr():
    text = ChaosText.error(
        RuntimeError("Download failed: proxy=http://user:pass@example.com:8080"),
        chaos_enabled=True,
    )

    assert text == "Не смог скачать медиа. Попробуй позже."
    assert "user:pass" not in text
    assert "proxy" not in text


def test_unsupported_error_text_does_not_echo_raw_exception():
    text = ChaosText.error(
        RuntimeError("Unsupported Twitter/X URL https://x.com/not/status"),
        chaos_enabled=False,
    )

    assert text == "Эта ссылка не поддерживается."
    assert "https://x.com" not in text
