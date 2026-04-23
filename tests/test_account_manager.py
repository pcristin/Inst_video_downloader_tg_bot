import json
from datetime import timedelta
from pathlib import Path

from src.instagram_video_bot.utils import account_manager as account_manager_module
from src.instagram_video_bot.utils.account_manager import AccountManager


def _write_accounts(path, *usernames):
    path.write_text("\n".join(f"{username}|password|totp" for username in usernames) + "\n")


def test_get_account_manager_ignores_directory_placeholder(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.txt").mkdir()
    (tmp_path / "accounts_state.json").mkdir()
    monkeypatch.setattr(account_manager_module, "_account_manager", None)

    manager = account_manager_module.get_account_manager()

    assert manager is None


def test_account_failure_counter_quarantines_after_threshold(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 0)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    account = manager.accounts[0]

    first_event = manager.record_account_failure(account, "login_failed")

    assert first_event.consecutive_failures == 1
    assert first_event.threshold_reached is False
    assert account.is_banned is False
    assert len(manager.get_available_accounts()) == 2

    second_event = manager.record_account_failure(account, "login_failed")

    assert second_event.consecutive_failures == 2
    assert second_event.threshold_reached is True
    assert account.is_banned is True
    assert account.ban_reason == "sequential_failures:login_failed"
    assert len(manager.get_available_accounts()) == 1

    state = json.loads(state_file.read_text())
    saved_account = next(acc for acc in state["accounts"] if acc["username"] == "first")
    assert saved_account["consecutive_failures"] == 2
    assert saved_account["last_failure_reason"] == "login_failed"
    assert saved_account["last_failure_at"]


def test_account_success_resets_failure_counter(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 0)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    account = manager.accounts[0]
    manager.record_account_failure(account, "timeout")

    manager.record_account_success(account)

    assert account.consecutive_failures == 0
    assert account.last_failure_reason is None
    assert account.last_failure_at is None
    assert account.is_banned is False
    state = json.loads(state_file.read_text())
    saved_account = state["accounts"][0]
    assert saved_account["consecutive_failures"] == 0
    assert saved_account["last_failure_reason"] is None
    assert saved_account["last_failure_at"] is None


def test_below_threshold_failure_does_not_alert_when_pool_is_low(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 2)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    manager.accounts[1].is_banned = True

    event = manager.record_account_failure(manager.accounts[0], "timeout")

    assert event.threshold_reached is False
    assert event.available_accounts == 1
    assert event.should_alert_owner is False
    assert manager._last_low_pool_alert_at is None


def test_low_pool_alert_respects_cooldown(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 2)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_ALERT_COOLDOWN_SECONDS", 3600)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    first_event = manager.record_account_failure(manager.accounts[0], "challenge_required")
    second_event = manager.record_account_failure(manager.accounts[1], "challenge_required")

    assert first_event.should_alert_owner is True
    assert second_event.should_alert_owner is False

    manager._last_low_pool_alert_at = manager._last_low_pool_alert_at - timedelta(seconds=3601)

    assert manager.should_alert_low_pool() is True


def test_repeated_failure_after_quarantine_does_not_alert_again(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 2)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_ALERT_COOLDOWN_SECONDS", 3600)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    first_event = manager.record_account_failure(manager.accounts[0], "challenge_required")
    manager._last_low_pool_alert_at = manager._last_low_pool_alert_at - timedelta(seconds=3601)
    repeated_event = manager.record_account_failure(manager.accounts[0], "challenge_required")

    assert first_event.threshold_reached is True
    assert first_event.should_alert_owner is True
    assert repeated_event.consecutive_failures == 2
    assert repeated_event.threshold_reached is False
    assert repeated_event.should_alert_owner is False
