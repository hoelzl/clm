"""QR-code generation for the deck editor.

Renders the editor URL as a QR code so a phone can scan it instead of
typing a LAN address. Pure-Python (``segno``) — no Pillow, no external
API — so it stays fully offline.

``segno`` is imported **lazily inside each function** (not at module top
level) because :mod:`clm.edit.routes` imports this module, and
:mod:`clm.edit.app` imports ``routes`` — so a top-level ``import segno``
would make the whole editor unimportable when the ``[edit]`` extra (which
provides segno) is not installed. Laziness lets the editor load and the QR
route degrade gracefully (HTTP 503) instead.

Surfaces:
- :func:`svg_data_uri` — for embedding in a web page (``<img src=…>``).
- :func:`print_terminal` — a desktop-console block rendering, shown by
  ``clm edit`` next to the URL.
- :func:`best_url` — picks the URL worth encoding (LAN IP when exposed).
"""

from __future__ import annotations

from typing import TextIO

#: Raised when segno is not installed; caught by callers to degrade gracefully.
SEGNO_MISSING_MSG = "segno is not installed (needs the [edit] extra)"


def _require_segno():
    """Import and return segno, raising a clear error if it is absent."""
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - exercised via the routes guard
        raise ImportError(SEGNO_MISSING_MSG) from exc
    return segno


def is_available() -> bool:
    """Return True iff segno is importable (i.e. the ``[edit]`` extra is installed)."""
    try:
        import segno  # noqa: F401
    except ImportError:
        return False
    return True


def svg_data_uri(url: str, *, scale: int = 6) -> str:
    """Return ``url`` as an inline-SVG ``data:`` URI for an ``<img>`` tag.

    ``scale`` is pixels-per-module; 6 renders comfortably on a phone camera.
    """
    segno = _require_segno()
    qr = segno.make(url, error="m")
    return str(qr.svg_data_uri(scale=scale, svgns=False, omitsize=True))


def print_terminal(url: str, *, file: TextIO | None = None) -> None:
    """Print a scannable block-QR rendering of ``url`` to ``file`` (default stdout).

    ``segno``'s terminal writer emits Unicode half-blocks directly; used by
    ``clm edit`` so the QR code appears in the desktop console. No-op-safe:
    any render error is swallowed so a QR glitch never blocks the server.
    """
    try:
        segno = _require_segno()
        segno.make(url, error="m").terminal(out=file, compact=True)
    except Exception:  # pragma: no cover - defensive; terminal quirks across envs
        pass


def best_url(host: str, port: int, *, lan_ip: str | None = None) -> str:
    """Pick the URL worth encoding: a LAN IP when exposed, else the bind host.

    When bound to ``0.0.0.0`` the caller resolves the machine's LAN IP and
    passes it here; otherwise we encode the explicit bind host (typically
    ``127.0.0.1``, useful only when previewing on the same machine).
    """
    display = lan_ip if (host in ("0.0.0.0", "::") and lan_ip) else host
    return f"http://{display}:{port}"
