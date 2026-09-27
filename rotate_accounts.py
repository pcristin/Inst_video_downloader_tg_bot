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
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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
        if not USERNAME.fullmatch(username) or username.casefold() in seen:
            raise ValueError(f"line {line_number}: invalid or duplicate username")
        if not password or "|" in password or "\n" in password:
            raise ValueError(f"line {line_number}: invalid password field")
        if seed and not re.fullmatch(r"[A-Z2-7]+", seed):
            raise ValueError(f"line {line_number}: invalid TOTP seed")
        if seed:
            try:
                pyotp.TOTP(seed).now()
            except (ValueError, TypeError) as error:
                raise ValueError(f"line {line_number}: invalid TOTP seed") from error
        candidates.append(Candidate(username, password, seed))
        seen.add(username.casefold())
    if not candidates:
        raise ValueError("candidate file has no accounts")
    return candidates


def fingerprint(candidates: list[Candidate]) -> str:
    content = "".join(account.account_line() for account in candidates)
    return hashlib.sha256(content.encode()).hexdigest()


def assert_private_candidate_file(path: Path) -> None:
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("candidate file must be owned by the operator and mode 0600")


def account_proxy(index: int) -> str | None:
    proxies = settings.get_proxy_list()
    if proxies:
        return proxies[index % len(proxies)]
    return settings.get_single_proxy()


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
    _write_stage_manifest(_result_path(stage), candidates, results)


def _write_stage_manifest(
    path: Path, candidates: list[Candidate], results: dict
) -> None:
    payload = {"fingerprint": fingerprint(candidates), "results": results}
    _private_write(path, (json.dumps(payload, sort_keys=True) + "\n").encode())


def _load_results(stage: Path, candidates: list[Candidate]) -> dict[str, str]:
    return _load_stage_manifest(_result_path(stage), candidates, "result")


def _load_stage_manifest(path: Path, candidates: list[Candidate], label: str) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text())
    if payload.get("fingerprint") != fingerprint(candidates):
        raise ValueError(f"staged {label} does not match candidate file")
    return payload.get("results", {})


def _session_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canary_path(stage: Path) -> Path:
    return stage / "canary.json"


def _write_canary_results(
    stage: Path, candidates: list[Candidate], results: dict[str, dict[str, str]]
) -> None:
    _write_stage_manifest(_canary_path(stage), candidates, results)


def _load_canary_results(
    stage: Path, candidates: list[Candidate]
) -> dict[str, dict[str, str]]:
    return _load_stage_manifest(_canary_path(stage), candidates, "canary")


def _identity_matches(client, candidate: Candidate) -> bool:
    identity = client.account_info()
    cookies = client.get_settings().get("cookies", {})
    return identity.username.casefold() == candidate.username.casefold() and str(
        identity.pk
    ) == str(cookies.get("ds_user_id"))


def _canary_passed(
    stage: Path, candidate: Candidate, index: int, canaries: dict
) -> bool:
    session = stage / "sessions" / f"{candidate.username}.json"
    result = canaries.get(str(index), {})
    return (
        isinstance(result, dict)
        and _session_is_usable(session)
        and result.get("result") == "success"
        and result.get("session_sha256") == _session_hash(session)
    )


def _validate_canary_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"instagram.com", "www.instagram.com"}
        or not re.fullmatch(r"/(?:share/)?(?:p|reel|tv)/[A-Za-z0-9_-]+/?", parsed.path)
    ):
        raise ValueError("canary URL must be a public Instagram post or reel URL")


def login_worker(path: Path, index: int, stage: Path) -> int:
    """Run one CAA login in an isolated process with redacted output."""
    from instagrapi import Client

    candidate = read_candidates(path)[index]
    if not candidate.totp_secret:
        print(json.dumps({"result": "missing_totp"}))
        return 1
    client = Client()
    proxy = account_proxy(index)
    if proxy:
        client.set_proxy(proxy)
    try:
        success = client.login(
            candidate.username,
            candidate.password,
            verification_code=pyotp.TOTP(candidate.totp_secret).now(),
        )
        if success:
            if not _identity_matches(client, candidate):
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
    client = Client()
    proxy = account_proxy(index)
    if proxy:
        client.set_proxy(proxy)
    try:
        client.load_settings(session_file)
        if _identity_matches(client, candidate):
            print(json.dumps({"result": "success"}))
            return 0
        print(json.dumps({"result": "identity_mismatch"}))
    except Exception as error:
        print(json.dumps({"result": type(error).__name__}))
    return 1


def canary_worker(path: Path, index: int, stage: Path, url: str) -> int:
    """Download one public media item using a saved, identity-checked session."""
    from instagrapi import Client

    candidate = read_candidates(path)[index]
    session = stage / "sessions" / f"{candidate.username}.json"
    if not _session_is_usable(session):
        print(json.dumps({"result": "session_missing"}))
        return 1
    client = Client()
    proxy = account_proxy(index)
    if proxy:
        client.set_proxy(proxy)
    try:
        client.load_settings(session)
        if not _identity_matches(client, candidate):
            print(json.dumps({"result": "identity_mismatch"}))
            return 1
        media_pk = client.media_pk_from_url(url)
        media = client.media_info(media_pk)
        with tempfile.TemporaryDirectory() as folder:
            if media.media_type == 1:
                outputs = [client.photo_download(media_pk, folder=Path(folder))]
            elif media.media_type == 2:
                outputs = [client.video_download(media_pk, folder=Path(folder))]
            elif media.media_type == 8:
                outputs = client.album_download(media_pk, folder=Path(folder))
            else:
                print(json.dumps({"result": "unsupported_media_type"}))
                return 1
            if any(
                Path(output).is_file() and Path(output).stat().st_size > 0
                for output in outputs
            ):
                print(json.dumps({"result": "success"}))
                return 0
        print(json.dumps({"result": "empty_download"}))
    except Exception as error:
        print(json.dumps({"result": type(error).__name__}))
    return 1


def canary(path: Path, root: Path, url: str, timeout: int = 120) -> dict[str, int]:
    _validate_canary_url(url)
    candidates = read_candidates(path)
    stage = _stage_for(root, candidates)
    results = _load_results(stage, candidates)
    if len(results) != len(candidates):
        raise ValueError("every candidate must have a prewarm result")
    canaries = _load_canary_results(stage, candidates)
    for index, candidate in enumerate(candidates):
        if results.get(str(index)) != "success":
            continue
        if _canary_passed(stage, candidate, index, canaries):
            continue
        session = stage / "sessions" / f"{candidate.username}.json"
        session_hash_before = (
            _session_hash(session) if _session_is_usable(session) else None
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "canary-worker",
                    "--candidates",
                    str(path),
                    "--index",
                    str(index),
                    "--stage-root",
                    str(stage),
                    "--canary-url",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            result = payload.get("result", "worker_failed")
            if completed.returncode != 0 and result == "success":
                result = "worker_failed"
        except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
            result = "worker_timeout_or_error"
        session_hash_after = (
            _session_hash(session) if _session_is_usable(session) else None
        )
        if result == "success" and session_hash_before != session_hash_after:
            result = "session_changed"
        canaries[str(index)] = {
            "result": result,
            "session_sha256": session_hash_after or "",
        }
        _write_canary_results(stage, candidates, canaries)
        print(f"account {index + 1}/{len(candidates)}: {result}", flush=True)
    return {
        "total": len(candidates),
        "passed": sum(
            results.get(str(index)) == "success"
            and _canary_passed(stage, candidate, index, canaries)
            for index, candidate in enumerate(candidates)
        ),
        "pending": sum(
            results.get(str(index)) == "success"
            and not _canary_passed(stage, candidate, index, canaries)
            for index, candidate in enumerate(candidates)
        ),
    }


def _verify_session(path: Path, index: int, stage: Path, timeout: int = 45) -> str:
    result = "worker_timeout_or_error"
    for _ in range(2):
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
            result = payload.get("result", "worker_failed")
            if completed.returncode == 0 and result == "success":
                return "success"
            if result in {"identity_mismatch", "session_missing", "LoginRequired"}:
                return result
        except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
            result = "worker_timeout_or_error"
    return result


def prewarm(
    path: Path,
    root: Path,
    seed_sessions: Path | None = None,
    timeout: int = 100,
    project: Path = Path(__file__).resolve().parent,
) -> dict[str, int]:
    candidates = read_candidates(path)
    diff = roster_diff(project / "accounts.txt", candidates)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    stage = _stage_for(root, candidates)
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(stage, 0o700)
    results = _load_results(stage, candidates)
    for index, candidate in enumerate(candidates):
        key = str(index)
        session = stage / "sessions" / f"{candidate.username}.json"
        if not candidate.totp_secret:
            results[key] = "missing_totp"
            session.unlink(missing_ok=True)
            _write_results(stage, candidates, results)
            continue
        staged_success = results.get(key) == "success" and _session_is_usable(session)
        if staged_success:
            verification = _verify_session(path, index, stage)
            if verification == "success":
                continue
            if verification not in {
                "identity_mismatch",
                "session_missing",
                "LoginRequired",
            }:
                print(
                    f"account {index + 1}/{len(candidates)}: verification_deferred",
                    flush=True,
                )
                continue
            staged_success = False
        if seed_sessions is not None and not staged_success:
            seeded = seed_sessions / f"{candidate.username}.json"
            if _session_is_usable(seeded):
                session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(seeded, session)
                os.chmod(session, 0o600)
                if _verify_session(path, index, stage) == "success":
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
    canaries = _load_canary_results(stage, candidates)
    for index, candidate in enumerate(candidates):
        if results[str(index)] == "success" and not _canary_passed(
            stage, candidate, index, canaries
        ):
            raise ValueError(
                f"staged media canary missing or stale for {candidate.username}"
            )
    verified_sessions: dict[str, bytes] = {}
    for index, candidate in enumerate(candidates):
        if results[str(index)] != "success":
            continue
        session = stage / "sessions" / f"{candidate.username}.json"
        verification = (
            _verify_session(path, index, stage)
            if _session_is_usable(session)
            else "session_missing"
        )
        if verification != "success":
            raise ValueError(
                f"staged session for {candidate.username} failed verification ({verification})"
            )
        session_bytes = session.read_bytes()
        if (
            hashlib.sha256(session_bytes).hexdigest()
            != canaries[str(index)]["session_sha256"]
        ):
            raise ValueError(f"staged media canary is stale for {candidate.username}")
        verified_sessions[candidate.username] = session_bytes

    sessions = project / "sessions"
    sessions.mkdir(exist_ok=True, mode=0o700)
    state_file = project / "account-state" / "accounts_state.json"
    auth_file = project / "secrets" / "instagram_auth.json"
    current_roster = project / "accounts.txt"
    owner_info = current_roster.stat()
    owner = (owner_info.st_uid, owner_info.st_gid)
    roster = "".join(candidate.account_line() for candidate in candidates).encode()
    previous_roster = {
        account.username: account for account in read_candidates(current_roster)
    }
    previous_state = {}
    if state_file.is_file():
        previous_state = {
            account["username"]: account
            for account in json.loads(state_file.read_text()).get("accounts", [])
        }
    activated_now = datetime.now().isoformat()

    def activation_time(candidate: Candidate) -> str | None:
        if previous_roster.get(candidate.username) != candidate:
            return activated_now
        prior = previous_state.get(candidate.username, {})
        if prior.get("is_banned"):
            return activated_now
        return prior.get("activated_at")

    state = {
        "accounts": [
            {
                "username": candidate.username,
                "is_banned": results[str(index)] != "success",
                "activated_at": (
                    activation_time(candidate)
                    if results[str(index)] == "success"
                    else None
                ),
                "last_used": (
                    previous_state.get(candidate.username, {}).get("last_used")
                    if previous_roster.get(candidate.username) == candidate
                    and not previous_state.get(candidate.username, {}).get("is_banned")
                    else None
                ),
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
            _private_write(target, verified_sessions[candidate.username], owner)
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
    parser.add_argument(
        "command",
        choices=["prewarm", "canary", "activate", "worker", "verify", "canary-worker"],
    )
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, default=Path(".account-rotation"))
    parser.add_argument("--seed-sessions", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--index", type=int)
    parser.add_argument("--canary-url")
    args = parser.parse_args()
    try:
        assert_private_candidate_file(args.candidates)
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
        if args.command == "canary-worker":
            if args.index is None or not args.canary_url:
                raise ValueError("canary-worker requires --index and --canary-url")
            _validate_canary_url(args.canary_url)
            raise SystemExit(
                canary_worker(
                    args.candidates, args.index, args.stage_root, args.canary_url
                )
            )
        if args.command == "prewarm":
            summary = prewarm(
                args.candidates,
                args.stage_root,
                args.seed_sessions,
                project=args.project,
            )
        elif args.command == "canary":
            if not args.canary_url:
                raise ValueError("canary requires --canary-url")
            summary = canary(args.candidates, args.stage_root, args.canary_url)
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
