"""Mobile Deck Studio — phone-friendly deck authoring served by ``clm serve``.

A second view on the existing ``clm serve`` FastAPI app (alongside the jobs
Monitor) that lets a deck be browsed and edited from a phone over Tailscale.
The phone is a thin authoring surface; the desktop does the heavy lifting
(parse, byte-exact write-back, change watching).

Design of record: ``docs/claude/design/mobile-deck-studio.md``. This package
implements **P0** (read-only browse + search) and **P1** (cell editing with
the optimistic-concurrency keystone + external-change watcher). Structural
ops (P2), bilingual lock/sync (P3) and build-preview/PWA (P4) are not yet
implemented.

Per the design's §9.4 steering note the P0/P1 frontend is intentionally a
lightweight vanilla-JS surface; the no-build Preact + CodeMirror PWA is
deferred to P2+ where in-cell editing ergonomics start to pay off.
"""

from clm.web.studio.service import StudioService

__all__ = ["StudioService"]
