import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import instagrapi

import rotate_accounts
from src.instagram_video_bot.utils.account_manager import AccountManager

SEED = "JBSWY3DPEHPK3PXP"
SESSION = {"cookies": {"sessionid": "example-session", "ds_user_id": "123"}}


def test_candidates_accept_tab_or_pipe_and_normalize_totp(tmp_path):
    path = tmp_path / "candidates.txt"
    path.write_text(f"first\tpw1\tJBSW Y3DP EHPK 3PXP\nsecond|pw2|{SEED}\n")

    accounts = rotate_accounts.read_candidates(path)

    assert [account.username for account in accounts] == ["first", "second"]
    assert accounts[0].totp_secret == SEED
    assert accounts[0].account_line() == f"first|pw1|{SEED}\n"


def test_candidate_file_requires_private_mode(tmp_path):
    path = tmp_path / "new.txt"
    path.write_text(f"first|pw1|{SEED}\n")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        rotate_accounts.assert_private_candidate_file(path)
    path.chmod(0o600)
    rotate_accounts.assert_private_candidate_file(path)


def test_empty_totp_is_valid_but_unavailable(tmp_path, monkeypatch):
    path = tmp_path / "new.txt"
    path.write_text("first|pw1|\n")
    monkeypatch.setattr(
        rotate_accounts.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("empty TOTP must not reach login worker"),
    )
    summary = rotate_accounts.prewarm(path, tmp_path / "stage", project=tmp_path)
    assert summary["failed"] == 1
    stage = rotate_accounts._stage_for(
        tmp_path / "stage", rotate_accounts.read_candidates(path)
    )
    assert json.loads((stage / "results.json").read_text())["results"] == {
        "0": "missing_totp"
    }


def test_single_proxy_fallback_matches_runtime(monkeypatch):
    settings_class = type(rotate_accounts.settings)
    monkeypatch.setattr(settings_class, "get_proxy_list", lambda _self: [])
    monkeypatch.setattr(
        settings_class, "get_single_proxy", lambda _self: "http://example.test:8080"
    )
    assert rotate_accounts.account_proxy(3) == "http://example.test:8080"


@pytest.mark.parametrize(
    "content",
    [
        "first\tpassword\n",
        f"first|password|{SEED}\nfirst|password|{SEED}\n",
        f"First|password|{SEED}\nfirst|password|{SEED}\n",
        "../bad|password|JBSWY3DPEHPK3PXP\n",
        "first|password|not-base32!\n",
    ],
)
def test_candidates_reject_invalid_roster_without_leaking_password(tmp_path, content):
    path = tmp_path / "candidates.txt"
    path.write_text(content)

    with pytest.raises(ValueError) as error:
        rotate_accounts.read_candidates(path)

    assert "password" not in str(error.value)


def test_prewarm_reuses_seeded_session_and_records_failure(tmp_path, monkeypatch):
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\nsecond|pw2|{SEED}\n")
    seeded = tmp_path / "seeded"
    seeded.mkdir()
    (seeded / "first.json").write_text(json.dumps(SESSION))
    monkeypatch.setattr(rotate_accounts, "_verify_session", lambda *_args: "success")
    monkeypatch.setattr(
        rotate_accounts.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Completed", (), {"stdout": '{"result":"ClientError"}\n', "returncode": 1}
        )(),
    )

    summary = rotate_accounts.prewarm(candidate_file, tmp_path / "stage", seeded)
    stage = rotate_accounts._stage_for(
        tmp_path / "stage", rotate_accounts.read_candidates(candidate_file)
    )
    results = json.loads((stage / "results.json").read_text())

    assert summary == {
        "total": 2,
        "success": 1,
        "failed": 1,
        "added": 2,
        "changed": 0,
        "removed": 0,
    }
    assert results["results"] == {"0": "success", "1": "ClientError"}
    result_text = (stage / "results.json").read_text()
    assert all(secret not in result_text for secret in ("pw1", "pw2", SEED))


def test_prewarm_records_worker_success_and_saved_session(tmp_path, monkeypatch):
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\n")
    calls = []

    def worker(command, **_kwargs):
        calls.append(command)
        stage = Path(command[command.index("--stage-root") + 1])
        (stage / "sessions").mkdir(parents=True, exist_ok=True)
        (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
        return type(
            "Completed", (), {"stdout": '{"result":"success"}\n', "returncode": 0}
        )()

    monkeypatch.setattr(rotate_accounts.subprocess, "run", worker)
    summary = rotate_accounts.prewarm(
        candidate_file, tmp_path / "stage", project=tmp_path
    )
    assert summary["success"] == 1
    assert len(calls) == 1
    assert "worker" in calls[0]
    stage = rotate_accounts._stage_for(
        tmp_path / "stage", rotate_accounts.read_candidates(candidate_file)
    )
    assert (stage / "sessions" / "first.json").exists()


def test_activate_requires_every_account_attempted(tmp_path):
    candidates = tmp_path / "new.txt"
    candidates.write_text(f"first|pw1|{SEED}\n")

    with pytest.raises(ValueError, match="every candidate"):
        rotate_accounts.activate(candidates, tmp_path / "stage", tmp_path / "project")


def test_activate_requires_matching_media_canary_before_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate_accounts, "_verify_session", lambda *_args: "success")
    project = tmp_path / "project"
    project.mkdir()
    roster = project / "accounts.txt"
    roster.write_text(f"old|pw|{SEED}\n")
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidate_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    session = stage / "sessions" / "first.json"
    session.write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})

    with pytest.raises(ValueError, match="canary"):
        rotate_accounts.activate(candidate_file, tmp_path / "stage", project)
    assert roster.read_text() == f"old|pw|{SEED}\n"

    rotate_accounts._write_canary_results(
        stage,
        candidates,
        {
            "0": {
                "result": "success",
                "session_sha256": rotate_accounts._session_hash(session),
            }
        },
    )
    session.write_text(json.dumps({**SESSION, "new": True}))
    with pytest.raises(ValueError, match="canary"):
        rotate_accounts.activate(candidate_file, tmp_path / "stage", project)
    assert roster.read_text() == f"old|pw|{SEED}\n"


def test_activate_preserves_ramp_age_for_unchanged_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate_accounts, "_verify_session", lambda *_args: "success")
    project = tmp_path / "project"
    project.mkdir()
    (project / "accounts.txt").write_text(
        f"first|pw1|{SEED}\nsecond|pw2|{SEED}\nthird|pw3|{SEED}\n"
    )
    (project / "account-state").mkdir()
    (project / "account-state" / "accounts_state.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "username": "first",
                        "activated_at": "2026-01-01T00:00:00",
                        "last_used": "2026-09-27T12:00:00",
                    },
                    {"username": "second", "activated_at": None, "is_banned": True},
                    {"username": "third", "is_banned": False},
                ]
            }
        )
    )
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(
        f"first|pw1|{SEED}\nsecond|pw2|{SEED}\nthird|pw3|{SEED}\n"
    )
    candidates = rotate_accounts.read_candidates(candidate_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    canaries = {}
    for index, candidate in enumerate(candidates):
        session = stage / "sessions" / f"{candidate.username}.json"
        session.write_text(json.dumps(SESSION))
        canaries[str(index)] = {
            "result": "success",
            "session_sha256": rotate_accounts._session_hash(session),
        }
    rotate_accounts._write_results(
        stage, candidates, {"0": "success", "1": "success", "2": "success"}
    )
    rotate_accounts._write_canary_results(stage, candidates, canaries)

    rotate_accounts.activate(candidate_file, tmp_path / "stage", project)

    state = json.loads((project / "account-state" / "accounts_state.json").read_text())
    assert state["accounts"][0]["activated_at"] == "2026-01-01T00:00:00"
    assert state["accounts"][0]["last_used"] == "2026-09-27T12:00:00"
    assert state["accounts"][1]["activated_at"] is not None
    assert state["accounts"][2]["activated_at"] is None


def test_activate_rejects_session_replaced_during_verification(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    roster = project / "accounts.txt"
    roster.write_text(f"old|pw|{SEED}\n")
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidate_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    session = stage / "sessions" / "first.json"
    session.write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})
    rotate_accounts._write_canary_results(
        stage,
        candidates,
        {
            "0": {
                "result": "success",
                "session_sha256": rotate_accounts._session_hash(session),
            }
        },
    )

    def replace_session(*_args):
        session.write_text(json.dumps({**SESSION, "renewed": True}))
        return "success"

    monkeypatch.setattr(rotate_accounts, "_verify_session", replace_session)
    with pytest.raises(ValueError, match="canary"):
        rotate_accounts.activate(candidate_file, tmp_path / "stage", project)
    assert roster.read_text() == f"old|pw|{SEED}\n"


def test_canary_checks_saved_session_and_downloads_media(tmp_path, monkeypatch, capsys):
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\n")
    stage = tmp_path / "stage"
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    monkeypatch.setattr(rotate_accounts, "account_proxy", lambda _index: None)

    class FakeClient:
        def load_settings(self, _path):
            pass

        def account_info(self):
            return SimpleNamespace(username="first", pk="123")

        def get_settings(self):
            return SESSION

        def media_pk_from_url(self, _url):
            return "456"

        def media_info(self, _pk):
            return SimpleNamespace(media_type=1)

        def photo_download(self, _pk, folder):
            output = folder / "sample.jpg"
            output.write_bytes(b"image")
            return output

    monkeypatch.setattr(instagrapi, "Client", FakeClient)
    assert (
        rotate_accounts.canary_worker(
            candidate_file, 0, stage, "https://www.instagram.com/p/test/"
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["result"] == "success"


def test_canary_records_success_for_exact_session_and_skips_failed_logins(
    tmp_path, monkeypatch
):
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\nsecond|pw2|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidate_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    rotate_accounts._write_results(
        stage, candidates, {"0": "success", "1": "LoginRequired"}
    )
    calls = []

    def worker(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(stdout='{"result":"success"}\n', returncode=0)

    monkeypatch.setattr(rotate_accounts.subprocess, "run", worker)
    first = rotate_accounts.canary(
        candidate_file, tmp_path / "stage", "https://www.instagram.com/p/test/"
    )
    second = rotate_accounts.canary(
        candidate_file, tmp_path / "stage", "https://www.instagram.com/p/test/"
    )

    assert first == second == {"total": 2, "passed": 1, "pending": 0}
    assert len(calls) == 1
    assert "canary-worker" in calls[0]
    assert rotate_accounts._load_canary_results(stage, candidates)["0"][
        "session_sha256"
    ] == rotate_accounts._session_hash(stage / "sessions" / "first.json")


def test_canary_rejects_session_replaced_during_worker(tmp_path, monkeypatch):
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidate_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    session = stage / "sessions" / "first.json"
    session.write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})

    def worker(*_args, **_kwargs):
        session.write_text(json.dumps({**SESSION, "renewed": True}))
        return SimpleNamespace(stdout='{"result":"success"}\n', returncode=0)

    monkeypatch.setattr(rotate_accounts.subprocess, "run", worker)
    summary = rotate_accounts.canary(
        candidate_file, tmp_path / "stage", "https://www.instagram.com/p/test/"
    )

    assert summary["passed"] == 0
    assert summary["pending"] == 1
    assert (
        rotate_accounts._load_canary_results(stage, candidates)["0"]["result"]
        != "success"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.instagram.com/p/test/",
        "https://evil.test/p/test/",
        "https://www.instagram.com/accounts/login/",
    ],
)
def test_canary_rejects_non_media_urls(tmp_path, url):
    with pytest.raises(ValueError, match="canary URL"):
        rotate_accounts.canary(tmp_path / "missing", tmp_path / "stage", url)


def test_activate_replaces_old_roster_state_auth_and_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate_accounts, "_verify_session", lambda *_args: "success")
    project = tmp_path / "project"
    (project / "sessions").mkdir(parents=True)
    (project / "account-state").mkdir()
    (project / "secrets").mkdir()
    (project / "accounts.txt").write_text(f"old|old-password|{SEED}\n")
    (project / "sessions" / "old.json").write_text(json.dumps(SESSION))
    (project / "sessions" / "second.json").write_text(json.dumps(SESSION))
    (project / "account-state" / "accounts_state.json").write_text(
        '{"accounts":[{"username":"old","password":"old-password"}]}'
    )
    (project / "secrets" / "instagram_auth.json").write_text(
        '{"instagram":["old-cookie"],"instagram_bearer":[]}'
    )
    candidate_file = tmp_path / "new.txt"
    candidate_file.write_text(f"first|pw1|{SEED}\nsecond|pw2|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidate_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    rotate_accounts._write_results(
        stage, candidates, {"0": "success", "1": "ClientError"}
    )
    rotate_accounts._write_canary_results(
        stage,
        candidates,
        {
            "0": {
                "result": "success",
                "session_sha256": rotate_accounts._session_hash(
                    stage / "sessions" / "first.json"
                ),
            }
        },
    )

    summary = rotate_accounts.activate(candidate_file, tmp_path / "stage", project)

    assert summary == {"total": 2, "success": 1, "failed": 1}
    assert (
        project / "accounts.txt"
    ).read_text() == f"first|pw1|{SEED}\nsecond|pw2|{SEED}\n"
    assert not (project / "sessions" / "old.json").exists()
    assert not (project / "sessions" / "second.json").exists()
    assert (project / "sessions" / "first.json").exists()
    for path in (
        project / "accounts.txt",
        project / "account-state" / "accounts_state.json",
        project / "secrets" / "instagram_auth.json",
        project / "sessions" / "first.json",
    ):
        assert path.stat().st_mode & 0o777 == 0o600
    state = json.loads((project / "account-state" / "accounts_state.json").read_text())
    assert state["accounts"][1]["is_banned"] is True
    assert "password" not in state["accounts"][0]
    assert (
        json.loads((project / "secrets" / "instagram_auth.json").read_text())
        == rotate_accounts.AUTH_EMPTY
    )
    manager = AccountManager(
        project / "accounts.txt", project / "account-state" / "accounts_state.json"
    )
    assert [account.username for account in manager.get_available_accounts()] == [
        "first"
    ]


def test_activate_restores_old_files_if_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate_accounts, "_verify_session", lambda *_args: "success")
    project = tmp_path / "project"
    (project / "sessions").mkdir(parents=True)
    (project / "account-state").mkdir()
    (project / "secrets").mkdir()
    old_roster = project / "accounts.txt"
    old_roster.write_text(f"old|old-password|{SEED}\n")
    old_session = project / "sessions" / "old.json"
    old_session.write_text(json.dumps(SESSION))
    auth = project / "secrets" / "instagram_auth.json"
    auth.write_text('{"instagram":["old-cookie"]}')
    candidates_file = tmp_path / "new.txt"
    candidates_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidates_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})
    rotate_accounts._write_canary_results(
        stage,
        candidates,
        {
            "0": {
                "result": "success",
                "session_sha256": rotate_accounts._session_hash(
                    stage / "sessions" / "first.json"
                ),
            }
        },
    )
    write = rotate_accounts._private_write
    failed = False

    def fail_once(path, data, owner=None):
        nonlocal failed
        if path == auth and not failed:
            failed = True
            raise OSError("simulated failure")
        return write(path, data, owner)

    monkeypatch.setattr(rotate_accounts, "_private_write", fail_once)

    with pytest.raises(OSError, match="simulated failure"):
        rotate_accounts.activate(candidates_file, tmp_path / "stage", project)

    assert old_roster.read_text() == f"old|old-password|{SEED}\n"
    assert old_session.exists()
    assert not (project / "sessions" / "first.json").exists()
    assert auth.read_text() == '{"instagram":["old-cookie"]}'


def test_activate_rejects_unverified_identity_before_mutation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    roster = project / "accounts.txt"
    roster.write_text(f"old|old-password|{SEED}\n")
    (project / "sessions").mkdir()
    old_session = project / "sessions" / "old.json"
    old_session.write_text(json.dumps(SESSION))
    (project / "account-state").mkdir()
    old_state = project / "account-state" / "accounts_state.json"
    old_state.write_text('{"accounts":[{"username":"old"}]}')
    (project / "secrets").mkdir()
    old_auth = project / "secrets" / "instagram_auth.json"
    old_auth.write_text('{"instagram":["old-cookie"]}')
    candidates_file = tmp_path / "new.txt"
    candidates_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidates_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})
    rotate_accounts._write_canary_results(
        stage,
        candidates,
        {
            "0": {
                "result": "success",
                "session_sha256": rotate_accounts._session_hash(
                    stage / "sessions" / "first.json"
                ),
            }
        },
    )
    monkeypatch.setattr(
        rotate_accounts, "_verify_session", lambda *_args: "identity_mismatch"
    )

    with pytest.raises(ValueError, match="failed verification"):
        rotate_accounts.activate(candidates_file, tmp_path / "stage", project)

    assert roster.read_text() == f"old|old-password|{SEED}\n"
    assert old_session.read_text() == json.dumps(SESSION)
    assert old_state.read_text() == '{"accounts":[{"username":"old"}]}'
    assert old_auth.read_text() == '{"instagram":["old-cookie"]}'


@pytest.mark.parametrize(
    "identity_name,expected", [("first", "success"), ("other", "identity_mismatch")]
)
def test_verify_worker_checks_authenticated_identity(
    tmp_path, monkeypatch, capsys, identity_name, expected
):
    candidates = tmp_path / "new.txt"
    candidates.write_text(f"first|pw1|{SEED}\n")
    stage = tmp_path / "stage"
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    monkeypatch.setattr(rotate_accounts, "account_proxy", lambda _index: None)

    class FakeClient:
        def load_settings(self, _path):
            pass

        def account_info(self):
            return SimpleNamespace(username=identity_name, pk="123")

        def get_settings(self):
            return SESSION

    monkeypatch.setattr(instagrapi, "Client", FakeClient)
    result = rotate_accounts.verify_worker(candidates, 0, stage)
    assert result == (0 if expected == "success" else 1)
    assert json.loads(capsys.readouterr().out)["result"] == expected


def test_login_worker_rejects_wrong_account_before_saving(
    tmp_path, monkeypatch, capsys
):
    candidates = tmp_path / "new.txt"
    candidates.write_text(f"first|pw1|{SEED}\n")
    stage = tmp_path / "stage"
    monkeypatch.setattr(rotate_accounts, "account_proxy", lambda _index: None)

    class FakeClient:
        def login(self, *_args, **_kwargs):
            return True

        def account_info(self):
            return SimpleNamespace(username="other", pk="123")

        def get_settings(self):
            return SESSION

    monkeypatch.setattr(instagrapi, "Client", FakeClient)
    assert rotate_accounts.login_worker(candidates, 0, stage) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "identity_mismatch"
    assert not (stage / "sessions" / "first.json").exists()


def test_session_verification_retries_transient_error(tmp_path, monkeypatch):
    calls = []

    def result(*_args, **_kwargs):
        calls.append(1)
        category = "ClientConnectionError" if len(calls) == 1 else "success"
        return SimpleNamespace(
            stdout=json.dumps({"result": category}),
            returncode=0 if category == "success" else 1,
        )

    monkeypatch.setattr(rotate_accounts.subprocess, "run", result)
    assert (
        rotate_accounts._verify_session(tmp_path / "new.txt", 0, tmp_path / "stage")
        == "success"
    )
    assert len(calls) == 2


def test_prewarm_keeps_session_when_verification_is_temporarily_unavailable(
    tmp_path, monkeypatch
):
    candidates_file = tmp_path / "new.txt"
    candidates_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidates_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    session = stage / "sessions" / "first.json"
    session.write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})
    monkeypatch.setattr(
        rotate_accounts, "_verify_session", lambda *_args: "ClientConnectionError"
    )
    monkeypatch.setattr(
        rotate_accounts.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must not re-login"),
    )

    summary = rotate_accounts.prewarm(
        candidates_file, tmp_path / "stage", project=tmp_path
    )

    assert summary["success"] == 1
    assert session.read_text() == json.dumps(SESSION)


def test_prewarm_imports_replacement_seed_after_staged_session_expires(
    tmp_path, monkeypatch
):
    candidates_file = tmp_path / "new.txt"
    candidates_file.write_text(f"first|pw1|{SEED}\n")
    candidates = rotate_accounts.read_candidates(candidates_file)
    stage = rotate_accounts._stage_for(tmp_path / "stage", candidates)
    (stage / "sessions").mkdir(parents=True)
    session = stage / "sessions" / "first.json"
    session.write_text(json.dumps(SESSION))
    rotate_accounts._write_results(stage, candidates, {"0": "success"})
    seeded = tmp_path / "seeded"
    seeded.mkdir()
    replacement = {"cookies": {"sessionid": "replacement", "ds_user_id": "123"}}
    (seeded / "first.json").write_text(json.dumps(replacement))
    responses = iter(["LoginRequired", "success"])
    monkeypatch.setattr(
        rotate_accounts, "_verify_session", lambda *_args: next(responses)
    )
    monkeypatch.setattr(
        rotate_accounts.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must not re-login"),
    )

    summary = rotate_accounts.prewarm(
        candidates_file, tmp_path / "stage", seeded, project=tmp_path
    )

    assert summary["success"] == 1
    assert json.loads(session.read_text()) == replacement


def test_verify_worker_reports_missing_session_without_contacting_instagram(
    tmp_path, capsys
):
    candidates = tmp_path / "new.txt"
    candidates.write_text(f"first|pw1|{SEED}\n")
    assert rotate_accounts.verify_worker(candidates, 0, tmp_path / "stage") == 1
    assert json.loads(capsys.readouterr().out)["result"] == "session_missing"


def test_verify_worker_reports_provider_error_without_leaking_details(
    tmp_path, monkeypatch, capsys
):
    candidates = tmp_path / "new.txt"
    candidates.write_text(f"first|pw1|{SEED}\n")
    stage = tmp_path / "stage"
    (stage / "sessions").mkdir(parents=True)
    (stage / "sessions" / "first.json").write_text(json.dumps(SESSION))
    monkeypatch.setattr(rotate_accounts, "account_proxy", lambda _index: None)

    class FakeClient:
        def load_settings(self, _path):
            raise RuntimeError("secret provider detail")

    monkeypatch.setattr(instagrapi, "Client", FakeClient)
    assert rotate_accounts.verify_worker(candidates, 0, stage) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["result"] == "RuntimeError"
    assert "secret provider detail" not in output
