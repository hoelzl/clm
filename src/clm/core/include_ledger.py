"""Per-topic ledger of include materializations.

Written by ``clm course sync-includes`` to ``<topic-dir>/.clm-include``. Two
consumers read it:

1. ``clm course sync-includes --remove``: only paths listed in the ledger are
   deleted, so untracked user files in the topic dir are never touched.
2. ``Topic.apply_includes`` at build time: a real on-disk file that
   matches a ledger entry (same ``as_path`` *and* same ``source``) is the
   include's authorized materialization, not an ad-hoc override — so the
   shadow warning is suppressed for it.

The schema is intentionally tiny and additive. Unknown keys are ignored
on read; older readers tolerate newer writers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

LEDGER_NAME = ".clm-include"
LEDGER_VERSION = 1


@dataclass
class LedgerEntry:
    """One materialized include recorded in a topic's ledger."""

    as_path: str
    source: str
    mode: str

    def to_dict(self) -> dict:
        return {"as_path": self.as_path, "source": self.source, "mode": self.mode}

    @classmethod
    def from_dict(cls, data: dict) -> LedgerEntry:
        return cls(
            as_path=str(data["as_path"]),
            source=str(data.get("source", "")),
            mode=str(data.get("mode", "copy")),
        )


@dataclass
class Ledger:
    """Per-topic record of materializations made by ``clm course sync-includes``."""

    entries: list[LedgerEntry] = field(default_factory=list)

    def upsert(self, entry: LedgerEntry) -> None:
        for i, existing in enumerate(self.entries):
            if existing.as_path == entry.as_path:
                self.entries[i] = entry
                return
        self.entries.append(entry)

    def to_dict(self) -> dict:
        return {
            "version": LEDGER_VERSION,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def load(cls, path: Path) -> Ledger:
        if not path.is_file():
            return cls()
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Ignoring unreadable ledger '%s': %s", path, e)
            return cls()
        raw_entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(raw_entries, list):
            return cls()
        entries: list[LedgerEntry] = []
        for raw in raw_entries:
            if isinstance(raw, dict) and "as_path" in raw:
                entries.append(LedgerEntry.from_dict(raw))
        return cls(entries=entries)

    def authorizes(self, *, as_path: str, source_root: Path, course_root: Path) -> bool:
        """Whether the ledger lists this include's materialization.

        Comparison uses resolved absolute paths so that platform-style
        and relative-vs-absolute differences in the recorded ``source``
        do not produce false negatives.
        """
        target = source_root.resolve()
        for entry in self.entries:
            if entry.as_path != as_path:
                continue
            try:
                entry_resolved = (course_root / entry.source).resolve()
            except OSError:
                continue
            if entry_resolved == target:
                return True
        return False
