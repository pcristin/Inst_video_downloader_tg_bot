"""Isolation and admission checks for explicitly authorized live canaries."""

import copy
import shutil
import threading
from contextlib import contextmanager

from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services import instagram_delivery_race as race
from src.instagram_video_bot.utils import account_manager


def eligible_accounts(manager):
    """Apply the same credentials, session and ramp checks as race reservation."""
    return sorted(
        (
            account
            for account in manager.get_available_accounts()
            if account.session_file
            and account.session_file.is_file()
            and account.username not in manager._leased_accounts
            and manager._ramp_allows(account)
        ),
        key=lambda account: account.username,
    )


@contextmanager
def isolated_account_state(manager, accounts, state_file):
    """Give each trial an independent copy of the original account health state.

    Sessions are also copied per trial. Source state must already be private,
    as required by the launch instructions. No trial updates the source manager.
    """
    trial = copy.copy(manager)
    trial.accounts = copy.deepcopy(accounts)
    trial.sessions_dir = state_file.parent / (state_file.stem + "-sessions")
    trial.sessions_dir.mkdir(parents=True, exist_ok=True)
    for index, account in enumerate(trial.accounts):
        copied_session = trial.sessions_dir / f"{index}.json"
        shutil.copyfile(account.session_file, copied_session)
        account.session_file = copied_session
    trial.current_account = None
    trial._leased_accounts = set()
    trial._lock = threading.RLock()
    trial.state_file = state_file
    previous_manager = account_manager._account_manager
    previous_state_file = settings.ACCOUNT_STATE_FILE
    try:
        account_manager._account_manager = trial
        settings.ACCOUNT_STATE_FILE = state_file
        trial._save_state()
        yield trial
    finally:
        account_manager._account_manager = previous_manager
        settings.ACCOUNT_STATE_FILE = previous_state_file


async def prepare_verified_race(downloader, url, output, bot, stager, report):
    """Reject sequential fallback instead of reporting it as a race trial."""
    original = race.first_success
    report["race_admitted"] = False

    async def observed(candidates, **kwargs):
        report["race_admitted"] = True
        return await original(candidates, **kwargs)

    # This harness runs one trial at a time in a dedicated process.
    race.first_success = observed
    try:
        info = await race.prepare_instagram_delivery(
            downloader, url, output, bot, stager
        )
        if not report["race_admitted"]:
            raise RuntimeError("Race trial fell back to sequential acquisition")
        return info
    finally:
        race.first_success = original
