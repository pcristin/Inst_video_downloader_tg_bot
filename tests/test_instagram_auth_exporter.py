import json
import logging
from pathlib import Path
from types import SimpleNamespace

from src.instagram_video_bot.utils import instagram_auth_exporter as exporter_module
from src.instagram_video_bot.utils.instagram_auth_exporter import (
    export_instagram_auth_file,
    session_settings_to_cookie_header,
)


def _account(username: str, tmp_path: Path):
    return SimpleNamespace(
        username=username,
        password=f"{username}-password",
        totp_secret=f"{username}-totp",
        proxy=None,
        session_file=tmp_path / "sessions" / f"{username}.json",
    )


def test_session_settings_to_cookie_header_keeps_only_safe_cookie_values():
    settings = {
        "cookies": {
            "mid": "mid-a",
            "csrftoken": "csrf-a",
            "ds_user_id": "42",
            "sessionid": "session-a",
            "rur": "ignored",
            "urlgen": "ignored-too",
            "bad": "unsafe;value",
        }
    }

    cookie_header = session_settings_to_cookie_header(settings)

    assert cookie_header == (
        "mid=mid-a; csrftoken=csrf-a; ds_user_id=42; sessionid=session-a"
    )


def test_session_settings_to_cookie_header_uses_authorization_data_fallback():
    settings = {
        "mid": "mid-b",
        "authorization_data": {
            "ds_user_id": "84",
            "sessionid": "session-b",
        },
    }

    cookie_header = session_settings_to_cookie_header(settings)

    assert cookie_header == "mid=mid-b; ds_user_id=84; sessionid=session-b"


def test_export_instagram_auth_file_logs_in_accounts_and_preserves_bearers(
    monkeypatch, tmp_path
):
    output_path = tmp_path / "secrets" / "instagram_auth.json"
    output_path.parent.mkdir()
    output_path.write_text(
        json.dumps({"instagram": ["old=cookie"], "instagram_bearer": ["Bearer old"]}),
        encoding="utf-8",
    )
    chown_calls = []
    monkeypatch.setattr(exporter_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        exporter_module.os,
        "chown",
        lambda path, uid, gid: chown_calls.append((path, uid, gid)),
    )
    settings_by_username = {
        "first": {
            "cookies": {
                "mid": "mid-first",
                "csrftoken": "csrf-first",
                "ds_user_id": "1",
                "sessionid": "session-first",
            }
        },
        "second": {
            "cookies": {
                "mid": "mid-second",
                "csrftoken": "csrf-second",
                "ds_user_id": "2",
                "sessionid": "session-second",
            }
        },
    }

    class FakeInnerClient:
        def __init__(self, settings):
            self._settings = settings

        def get_settings(self):
            return self._settings

    class FakeInstagramClient:
        def __init__(self, *, username, password, session_file, proxy, totp_secret):
            self.username = username
            self.password = password
            self.session_file = session_file
            self.proxy = proxy
            self.totp_secret = totp_secret
            self.client = FakeInnerClient(settings_by_username[username])

        def login(self):
            return True

    summary = export_instagram_auth_file(
        [_account("first", tmp_path), _account("second", tmp_path)],
        output_path,
        client_factory=FakeInstagramClient,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {
        "instagram": [
            "mid=mid-first; csrftoken=csrf-first; ds_user_id=1; sessionid=session-first",
            "mid=mid-second; csrftoken=csrf-second; ds_user_id=2; sessionid=session-second",
        ],
        "instagram_bearer": ["Bearer old"],
    }
    assert summary.attempted == 2
    assert summary.exported == 2
    assert summary.failed == 0
    assert output_path.stat().st_mode & 0o777 == 0o600
    assert chown_calls == [(output_path, 1000, 1000)]


def test_export_instagram_auth_file_falls_back_to_readable_mode_when_chown_fails(
    monkeypatch, tmp_path
):
    output_path = tmp_path / "secrets" / "instagram_auth.json"
    monkeypatch.setattr(exporter_module.os, "geteuid", lambda: 0)

    def fail_chown(_path, _uid, _gid):
        raise OSError("unsupported ownership")

    monkeypatch.setattr(exporter_module.os, "chown", fail_chown)

    class FakeInnerClient:
        def get_settings(self):
            return {"cookies": {"sessionid": "session-first"}}

    class FakeInstagramClient:
        def __init__(self, **_kwargs):
            self.client = FakeInnerClient()

        def login(self):
            return True

    summary = export_instagram_auth_file(
        [_account("first", tmp_path)],
        output_path,
        client_factory=FakeInstagramClient,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {
        "instagram": ["sessionid=session-first"],
        "instagram_bearer": [],
    }
    assert summary.exported == 1
    assert output_path.stat().st_mode & 0o777 == 0o644


def test_export_instagram_auth_file_redacts_login_failures(tmp_path, caplog):
    output_path = tmp_path / "secrets" / "instagram_auth.json"

    class FailingInstagramClient:
        def __init__(self, **_kwargs):
            pass

        def login(self):
            raise RuntimeError("login failed with sessionid=SECRET")

    with caplog.at_level(logging.WARNING):
        summary = export_instagram_auth_file(
            [_account("first", tmp_path)],
            output_path,
            client_factory=FailingInstagramClient,
        )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {"instagram": [], "instagram_bearer": []}
    assert summary.attempted == 1
    assert summary.exported == 0
    assert summary.failed == 1
    assert "SECRET" not in caplog.text
    assert "sessionid" not in caplog.text.lower()
