"""QR-code generation for Mobile Deck Studio pairing.

Renders the Studio URL (plus its bearer token) as a QR code so a phone can
scan it instead of typing a Tailscale/LAN address and token by hand. Pure
Python (``segno``) — no Pillow, no external API — so it stays fully offline.

``segno`` is imported **lazily inside each function** (not at module top
level) so that importing this module never fails when the ``[web]`` extra
that provides ``segno`` is not installed: the QR surface degrades gracefully
(``is_available()`` returns ``False``, ``print_terminal`` is a no-op) instead
of making ``clm serve`` unimportable.

Lifted, with light adaptation, from the closed ``clm edit`` prototype
(PR #394) per the design's §9.3 reuse note — it is exactly the §3.2 pairing
helper and a reimplementation would buy nothing.

Surfaces:
- :func:`svg_data_uri` — for embedding in a web page (``<img src=…>``).
- :func:`print_terminal` — a desktop-console block rendering, shown by
  ``clm serve`` next to the Studio URL.
- :func:`is_available` — whether ``segno`` is importable.
"""

from __future__ import annotations

from typing import TextIO

#: Raised when segno is not installed; caught by callers to degrade gracefully.
SEGNO_MISSING_MSG = "segno is not installed (needs the [web] extra)"


def _require_segno():
    """Import and return segno, raising a clear error if it is absent."""
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - exercised via the route guard
        raise ImportError(SEGNO_MISSING_MSG) from exc
    return segno


def is_available() -> bool:
    """Return True iff segno is importable (i.e. the ``[web]`` extra is installed)."""
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
    ``clm serve`` so the QR code appears in the desktop console. No-op-safe:
    any render error (including segno being absent) is swallowed so a QR
    glitch never blocks the server.
    """
    try:
        segno = _require_segno()
        segno.make(url, error="m").terminal(out=file, compact=True)
    except Exception:  # pragma: no cover - defensive; terminal quirks across envs
        pass
