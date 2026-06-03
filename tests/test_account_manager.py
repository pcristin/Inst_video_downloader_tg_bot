import json
import logging
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


def test_load_accounts_redacts_proxy_credentials_in_logs(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        account_manager_module.settings,
        "PROXIES",
        "proxy-user:proxy-pass@proxy.example:1234",
    )
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first")

    with caplog.at_level(logging.INFO):
        AccountManager(accounts_file=accounts_file, state_file=state_file)

    assert "proxy-user:proxy-pass" not in caplog.text
    assert "http://***@proxy.example:1234" in caplog.text


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


def test_hard_account_failure_quarantines_immediately(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 0)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    account = manager.accounts[0]

    event = manager.record_account_failure(account, "manual_verification")

    assert event.consecutive_failures == 1
    assert event.threshold == 1
    assert event.threshold_reached is True
    assert account.is_banned is True
    assert account.ban_reason == "replacement_required:manual_verification"
    assert [acc.username for acc in manager.get_available_accounts()] == ["second"]

    state = json.loads(state_file.read_text())
    saved_account = next(acc for acc in state["accounts"] if acc["username"] == "first")
    assert saved_account["is_banned"] is True
    assert saved_account["ban_reason"] == "replacement_required:manual_verification"


def test_rate_limit_failure_quarantines_temporarily(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 0)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    account = manager.accounts[0]

    event = manager.record_account_failure(account, "rate_limited")

    assert event.consecutive_failures == 1
    assert event.threshold == 1
    assert event.threshold_reached is True
    assert account.is_banned is True
    assert account.ban_reason == "hard_failure:rate_limited"
    assert [acc.username for acc in manager.get_available_accounts()] == ["second"]

    account.banned_at = account_manager_module.datetime.now() - timedelta(hours=7)
    manager.reset_old_banned_accounts(hours=6)

    assert account.is_banned is False
    assert account.ban_reason is None
    assert account.consecutive_failures == 0
    assert [acc.username for acc in manager.get_available_accounts()] == ["first", "second"]


def test_hard_account_failure_quarantines_after_prior_soft_failure(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 0)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    account = manager.accounts[0]

    soft_event = manager.record_account_failure(account, "timeout")

    assert soft_event.threshold_reached is False
    assert account.is_banned is False

    hard_event = manager.record_account_failure(account, "manual_verification")

    assert hard_event.consecutive_failures == 2
    assert hard_event.threshold == 1
    assert hard_event.threshold_reached is True
    assert account.is_banned is True
    assert account.ban_reason == "replacement_required:manual_verification"
    assert [acc.username for acc in manager.get_available_accounts()] == ["second"]


def test_failure_after_hard_quarantine_preserves_original_ban(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(account_manager_module.settings, "ACCOUNT_LOW_WATERMARK", 0)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    account = manager.accounts[0]

    hard_event = manager.record_account_failure(account, "manual_verification")
    repeated_event = manager.record_account_failure(account, "timeout")

    assert hard_event.threshold_reached is True
    assert repeated_event.consecutive_failures == 2
    assert repeated_event.threshold_reached is False
    assert account.is_banned is True
    assert account.ban_reason == "replacement_required:manual_verification"
    assert [acc.username for acc in manager.get_available_accounts()] == ["second"]


def test_old_replacement_required_accounts_are_not_reset(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    replace_account = manager.accounts[0]
    temporary_account = manager.accounts[1]
    old_ban_time = account_manager_module.datetime.now() - timedelta(hours=7)
    for account, reason in (
        (replace_account, "replacement_required:manual_verification"),
        (temporary_account, "provider_timeout_stale"),
    ):
        account.is_banned = True
        account.ban_reason = reason
        account.banned_at = old_ban_time
        account.consecutive_failures = 1
        account.last_failure_reason = reason.split(":", 1)[-1]
        account.last_failure_at = old_ban_time
    manager._save_state()

    manager.reset_old_banned_accounts(hours=6)

    assert replace_account.is_banned is True
    assert replace_account.ban_reason == "replacement_required:manual_verification"
    assert replace_account.consecutive_failures == 1
    assert temporary_account.is_banned is False
    assert temporary_account.ban_reason is None
    assert [acc.username for acc in manager.get_available_accounts()] == ["second"]


def test_legacy_hard_failure_reset_only_for_temporary_reasons(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "manual", "limited")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    manual_account = manager.accounts[0]
    limited_account = manager.accounts[1]
    old_ban_time = account_manager_module.datetime.now() - timedelta(hours=7)
    for account, reason in (
        (manual_account, "hard_failure:manual_verification"),
        (limited_account, "hard_failure:rate_limited"),
    ):
        account.is_banned = True
        account.ban_reason = reason
        account.banned_at = old_ban_time
        account.consecutive_failures = 1
        account.last_failure_reason = reason.split(":", 1)[-1]
        account.last_failure_at = old_ban_time
    manager._save_state()

    manager.reset_old_banned_accounts(hours=6)

    assert manual_account.is_banned is True
    assert manual_account.ban_reason == "hard_failure:manual_verification"
    assert limited_account.is_banned is False
    assert limited_account.ban_reason is None
    assert [acc.username for acc in manager.get_available_accounts()] == ["limited"]


def test_raw_setup_failure_reset_only_for_temporary_reasons(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "challenge", "auth", "limited")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)
    challenge_account = manager.accounts[0]
    auth_account = manager.accounts[1]
    limited_account = manager.accounts[2]
    old_ban_time = account_manager_module.datetime.now() - timedelta(hours=7)
    for account, reason in (
        (challenge_account, "challenge_required"),
        (auth_account, "auth_failed"),
        (limited_account, "rate_limited"),
    ):
        account.is_banned = True
        account.ban_reason = reason
        account.banned_at = old_ban_time
        account.consecutive_failures = 1
        account.last_failure_reason = reason
        account.last_failure_at = old_ban_time
    manager._save_state()

    manager.reset_old_banned_accounts(hours=6)

    assert challenge_account.is_banned is True
    assert challenge_account.ban_reason == "challenge_required"
    assert auth_account.is_banned is True
    assert auth_account.ban_reason == "auth_failed"
    assert limited_account.is_banned is False
    assert limited_account.ban_reason is None
    assert [acc.username for acc in manager.get_available_accounts()] == ["limited"]


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


def test_acquire_account_excludes_usernames(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    account = manager.acquire_account(excluded_usernames={"first"})

    assert account is not None
    assert account.username == "second"
    assert manager._leased_accounts == {"second"}


def test_leasable_account_count_excludes_current_leases(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    leased = manager.acquire_account()

    assert leased is not None
    assert manager.get_leasable_account_count() == 1

    manager.release_account(leased)

    assert manager.get_leasable_account_count() == 2


def test_eligible_account_count_includes_current_leases(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    accounts_file = tmp_path / "accounts.txt"
    state_file = tmp_path / "accounts_state.json"
    _write_accounts(accounts_file, "first", "second")
    manager = AccountManager(accounts_file=accounts_file, state_file=state_file)

    leased = manager.acquire_account()

    assert leased is not None
    assert manager.get_eligible_account_count() == 2
    assert manager.get_eligible_account_count(excluded_usernames={leased.username}) == 1


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
