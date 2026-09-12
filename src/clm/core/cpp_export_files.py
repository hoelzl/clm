"""File-set conventions of the per-deck C++ code export (issue #928, phase 3).

A C++ deck's ``format="code"`` output is no longer one translation unit:
next to ``<deck>.cpp`` the export may write a header and one file per
workshop. The emitter (worker side), the provenance manifest and the CMake
project generator (host side) all need the same naming rule, and the
worker/CLI boundary carries the companion files by name, so the rule lives
here — in ``clm.core``, below every consumer.

Layout next to a deck output ``<dir>/<stem>.cpp``::

    <dir>/<stem>.hpp               hoisted includes + ``global`` cells; only
                                   when the deck has workshops or ``global``
                                   cells (the lecture file includes it)
    <dir>/<stem>_workshop_<N>.cpp  one per workshop range with code, ``N``
                                   being the range's 1-based ordinal in the
                                   deck; includes the header
"""

from __future__ import annotations

import re
from pathlib import Path

HEADER_SUFFIX = ".hpp"
WORKSHOP_INFIX = "_workshop_"
_WORKSHOP_RE = re.compile(rf"^(?P<stem>.+){re.escape(WORKSHOP_INFIX)}(?P<n>\d+)\.cpp$")


def header_file_name(stem: str) -> str:
    """The deck header's file name for a deck output named ``<stem>.cpp``."""
    return f"{stem}{HEADER_SUFFIX}"


def workshop_file_name(stem: str, ordinal: int) -> str:
    """The file name of workshop ``ordinal`` (1-based) of deck ``<stem>.cpp``."""
    return f"{stem}{WORKSHOP_INFIX}{ordinal}.cpp"


def workshop_ordinal(file_name: str, stem: str) -> int | None:
    """The workshop ordinal encoded in ``file_name``, if it belongs to ``stem``."""
    m = _WORKSHOP_RE.match(file_name)
    if m is None or m.group("stem") != stem:
        return None
    return int(m.group("n"))


def companion_output_files(main_output: Path) -> list[Path]:
    """The companion files present on disk next to deck output ``main_output``.

    The header first (if present), then the workshop files by ordinal. Only
    files that exist are returned — the enumeration is used after the build
    (manifest, CMake), when the emitted set is whatever the worker wrote.
    """
    stem = main_output.stem
    directory = main_output.parent
    found: list[Path] = []
    header = directory / header_file_name(stem)
    if header.is_file():
        found.append(header)
    workshops: list[tuple[int, Path]] = []
    try:
        candidates = list(directory.iterdir())
    except OSError:
        candidates = []
    for path in candidates:
        ordinal = workshop_ordinal(path.name, stem)
        if ordinal is not None and path.is_file():
            workshops.append((ordinal, path))
    found.extend(path for _, path in sorted(workshops))
    return found
