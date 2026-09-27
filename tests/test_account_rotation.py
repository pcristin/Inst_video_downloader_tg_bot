import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "content",
    [
        "first\tpassword\n",
        f"first|password|{SEED}\nfirst|password|{SEED}\n",
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
    assert "pw1" not in (stage / "results.json").read_text()


def test_activate_requires_every_account_attempted(tmp_path):
    candidates = tmp_path / "new.txt"
    candidates.write_text(f"first|pw1|{SEED}\n")

    with pytest.raises(ValueError, match="every candidate"):
        rotate_accounts.activate(candidates, tmp_path / "stage", tmp_path / "project")


def test_activate_replaces_old_roster_state_auth_and_sessions(tmp_path):
    project = tmp_path / "project"
    (project / "sessions").mkdir(parents=True)
    (project / "account-state").mkdir()
    (project / "secrets").mkdir()
    (project / "accounts.txt").write_text(f"old|old-password|{SEED}\n")
    (project / "sessions" / "old.json").write_text(json.dumps(SESSION))
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

    summary = rotate_accounts.activate(candidate_file, tmp_path / "stage", project)

    assert summary == {"total": 2, "success": 1, "failed": 1}
    assert (
        project / "accounts.txt"
    ).read_text() == f"first|pw1|{SEED}\nsecond|pw2|{SEED}\n"
    assert not (project / "sessions" / "old.json").exists()
    assert (project / "sessions" / "first.json").exists()
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
