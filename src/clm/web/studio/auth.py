"""Bearer-token pairing for Mobile Deck Studio.

Anyone who can reach the Studio URL (over Tailscale / LAN) must present a
shared bearer token. The token is **persistent** — stored in the user config
dir — so the pairing QR code is stable across server restarts (a phone that
scanned it once keeps working). ``--rotate-token`` cycles it.

The token is the real access gate: ``clm serve`` binds localhost and exposure
is via ``tailscale serve`` / explicit ``--host``, so the network boundary is
the tailnet; the token guards against anyone else on it. One token, full
access — there are no per-user accounts (§3.2 of the design).
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.security.utils import get_authorization_scheme_param

logger = logging.getLogger(__name__)

#: File name under the user config dir holding the persistent Studio token.
_TOKEN_FILENAME = "studio_token"


def _token_path() -> Path:
    """Return the path of the persistent Studio token file.

    Uses ``platformdirs`` so the location is correct per-OS (e.g. ``%APPDATA%``
    on Windows, ``~/.config`` on Linux).
    """
    from platformdirs import user_config_dir

    return Path(user_config_dir("clm")) / _TOKEN_FILENAME


def _generate_token() -> str:
    """Return a fresh URL-safe random token."""
    return secrets.token_urlsafe(24)


def get_or_create_token(*, rotate: bool = False) -> str:
    """Return the persistent Studio token, creating (or rotating) it on disk.

    Args:
        rotate: When True, discard any existing token and write a new one.

    Returns:
        The bearer token (stable across restarts unless rotated).
    """
    path = _token_path()
    if not rotate and path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = _generate_token()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    logger.info("Studio token %s at %s", "rotated" if rotate else "created", path)
    return token


def extract_token(request: Request) -> str | None:
    """Pull the presented token from a request.

    Accepts either an ``Authorization: Bearer <token>`` header (used by the
    PWA for REST/WS calls once paired) or a ``?token=`` query parameter (used
    by the initial QR-code deep link, which the frontend then stores).
    """
    auth = request.headers.get("Authorization")
    if auth:
        scheme, param = get_authorization_scheme_param(auth)
        if scheme.lower() == "bearer" and param:
            return param
    query_token = request.query_params.get("token")
    if query_token:
        return query_token
    return None


def token_matches(request: Request, expected: str) -> bool:
    """Constant-time check that the request presents ``expected``."""
    presented = extract_token(request)
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)
