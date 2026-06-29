import sys
from pathlib import Path
from types import SimpleNamespace

import manage_accounts


def test_export_auth_command_exports_all_configured_accounts(monkeypatch, tmp_path):
    accounts = [
        SimpleNamespace(username="first"),
        SimpleNamespace(username="second"),
    ]
    manager = SimpleNamespace(
        accounts=accounts,
        get_available_accounts=lambda: [accounts[0]],
    )
    called = {}

    def fake_export(selected_accounts, output_path):
        called["accounts"] = list(selected_accounts)
        called["output_path"] = output_path
        return SimpleNamespace(attempted=2, exported=2, failed=0, skipped=0)

    monkeypatch.setattr(manage_accounts, "get_account_manager", lambda: manager)
    monkeypatch.setattr(manage_accounts, "export_instagram_auth_file", fake_export)

    output_path = tmp_path / "instagram_auth.json"
    manage_accounts.export_auth_command(output_path=output_path)

    assert called == {"accounts": accounts, "output_path": output_path}


def test_export_auth_command_can_limit_to_available_accounts(monkeypatch, tmp_path):
    accounts = [
        SimpleNamespace(username="first"),
        SimpleNamespace(username="second"),
    ]
    manager = SimpleNamespace(
        accounts=accounts,
        get_available_accounts=lambda: [accounts[1]],
    )
    called = {}

    def fake_export(selected_accounts, output_path):
        called["accounts"] = list(selected_accounts)
        called["output_path"] = output_path
        return SimpleNamespace(attempted=1, exported=1, failed=0, skipped=0)

    monkeypatch.setattr(manage_accounts, "get_account_manager", lambda: manager)
    monkeypatch.setattr(manage_accounts, "export_instagram_auth_file", fake_export)

    output_path = tmp_path / "instagram_auth.json"
    manage_accounts.export_auth_command(output_path=output_path, available_only=True)

    assert called == {"accounts": [accounts[1]], "output_path": output_path}


def test_main_accepts_export_auth_options(monkeypatch, tmp_path):
    output_path = tmp_path / "instagram_auth.json"
    called = {}

    def fake_export_auth_command(*, output_path, available_only):
        called["output_path"] = output_path
        called["available_only"] = available_only

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_accounts.py",
            "export-auth",
            "--output",
            str(output_path),
            "--available-only",
        ],
    )
    monkeypatch.setattr(
        manage_accounts, "export_auth_command", fake_export_auth_command
    )

    manage_accounts.main()

    assert called == {"output_path": output_path, "available_only": True}
