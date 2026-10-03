"""Direct candidates never stage partial albums or use login recovery."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from src.instagram_video_bot.services.download_models import DownloadError
from src.instagram_video_bot.services.instagram_client import InstagramAuthError
from src.instagram_video_bot.services import video_downloader

VIDEO = "https://scontent.cdninstagram.com/media.mp4"
PHOTO = "https://scontent.fbcdn.net/photo.jpg"


@pytest.fixture
def source_env(monkeypatch, tmp_path):
    module = importlib.import_module(
        "src.instagram_video_bot.services.instagram_direct_sources"
    )
    account = SimpleNamespace(
        username="test", session_file=tmp_path / "session.json", proxy=None
    )
    account.session_file.write_text("{}")
    raw = {
        "media_type": 2,
        "video_versions": [{"url": VIDEO}],
        "caption": {"text": "Caption"},
    }
    api = Mock()
    api.load_settings.return_value = {"authorization_data": {"sessionid": "fake"}}
    api.media_pk_from_url.return_value = "123"
    api.private_request.return_value = {"items": [raw]}
    client = SimpleNamespace(client=api)
    factory = Mock(return_value=client)
    monkeypatch.setattr(video_downloader, "_SavedSessionInstagramClient", factory)
    responses = []

    def head(url, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (3, 5)
        response = Mock(
            status_code=200,
            headers={
                "Content-Length": "1000",
                "Content-Type": "image/jpeg" if url == PHOTO else "video/mp4",
            },
        )
        responses.append(response)
        return response

    head_mock = Mock(side_effect=head)
    monkeypatch.setattr(module.requests, "head", head_mock)
    probe = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 720,
                "height": 1280,
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "10.25"},
    }

    def run(cmd, **kwargs):
        assert kwargs["timeout"] == 12
        assert cmd[cmd.index("-rw_timeout") + 1] == "8000000"
        return SimpleNamespace(returncode=0, stdout=json.dumps(probe))

    monkeypatch.setattr(module.subprocess, "run", run)
    return SimpleNamespace(
        module=module,
        account=account,
        raw=raw,
        api=api,
        factory=factory,
        probe=probe,
        head=head_mock,
        responses=responses,
        output=tmp_path / "output",
    )


def extract(env):
    return env.module.extract_direct_sources(
        env.account, "https://www.instagram.com/p/ABC/", env.output
    )


def test_ready_video_uses_saved_settings_without_authentication(source_env):
    env = source_env
    result = extract(env)
    assert result.title == "Caption"
    assert result.media_items[0].remote_url == VIDEO
    assert (
        result.media_items[0].width,
        result.media_items[0].height,
        result.duration,
    ) == (720, 1280, 10.25)
    assert not env.output.exists()
    env.factory.assert_called_once_with(
        username="test",
        password="",
        session_file=env.account.session_file,
        proxy=None,
        totp_secret=None,
    )
    env.api.private_request.assert_called_once_with("media/123/info/")
    env.api.login.assert_not_called()
    env.api.get_timeline_feed.assert_not_called()
    assert all(response.close.called for response in env.responses)


def test_complete_mixed_album_preserves_order(source_env):
    env = source_env
    env.raw.update(
        media_type=8,
        carousel_media=[
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": PHOTO, "width": 1080, "height": 1080}]
                },
            },
            {"media_type": 2, "video_versions": [{"url": VIDEO}]},
        ],
    )
    result = extract(env)
    assert [item.remote_url for item in result.media_items] == [PHOTO, VIDEO]
    assert [item.media_type for item in result.media_items] == ["photo", "video"]


@pytest.mark.parametrize(
    "mutation", ["silent", "hevc", "pixel_format", "zero_width", "nan_duration"]
)
def test_incompatible_video_is_ineligible_without_partial_output(source_env, mutation):
    env = source_env
    if mutation == "silent":
        env.probe["streams"].pop()
    elif mutation == "hevc":
        env.probe["streams"][0]["codec_name"] = "hevc"
    elif mutation == "pixel_format":
        env.probe["streams"][0]["pix_fmt"] = "yuv444p"
    elif mutation == "zero_width":
        env.probe["streams"][0]["width"] = 0
    else:
        env.probe["format"]["duration"] = "nan"
    with pytest.raises(DownloadError, match="^direct_candidate_ineligible$"):
        extract(env)
    assert not env.output.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://scontent.fbcdn.net/a",
        "https://fbcdn.net.attacker.test/a",
        "https://user:password@scontent.fbcdn.net/a",
        "https://127.0.0.1/a",
    ],
)
def test_untrusted_source_never_reaches_network(source_env, url):
    env = source_env
    env.raw["video_versions"][0]["url"] = url
    with pytest.raises(DownloadError, match="direct_candidate_ineligible"):
        extract(env)
    env.head.assert_not_called()


@pytest.mark.parametrize(
    "status,headers",
    [
        (302, {"Location": "https://attacker.test/a"}),
        (200, {"Content-Type": "video/mp4"}),
        (200, {"Content-Type": "video/mp4", "Content-Length": "20000001"}),
        (200, {"Content-Type": "text/html", "Content-Length": "100"}),
    ],
)
def test_invalid_head_response_is_ineligible(source_env, status, headers):
    env = source_env
    env.head.side_effect = None
    env.head.return_value = Mock(status_code=status, headers=headers)
    with pytest.raises(DownloadError, match="direct_candidate_ineligible"):
        extract(env)


@pytest.mark.parametrize(
    "width,height,length,mime",
    [
        (10001, 1, "100", "image/jpeg"),
        (2000, 50, "100", "image/jpeg"),
        (100, 100, "5000001", "image/jpeg"),
        (100, 100, "100", "image/webp"),
    ],
)
def test_photo_constraints_reject_entire_album(source_env, width, height, length, mime):
    env = source_env
    env.raw.update(
        media_type=8,
        carousel_media=[
            {"media_type": 2, "video_versions": [{"url": VIDEO}]},
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": PHOTO, "width": width, "height": height}]
                },
            },
        ],
    )
    env.head.side_effect = [
        Mock(
            status_code=200,
            headers={"Content-Type": "video/mp4", "Content-Length": "100"},
        ),
        Mock(status_code=200, headers={"Content-Type": mime, "Content-Length": length}),
    ]
    with pytest.raises(DownloadError, match="direct_candidate_ineligible"):
        extract(env)
    assert not env.output.exists()


@pytest.mark.parametrize(
    "error,reason",
    [
        (LoginRequired, "auth_challenge"),
        (ChallengeRequired, "auth_challenge"),
        (BadPassword, "invalid username or password"),
        (PleaseWaitFewMinutes, "rate_limited"),
        (FeedbackRequired, "rate_limited"),
    ],
)
def test_explicit_rejection_retains_auth_reason(source_env, error, reason):
    source_env.api.private_request.side_effect = error("private details")
    with pytest.raises(InstagramAuthError, match=f"^{reason}$"):
        extract(source_env)


def test_transport_error_is_not_auth_rejection(source_env):
    source_env.api.private_request.side_effect = requests.ConnectionError("offline")
    with pytest.raises(requests.ConnectionError):
        extract(source_env)


def test_missing_session_does_not_authenticate(source_env):
    source_env.account.session_file.unlink()
    with pytest.raises(DownloadError, match="saved_session_unavailable"):
        extract(source_env)
    source_env.api.private_request.assert_not_called()


def test_private_api_challenge_callback_cannot_attempt_recovery(source_env):
    from instagrapi.mixins.private import PrivateRequestMixin

    api = source_env.api
    api.authorization = None
    api.delay_range = None
    api.private_requests_count = 0
    api.handle_exception = None
    api.last_json = {"challenge": {"url": "https://www.instagram.com/challenge/"}}
    api._send_private_request.side_effect = ChallengeRequired("challenge")
    api.private_request.side_effect = (
        lambda endpoint: PrivateRequestMixin.private_request(api, endpoint)
    )
    with pytest.raises(InstagramAuthError, match="auth_challenge"):
        extract(source_env)
    api.challenge_resolve.assert_not_called()


@pytest.mark.parametrize("items", [{"bad": "shape"}, "not a list", [None]])
def test_malformed_metadata_is_not_authentication_failure(source_env, items):
    source_env.api.private_request.return_value = {"items": items}
    with pytest.raises(DownloadError, match="direct_metadata_unavailable"):
        extract(source_env)


def test_silent_member_rejects_album_before_any_head(source_env):
    env = source_env
    env.raw.update(
        media_type=8,
        carousel_media=[
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": PHOTO, "width": 1080, "height": 1080}]
                },
            },
            {"media_type": 2, "video_versions": [{"url": VIDEO}]},
        ],
    )
    env.probe["streams"].pop()
    with pytest.raises(DownloadError, match="direct_candidate_ineligible"):
        extract(env)
    env.head.assert_not_called()


def test_probe_timeout_is_ineligible_and_redacts_source(source_env, monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["ffprobe", VIDEO], 12)

    monkeypatch.setattr(source_env.module.subprocess, "run", timeout)
    with pytest.raises(DownloadError, match="^direct_candidate_ineligible$") as error:
        extract(source_env)
    assert VIDEO not in str(error.value)
    assert error.value.__suppress_context__


def test_exhausted_preflight_budget_stops_before_network(source_env, monkeypatch):
    clock = iter([0, 19])
    monkeypatch.setattr(source_env.module.time, "monotonic", lambda: next(clock))
    with pytest.raises(DownloadError, match="direct_candidate_ineligible"):
        extract(source_env)
    source_env.head.assert_not_called()
