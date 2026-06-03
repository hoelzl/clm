"""Frozen manifest: the per-destination record of what has been released.

Written into the cohort's own repository as ``.clm-released.json``, this is the
**freeze boundary**: a topic recorded here is never re-propagated by a later
sync (so students keep exactly what they were given), unless an explicit
``--refreeze`` overrides it. Each record is per-topic — ``source_commit`` (the
build the cohort received), ``copied_at``, and a single rolled-up
``topic_digest`` for tamper/drift detection — NOT a per-file hash map, keeping
the student-shipped artifact small (issue #208, D7).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from attrs import Factory, asdict, define, frozen

logger = logging.getLogger(__name__)

FROZEN_FILENAME = ".clm-released.json"
FROZEN_VERSION = 1


@frozen
class FrozenRecord:
    source_commit: str | None
    copied_at: str
    topic_digest: str


@define
class FrozenManifest:
    """Realized release state for one channel (lives in the destination repo)."""

    channel: str
    frozen: dict[str, FrozenRecord] = Factory(dict)
    skeleton_frozen: bool = False

    @classmethod
    def load(cls, path: Path, *, channel: str) -> FrozenManifest:
        """Load the frozen manifest; a missing file yields an empty one.

        *channel* is used when the file is absent or omits the field, so a
        freshly initialized destination still names its cohort.
        """
        if not path.exists():
            return cls(channel=channel)
        data = json.loads(path.read_text(encoding="utf-8"))
        frozen = {
            topic_id: FrozenRecord(
                source_commit=rec.get("source_commit"),
                copied_at=rec.get("copied_at", ""),
                topic_digest=rec.get("topic_digest", ""),
            )
            for topic_id, rec in data.get("frozen", {}).items()
        }
        return cls(
            channel=data.get("channel", channel),
            frozen=frozen,
            skeleton_frozen=bool(data.get("skeleton_frozen", False)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": FROZEN_VERSION,
            "channel": self.channel,
            "skeleton_frozen": self.skeleton_frozen,
            # Sorted so the published file is deterministic and diffs cleanly.
            "frozen": {topic_id: asdict(self.frozen[topic_id]) for topic_id in sorted(self.frozen)},
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def is_frozen(self, topic_id: str) -> bool:
        return topic_id in self.frozen

    def freeze(self, topic_id: str, record: FrozenRecord) -> None:
        self.frozen[topic_id] = record
