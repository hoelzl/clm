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
    """Pull the presented token from a request's ``Authorization`` header.

    **Only** the header. A ``?token=`` query parameter used to be accepted as
    well, for the QR-code deep link — but a URL is the worst place to keep a
    credential that never expires: it lands in uvicorn's access log, in any
    proxy's, in browser history, and in the ``Referer`` of anything the page
    links out to (S7 of the 2026-07-24 review).

    Nothing needed it. The deep link targets ``/studio/``, which is a static
    mount with no auth at all — the token in that URL is read by the frontend,
    not by this function — and the PWA has always sent the header on every API
    call. The QR now carries the token in the URL *fragment*, which is never
    transmitted to the server, so the pairing flow leaks nothing.
    """
    auth = request.headers.get("Authorization")
    if auth:
        scheme, param = get_authorization_scheme_param(auth)
        if scheme.lower() == "bearer" and param:
            return param
    return None


def tokens_match(presented: str | None, expected: str) -> bool:
    """Constant-time comparison of a client-supplied token against ``expected``.

    ``secrets.compare_digest`` raises ``TypeError`` when either ``str`` holds a
    non-ASCII character, and the presented value comes straight off the wire —
    Starlette decodes headers as latin-1, so a byte above 0x7F is enough. That
    would turn a bad token into a 500 from inside the auth check. Comparing
    the UTF-8 encodings keeps the comparison constant-time *and* total.
    """
    if not presented:
        return False
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def token_matches(request: Request, expected: str) -> bool:
    """Constant-time check that the request presents ``expected``."""
    return tokens_match(extract_token(request), expected)
