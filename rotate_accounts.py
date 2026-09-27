#!/usr/bin/env python3
"""Validate, prewarm, and activate an Instagram account roster on the host."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pyotp

sys.path.insert(0, str(Path(__file__).parent / "src"))
from instagram_video_bot.config.settings import settings

USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
AUTH_EMPTY = {"instagram": [], "instagram_bearer": []}


@dataclass(frozen=True)
class Candidate:
    username: str
    password: str
    totp_secret: str

    def account_line(self) -> str:
        return f"{self.username}|{self.password}|{self.totp_secret}\n"


def read_candidates(path: Path) -> list[Candidate]:
    """Validate the entire input before any login or file mutation."""
    candidates = []
    seen = set()
    for line_number, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        separator = "\t" if "\t" in raw else "|"
        parts = raw.split(separator)
        if len(parts) != 3:
            raise ValueError(f"line {line_number}: expected exactly three fields")
        username, password, seed = (part.strip() for part in parts)
        seed = re.sub(r"\s+", "", seed).upper()
        if not USERNAME.fullmatch(username) or username in seen:
            raise ValueError(f"line {line_number}: invalid or duplicate username")
        if not password or "|" in password or "\n" in password:
            raise ValueError(f"line {line_number}: invalid password field")
        if not seed or not re.fullmatch(r"[A-Z2-7]+", seed):
            raise ValueError(f"line {line_number}: invalid TOTP seed")
        try:
            pyotp.TOTP(seed).now()
        except (ValueError, TypeError) as error:
            raise ValueError(f"line {line_number}: invalid TOTP seed") from error
        candidates.append(Candidate(username, password, seed))
        seen.add(username)
    if not candidates:
        raise ValueError("candidate file has no accounts")
    return candidates


def fingerprint(candidates: list[Candidate]) -> str:
    content = "".join(account.account_line() for account in candidates)
    return hashlib.sha256(content.encode()).hexdigest()


def roster_diff(current_path: Path, candidates: list[Candidate]) -> dict[str, int]:
    """Count roster changes without displaying or persisting credentials."""
    if not current_path.is_file():
        return {"added": len(candidates), "changed": 0, "removed": 0}
    current = {account.username: account for account in read_candidates(current_path)}
    proposed = {account.username: account for account in candidates}
    return {
        "added": len(proposed.keys() - current.keys()),
        "changed": sum(
            proposed[name] != current[name] for name in proposed.keys() & current.keys()
        ),
        "removed": len(current.keys() - proposed.keys()),
    }


def _private_write(
    path: Path, data: bytes, owner: tuple[int, int] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        if owner is not None:
            os.chown(temporary, *owner)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _snapshot(path: Path) -> tuple[bytes, tuple[int, int], int] | None:
    if not path.exists():
        return None
    info = path.stat()
    return path.read_bytes(), (info.st_uid, info.st_gid), info.st_mode & 0o777


def _restore(path: Path, snapshot: tuple[bytes, tuple[int, int], int] | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        content, owner, mode = snapshot
        _private_write(path, content, owner)
        os.chmod(path, mode)


def _session_is_usable(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        session = json.loads(path.read_text())
        cookies = session.get("cookies", {})
        return bool(cookies.get("sessionid") and cookies.get("ds_user_id"))
    except (ValueError, OSError, AttributeError):
        return False


def _stage_for(root: Path, candidates: list[Candidate]) -> Path:
    return root / fingerprint(candidates)[:20]


def _result_path(stage: Path) -> Path:
    return stage / "results.json"


def _write_results(
    stage: Path, candidates: list[Candidate], results: dict[str, str]
) -> None:
    payload = {"fingerprint": fingerprint(candidates), "results": results}
    _private_write(
        _result_path(stage), (json.dumps(payload, sort_keys=True) + "\n").encode()
    )


def _load_results(stage: Path, candidates: list[Candidate]) -> dict[str, str]:
    if not _result_path(stage).exists():
        return {}
    payload = json.loads(_result_path(stage).read_text())
    if payload.get("fingerprint") != fingerprint(candidates):
        raise ValueError("staged result does not match candidate file")
    return payload.get("results", {})


def login_worker(path: Path, index: int, stage: Path) -> int:
    """Run one CAA login in an isolated process with redacted output."""
    from instagrapi import Client

    candidate = read_candidates(path)[index]
    proxies = settings.get_proxy_list()
    client = Client()
    if proxies:
        client.set_proxy(proxies[index % len(proxies)])
    try:
        success = client.login(
            candidate.username,
            candidate.password,
            verification_code=pyotp.TOTP(candidate.totp_secret).now(),
        )
        if success:
            identity = client.account_info()
            if identity.username.casefold() != candidate.username.casefold():
                print(json.dumps({"result": "identity_mismatch"}))
                return 1
            target = stage / "sessions" / f"{candidate.username}.json"
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            client.dump_settings(target)
            os.chmod(target, 0o600)
            if _session_is_usable(target):
                print(json.dumps({"result": "success"}))
                return 0
    except Exception as error:
        print(json.dumps({"result": type(error).__name__}))
        return 1
    print(json.dumps({"result": "login_failed"}))
    return 1


def verify_worker(path: Path, index: int, stage: Path) -> int:
    """Check saved session authority and identity against Instagram."""
    from instagrapi import Client

    candidate = read_candidates(path)[index]
    session_file = stage / "sessions" / f"{candidate.username}.json"
    if not _session_is_usable(session_file):
        print(json.dumps({"result": "session_missing"}))
        return 1
    proxies = settings.get_proxy_list()
    client = Client()
    if proxies:
        client.set_proxy(proxies[index % len(proxies)])
    try:
        client.load_settings(session_file)
        identity = client.account_info()
        cookies = client.get_settings().get("cookies", {})
        if identity.username.casefold() == candidate.username.casefold() and str(
            identity.pk
        ) == str(cookies.get("ds_user_id")):
            print(json.dumps({"result": "success"}))
            return 0
        print(json.dumps({"result": "identity_mismatch"}))
    except Exception as error:
        print(json.dumps({"result": type(error).__name__}))
    return 1


def _verify_session(path: Path, index: int, stage: Path, timeout: int = 45) -> bool:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "verify",
                "--candidates",
                str(path),
                "--index",
                str(index),
                "--stage-root",
                str(stage),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        return completed.returncode == 0 and payload.get("result") == "success"
    except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
        return False


def prewarm(
    path: Path, root: Path, seed_sessions: Path | None = None, timeout: int = 100
) -> dict[str, int]:
    candidates = read_candidates(path)
    diff = roster_diff(Path("accounts.txt"), candidates)
    stage = _stage_for(root, candidates)
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(stage, 0o700)
    results = _load_results(stage, candidates)
    for index, candidate in enumerate(candidates):
        key = str(index)
        session = stage / "sessions" / f"{candidate.username}.json"
        if (
            results.get(key) == "success"
            and _session_is_usable(session)
            and _verify_session(path, index, stage)
        ):
            continue
        if seed_sessions is not None:
            seeded = seed_sessions / f"{candidate.username}.json"
            if _session_is_usable(seeded):
                session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(seeded, session)
                os.chmod(session, 0o600)
                if _verify_session(path, index, stage):
                    results[key] = "success"
                    _write_results(stage, candidates, results)
                    continue
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "worker",
                    "--candidates",
                    str(path),
                    "--index",
                    str(index),
                    "--stage-root",
                    str(stage),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            category = payload.get("result", "worker_failed")
            results[key] = (
                "success"
                if completed.returncode == 0 and _session_is_usable(session)
                else category
            )
        except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
            results[key] = "worker_timeout_or_error"
        _write_results(stage, candidates, results)
        print(f"account {index + 1}/{len(candidates)}: {results[key]}", flush=True)
    return {
        "total": len(candidates),
        "success": sum(value == "success" for value in results.values()),
        "failed": sum(value != "success" for value in results.values()),
        **diff,
    }


def activate(path: Path, root: Path, project: Path) -> dict[str, int]:
    """Install a fully attempted roster while the bot is stopped."""
    candidates = read_candidates(path)
    stage = _stage_for(root, candidates)
    results = _load_results(stage, candidates)
    if len(results) != len(candidates) or any(
        str(i) not in results for i in range(len(candidates))
    ):
        raise ValueError("every candidate must have a prewarm result")
    successful = [
        candidate
        for index, candidate in enumerate(candidates)
        if results[str(index)] == "success"
    ]
    if not successful:
        raise ValueError("no successful sessions; refusing activation")
    for index, candidate in enumerate(candidates):
        if results[str(index)] == "success" and (
            not _session_is_usable(stage / "sessions" / f"{candidate.username}.json")
            or not _verify_session(path, index, stage)
        ):
            raise ValueError("staged session could not authenticate as candidate")

    sessions = project / "sessions"
    sessions.mkdir(exist_ok=True)
    state_file = project / "account-state" / "accounts_state.json"
    auth_file = project / "secrets" / "instagram_auth.json"
    current_roster = project / "accounts.txt"
    owner_info = current_roster.stat()
    owner = (owner_info.st_uid, owner_info.st_gid)
    roster = "".join(candidate.account_line() for candidate in candidates).encode()
    state = {
        "accounts": [
            {
                "username": candidate.username,
                "is_banned": results[str(index)] != "success",
                "ban_reason": (
                    None if results[str(index)] == "success" else "prewarm_failed"
                ),
            }
            for index, candidate in enumerate(candidates)
        ]
    }
    files = {path: _snapshot(path) for path in (current_roster, state_file, auth_file)}
    previous_sessions = {path: _snapshot(path) for path in sessions.glob("*.json")}
    try:
        # Populate sessions before exposing the new roster. The bot is stopped by Make.
        for candidate in successful:
            target = sessions / f"{candidate.username}.json"
            _private_write(
                target, (stage / "sessions" / target.name).read_bytes(), owner
            )
        _private_write(
            state_file, (json.dumps(state, sort_keys=True) + "\n").encode(), owner
        )
        _private_write(auth_file, (json.dumps(AUTH_EMPTY) + "\n").encode(), owner)
        _private_write(current_roster, roster, owner)
        active = {candidate.username for candidate in successful}
        for old in sessions.glob("*.json"):
            if old.stem not in active:
                old.unlink()
    except OSError:
        for current in sessions.glob("*.json"):
            if current not in previous_sessions:
                current.unlink()
        for old, snapshot in previous_sessions.items():
            _restore(old, snapshot)
        for original, snapshot in files.items():
            _restore(original, snapshot)
        raise
    return {
        "total": len(candidates),
        "success": len(successful),
        "failed": len(candidates) - len(successful),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prewarm", "activate", "worker", "verify"])
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, default=Path(".account-rotation"))
    parser.add_argument("--seed-sessions", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--index", type=int)
    args = parser.parse_args()
    try:
        if args.command == "worker":
            if args.index is None:
                raise ValueError("worker requires --index")
            raise SystemExit(login_worker(args.candidates, args.index, args.stage_root))
        if args.command == "verify":
            if args.index is None:
                raise ValueError("verify requires --index")
            raise SystemExit(
                verify_worker(args.candidates, args.index, args.stage_root)
            )
        if args.command == "prewarm":
            summary = prewarm(args.candidates, args.stage_root, args.seed_sessions)
        else:
            summary = activate(args.candidates, args.stage_root, args.project)
        print(json.dumps(summary, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            f"account rotation failed: {type(error).__name__}: {error}", file=sys.stderr
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
