"""Release ledger: the volatile, per-channel list of released topic ids.

The ledger is the single source of *release intent* for one cohort. It is a
plain-text file (one topic id per line; ``#`` comments and blank lines ignored)
deliberately chosen over a structured format so that:

* it is trivially hand-editable by the course author,
* every release is a one-line addition -> minimal, readable git diffs,
* it carries no new dependency.

It lives in the **course source repo** (never in ``course.xml``) and is
*cumulative*: it lists every topic released so far, which is what makes the
sync sweep-safe. A future scheduled-release layer (issue #208 D5) can extend
the line format (e.g. ``topic_id @ YYYY-MM-DD``) without changing this file's
role.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from attrs import Factory, define

logger = logging.getLogger(__name__)

_HEADER = (
    "# CLM release ledger. One released topic id per line.\n"
    "# Lines starting with '#' and blank lines are ignored. Cumulative:\n"
    "# list every topic released to this cohort so far. Edit by hand or via\n"
    "# `clm release add` / `clm release week`.\n"
)


@define
class Ledger:
    """The cumulative, ordered set of released topic ids for one channel."""

    released: list[str] = Factory(list)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        """Load a ledger; a missing file yields an empty ledger.

        Duplicate ids are collapsed (first occurrence wins) so a hand-edited
        file with accidental repeats still behaves cumulatively.
        """
        if not path.exists():
            return cls()
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line not in seen:
                seen.add(line)
                ordered.append(line)
        return cls(ordered)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(self.released)
        path.write_text(_HEADER + body + ("\n" if body else ""), encoding="utf-8")

    @property
    def released_set(self) -> frozenset[str]:
        return frozenset(self.released)

    def add(self, topic_ids: Iterable[str]) -> list[str]:
        """Append the given topic ids that are not already present.

        Returns the ids that were actually added (in input order), so callers
        can report "already released" no-ops. Validation against the spec's
        real topic ids is the caller's responsibility (see
        :func:`partition_known`).
        """
        existing = set(self.released)
        added: list[str] = []
        for tid in topic_ids:
            if tid not in existing:
                existing.add(tid)
                self.released.append(tid)
                added.append(tid)
        return added


def partition_known(
    topic_ids: Iterable[str], valid_ids: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Split *topic_ids* into ``(known, unknown)`` against *valid_ids*.

    Order is preserved within each group. Used by the CLI to reject typos
    before they reach the ledger.
    """
    valid = set(valid_ids)
    known: list[str] = []
    unknown: list[str] = []
    for tid in topic_ids:
        (known if tid in valid else unknown).append(tid)
    return known, unknown
