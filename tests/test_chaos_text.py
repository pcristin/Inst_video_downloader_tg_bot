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
