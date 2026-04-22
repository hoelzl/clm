"""Lookup helpers for ``video_to_slide_mapping.json`` style inventories.

Downstream course repositories (e.g. PythonCourses/planning/) maintain
a freshness-annotated inventory that maps recorded video files to the
slide files they are (approximately) aligned with.  The schema is
informal — each entry is a JSON object with at least ``path`` (video
absolute path) and ``matched_slide`` (slide path, usually relative to
the course repo root, occasionally absolute).

This module encapsulates loading and slide→video lookup so the new
``compare-from-inventory`` command and its MCP counterpart don't have
to re-parse the file themselves.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InventoryEntry:
    """One row of the inventory JSON, reduced to fields we care about."""

    video_path: Path
    matched_slide: Path | None
    match_score: float | None
    freshness: str | None
    raw: dict


def load_inventory(path: Path) -> list[InventoryEntry]:
    """Parse the inventory JSON at ``path``.

    Tolerates missing optional fields — only ``path`` is required per
    entry.  Entries with neither ``path`` nor ``video_path`` are
    skipped with a warning.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[InventoryEntry] = []
    for row in data:
        video_raw = row.get("path") or row.get("video_path")
        if not video_raw:
            logger.warning("Inventory entry missing 'path': %r", row)
            continue
        matched_raw = row.get("matched_slide") or row.get("slide_file")
        entries.append(
            InventoryEntry(
                video_path=Path(video_raw),
                matched_slide=Path(matched_raw) if matched_raw else None,
                match_score=row.get("match_score"),
                freshness=row.get("freshness"),
                raw=row,
            )
        )
    return entries


def _normalize_slide_path(p: Path, base: Path) -> Path:
    """Resolve ``p`` against ``base`` when relative, then canonicalize."""
    resolved = p if p.is_absolute() else base / p
    try:
        return resolved.resolve()
    except OSError:
        return resolved


def find_videos_for_slide(
    entries: list[InventoryEntry],
    slide_file: Path,
    *,
    inventory_base: Path,
) -> list[InventoryEntry]:
    """Return every inventory entry whose ``matched_slide`` points to ``slide_file``.

    ``inventory_base`` is used to resolve relative ``matched_slide``
    paths (typically the directory containing the inventory JSON).
    Results preserve inventory order — usually part-1, part-2, … for
    multi-part recordings, which is the order ``sync``/``compare``
    want.
    """
    try:
        target = slide_file.resolve()
    except OSError:
        target = slide_file

    matches: list[InventoryEntry] = []
    for entry in entries:
        if entry.matched_slide is None:
            continue
        candidate = _normalize_slide_path(entry.matched_slide, inventory_base)
        if candidate == target:
            matches.append(entry)
    return matches
