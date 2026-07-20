from telegram.error import NetworkError

from src.instagram_video_bot.services.job_states import (
    FailureDetails,
    FailureReason,
    FailureStage,
    JobState,
    classify_failure,
)


def test_job_state_values_remain_storage_compatible():
    assert JobState.QUEUED.value == "queued"
    assert JobState.RUNNING.value == "running"
    assert JobState.COMPLETED.value == "completed"
    assert JobState.FAILED.value == "failed"
    assert JobState.CANCELLED.value == "cancelled"


def test_provider_timeout_is_retryable():
    details = classify_failure(
        TimeoutError("provider timed out"), stage=FailureStage.ACQUISITION
    )

    assert details == FailureDetails(
        FailureReason.PROVIDER_TIMEOUT,
        retryable=True,
    )


def test_unsupported_url_is_not_retryable():
    details = classify_failure(
        ValueError("unsupported URL"), stage=FailureStage.ACQUISITION
    )

    assert details.reason is FailureReason.UNSUPPORTED_URL
    assert details.retryable is False


def test_ambiguous_delivery_is_never_retryable():
    details = classify_failure(
        NetworkError("send timed out"),
        stage=FailureStage.DELIVERY,
        ambiguous_delivery=True,
    )

    assert details.reason is FailureReason.DELIVERY_AMBIGUOUS
    assert details.retryable is False
