from pathlib import Path

from src.instagram_video_bot.utils import account_manager as account_manager_module


def test_get_account_manager_ignores_directory_placeholder(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.txt").mkdir()
    (tmp_path / "accounts_state.json").mkdir()
    monkeypatch.setattr(account_manager_module, "_account_manager", None)

    manager = account_manager_module.get_account_manager()

    assert manager is None
