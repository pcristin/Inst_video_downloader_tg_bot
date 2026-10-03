"""Offline regression tests for live-canary eligibility and measurement controls."""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.instagram_video_bot.utils.account_manager import Account, AccountManager

spec = importlib.util.spec_from_file_location(
    "benchmark_support",
    Path(__file__).resolve().parents[1]
    / "docs/experiments/2026-10-03-media-race/benchmark_support.py",
)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def test_eligibility_uses_credentials_sessions_leases_and_ramp(tmp_path):
    session = tmp_path / "session.json"
    session.write_text("{}")
    good = Account("good", "password", "totp", session_file=session)
    missing_totp = Account("missing-totp", "password", session_file=session)
    ramped = Account(
        "ramped",
        "password",
        "totp",
        session_file=session,
        activated_at=datetime.now(),
        last_used=datetime.now(),
    )
    leased = Account("leased", "password", "totp", session_file=session)
    missing_session = Account("missing-session", "password", "totp")
    manager = object.__new__(AccountManager)
    manager.accounts = [good, missing_totp, ramped, leased, missing_session]
    manager._leased_accounts = {"leased"}
    assert support.eligible_accounts(manager) == [good]
    ramped.activated_at = datetime.now() - timedelta(days=6)
    assert support.eligible_accounts(manager) == [good, ramped]


def test_modes_copy_account_health_and_sessions(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    session.write_text("original")
    account = Account("user", "password", "totp", session_file=session)
    manager = object.__new__(AccountManager)
    manager.accounts = [account]
    manager._leased_accounts = set()
    monkeypatch.setattr(support.account_manager, "_account_manager", manager)
    original_state = support.settings.ACCOUNT_STATE_FILE
    for mode in ["current", "race"]:
        with support.isolated_account_state(
            manager, [account], tmp_path / f"{mode}.json"
        ) as trial:
            assert trial.sessions_dir.stat().st_mode & 0o777 == 0o700
            assert trial.accounts[0].session_file.stat().st_mode & 0o777 == 0o600
            assert trial.accounts[0].last_used is None
            assert trial.accounts[0].session_file.read_text() == "original"
            trial.accounts[0].last_used = datetime.now()
            trial.accounts[0].session_file.write_text("changed")
            trial._save_state()
            assert support.account_manager.get_account_manager() is trial
    assert account.last_used is None
    assert session.read_text() == "original"
    assert support.settings.ACCOUNT_STATE_FILE == original_state
    assert support.account_manager.get_account_manager() is manager


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
async def test_only_an_admitted_race_can_be_reported_successful(monkeypatch, admitted):
    sentinel = SimpleNamespace()

    async def first_success(candidates, **kwargs):
        return sentinel

    async def prepare(*args):
        if admitted:
            return await support.race.first_success({"direct": None, "local": None})
        return sentinel

    monkeypatch.setattr(support.race, "first_success", first_success)
    monkeypatch.setattr(support.race, "prepare_instagram_delivery", prepare)
    report = {}
    if admitted:
        assert (
            await support.prepare_verified_race(None, None, None, None, None, report)
            is sentinel
        )
    else:
        with pytest.raises(RuntimeError, match="sequential"):
            await support.prepare_verified_race(None, None, None, None, None, report)
    assert report["race_admitted"] is admitted
    assert support.race.first_success is first_success
