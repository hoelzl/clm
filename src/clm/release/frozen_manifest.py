"""Frozen manifest: the per-destination record of what has been released.

Written into the cohort's own repository, this is the **freeze boundary**: a
topic recorded here is never re-propagated by a later sync (so students keep
exactly what they were given), unless an explicit ``--refreeze`` overrides it.
Each record is per-topic — ``source_commit`` (the build the cohort received),
``copied_at``, and a single rolled-up ``topic_digest`` for tamper/drift
detection — NOT a per-file hash map, keeping the student-shipped artifact
small (issue #208, D7).

The file is **per release stream** (issue #325): a named stream writes
``.clm-released.<stream>.json`` so several streams (e.g. materials and
solutions) can release into the *same* destination repository without
colliding on freeze records — topic ids are shared across streams, so a
single file would make stream A's release freeze the topic for stream B. The
unnamed single-block layout (issue #208) keeps the legacy
``.clm-released.json`` name. :func:`load_frozen_manifest` adopts a legacy
file whose ``channel`` field matches, so existing deployments migrate on
their next sync.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from attrs import Factory, asdict, define, frozen

logger = logging.getLogger(__name__)

FROZEN_FILENAME = ".clm-released.json"
FROZEN_VERSION = 1


def frozen_manifest_filename(stream: str) -> str:
    """The frozen manifest's filename for a release *stream* (issue #325).

    A named stream owns ``.clm-released.<stream>.json``; the unnamed
    single-block layout keeps the legacy ``.clm-released.json``.
    """
    return f".clm-released.{stream}.json" if stream else FROZEN_FILENAME


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


@frozen
class LoadedFrozenManifest:
    """A frozen manifest resolved to its per-stream location (issue #325).

    ``path`` is where a subsequent :meth:`FrozenManifest.save` belongs (always
    the per-stream filename). ``adopted_legacy`` names a legacy
    ``.clm-released.json`` whose records were adopted — the caller deletes it
    after a successful save so the destination carries one file per stream.
    ``ignored_legacy_channel`` is set when a legacy file exists but records a
    *different* channel (normal in a shared destination: it belongs to the
    stream that has not migrated yet) so callers can surface a note.
    """

    manifest: FrozenManifest
    path: Path
    adopted_legacy: Path | None = None
    ignored_legacy_channel: str | None = None


def load_frozen_manifest(dest_root: Path, *, stream: str, channel: str) -> LoadedFrozenManifest:
    """Load *channel*'s frozen manifest from *dest_root* (issue #325).

    Reads the per-stream file (:func:`frozen_manifest_filename`). When a named
    stream's file does not exist yet but the legacy ``.clm-released.json``
    does **and** its ``channel`` field equals *channel*, the legacy records
    are adopted — the pre-#325 deployment migrates to the per-stream name on
    its next save. A legacy file recording a different channel is left alone
    (it belongs to another stream sharing this destination).
    """
    path = dest_root / frozen_manifest_filename(stream)
    if not stream or path.exists():
        return LoadedFrozenManifest(FrozenManifest.load(path, channel=channel), path)
    legacy = dest_root / FROZEN_FILENAME
    if legacy.exists():
        legacy_channel = json.loads(legacy.read_text(encoding="utf-8")).get("channel", "")
        if legacy_channel == channel:
            logger.info("adopting legacy frozen manifest %s for channel %r", legacy, channel)
            return LoadedFrozenManifest(
                FrozenManifest.load(legacy, channel=channel), path, adopted_legacy=legacy
            )
        return LoadedFrozenManifest(
            FrozenManifest(channel=channel), path, ignored_legacy_channel=legacy_channel
        )
    return LoadedFrozenManifest(FrozenManifest(channel=channel), path)
