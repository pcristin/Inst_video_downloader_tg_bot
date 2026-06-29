"""Export logged-in instagrapi sessions as fast Instagram auth contexts."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

COOKIE_ORDER = ("mid", "csrftoken", "ds_user_id", "sessionid")


@dataclass(frozen=True)
class InstagramAuthExportSummary:
    """Summary of an auth context export run."""

    attempted: int
    exported: int
    failed: int
    skipped: int
    output_path: Path


def session_settings_to_cookie_header(settings: Mapping[str, Any]) -> str | None:
    """Build a Cookie header from safe session values in instagrapi settings."""
    cookie_values: dict[str, str] = {}
    for name in COOKIE_ORDER:
        value = _find_cookie_value(settings, name)
        if value is not None:
            cookie_values[name] = value

    if "sessionid" not in cookie_values:
        return None

    return "; ".join(
        f"{name}={cookie_values[name]}"
        for name in COOKIE_ORDER
        if name in cookie_values
    )


def export_instagram_auth_file(
    accounts: Iterable[Any],
    output_path: Path,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> InstagramAuthExportSummary:
    """Log into configured accounts and write a Cobalt-compatible auth JSON file."""
    if client_factory is None:
        from ..services.instagram_client import InstagramClient

        client_factory = InstagramClient

    attempted = 0
    exported = 0
    failed = 0
    skipped = 0
    cookie_headers: list[str] = []
    seen_cookie_headers: set[str] = set()

    for account in accounts:
        username = str(getattr(account, "username", "") or "").strip()
        password = str(getattr(account, "password", "") or "").strip()
        totp_secret = str(getattr(account, "totp_secret", "") or "").strip()
        if not username or not password or not totp_secret:
            skipped += 1
            continue

        attempted += 1
        try:
            client = client_factory(
                username=username,
                password=password,
                session_file=getattr(account, "session_file", None),
                proxy=getattr(account, "proxy", None),
                totp_secret=totp_secret,
            )
            logged_in = bool(client.login())
        except Exception as exc:
            failed += 1
            logger.warning(
                "Instagram auth export failed for account %s during login (%s)",
                username,
                type(exc).__name__,
            )
            continue

        if not logged_in:
            failed += 1
            logger.warning(
                "Instagram auth export login failed for account %s", username
            )
            continue

        try:
            settings = client.client.get_settings()
        except Exception as exc:
            failed += 1
            logger.warning(
                "Instagram auth export could not read session for account %s (%s)",
                username,
                type(exc).__name__,
            )
            continue

        if not isinstance(settings, Mapping):
            failed += 1
            logger.warning(
                "Instagram auth export got invalid session for account %s", username
            )
            continue

        cookie_header = session_settings_to_cookie_header(settings)
        if cookie_header is None:
            failed += 1
            logger.warning(
                "Instagram auth export found no usable session for account %s", username
            )
            continue

        if cookie_header not in seen_cookie_headers:
            seen_cookie_headers.add(cookie_header)
            cookie_headers.append(cookie_header)
            exported += 1

    payload = {
        "instagram": cookie_headers,
        "instagram_bearer": _read_existing_bearers(output_path),
    }
    _write_auth_payload(output_path, payload)
    return InstagramAuthExportSummary(
        attempted=attempted,
        exported=exported,
        failed=failed,
        skipped=skipped,
        output_path=output_path,
    )


def _find_cookie_value(settings: Mapping[str, Any], name: str) -> str | None:
    sources: list[Mapping[str, Any]] = []
    cookies = settings.get("cookies")
    if isinstance(cookies, Mapping):
        sources.append(cookies)
    authorization_data = settings.get("authorization_data")
    if isinstance(authorization_data, Mapping):
        sources.append(authorization_data)
    sources.append(settings)

    for source in sources:
        raw_value = source.get(name)
        if isinstance(raw_value, str) and _is_safe_cookie_value(raw_value):
            return raw_value.strip()
    return None


def _is_safe_cookie_value(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    return not any(char in stripped for char in (";", "\r", "\n", "\x00"))


def _read_existing_bearers(path: Path) -> list[str]:
    if not path.exists() or path.is_dir():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, Mapping):
        return []
    bearers = payload.get("instagram_bearer")
    if not isinstance(bearers, list):
        return []
    return [
        bearer.strip()
        for bearer in bearers
        if isinstance(bearer, str) and _is_safe_bearer_value(bearer)
    ]


def _is_safe_bearer_value(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    return not any(char in stripped for char in ("\r", "\n", "\x00"))


def _write_auth_payload(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(path)
    os.chmod(path, 0o600)
