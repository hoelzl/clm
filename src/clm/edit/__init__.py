"""Mobile-friendly deck editor for CLM slide files.

A self-contained optional module (install with ``[edit]``) that serves an
HTMX-based web UI for editing percent-format ``.py`` slide files from a
browser — primarily a phone on the same LAN as the machine running
``clm edit``.

The module is split into:

- :mod:`clm.edit.deck_file` — the pure library core. A :class:`DeckFile`
  wraps one slide file as a list of :class:`~clm.slides.raw_cells.RawCell`
  objects and exposes index-keyed edit operations (replace / insert /
  delete / move / header rewrite) that round-trip byte-identically for
  untouched cells. Has no web dependencies — fully unit-testable.
- :mod:`clm.edit.app` / :mod:`clm.edit.routes` — the FastAPI + HTMX shell.
- ``clm edit`` (see :mod:`clm.cli.commands.edit`) — the launcher command.
"""

from __future__ import annotations

from clm.edit.deck_file import CellInfo, DeckFile, DeckFileError

__all__ = ["CellInfo", "DeckFile", "DeckFileError"]
