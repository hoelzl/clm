"""The release sync/promote algorithm.

Reconciles release *intent* (a :class:`~clm.release.ledger.Ledger`) into release
*fact* (a :class:`~clm.release.frozen_manifest.FrozenManifest` in the cohort's
destination), driven by the frozen source's ``.clm-manifest.json`` provenance
index. The rules (issue #208):

* a released topic **not yet frozen** -> copy its files (by manifest) and record
  the freeze;
* a released topic **already frozen** -> skip (students keep what they were
  given) unless it is in *refreeze*;
* the skeleton (global, topic-less files) is copied once at channel init, then
  frozen.

Promotion copies bytes verbatim from the source tree; it never rebuilds or
re-executes anything, and — because the manifest lists only topic output files —
it never copies ``.clm-*`` build sidecars into the destination.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from attrs import frozen

from clm.core.provenance_manifest import manifest_files_by_topic, topic_digest_from_files
from clm.release.frozen_manifest import FrozenManifest, FrozenRecord

logger = logging.getLogger(__name__)

# Topic plan actions.
COPY = "copy"
REFREEZE = "refreeze"
SKIP_FROZEN = "skip-frozen"


@frozen
class TopicPlan:
    topic_id: str
    action: str
    file_count: int


@frozen
class SyncPlan:
    copy_skeleton: bool
    skeleton_file_count: int
    topics: tuple[TopicPlan, ...]

    @property
    def to_copy(self) -> tuple[TopicPlan, ...]:
        return tuple(t for t in self.topics if t.action in (COPY, REFREEZE))

    @property
    def skipped(self) -> tuple[TopicPlan, ...]:
        return tuple(t for t in self.topics if t.action == SKIP_FROZEN)


@frozen
class SyncResult:
    copied_topics: tuple[str, ...]
    refrozen_topics: tuple[str, ...]
    skipped_topics: tuple[str, ...]
    skeleton_copied: bool
    files_copied: int


def plan_sync(
    *,
    manifest: dict[str, Any],
    ledger_released: Iterable[str],
    frozen: FrozenManifest,
    refreeze: Iterable[str] = (),
) -> SyncPlan:
    """Compute what a sync would do, without touching the filesystem."""
    refreeze_set = set(refreeze)
    by_topic = manifest_files_by_topic(manifest)
    plans: list[TopicPlan] = []
    for topic_id in ledger_released:
        file_count = len(by_topic.get(topic_id, []))
        if frozen.is_frozen(topic_id) and topic_id not in refreeze_set:
            action = SKIP_FROZEN
        elif frozen.is_frozen(topic_id):
            action = REFREEZE
        else:
            action = COPY
        plans.append(TopicPlan(topic_id, action, file_count))
    skeleton_files = by_topic.get(None, [])
    return SyncPlan(
        copy_skeleton=not frozen.skeleton_frozen,
        skeleton_file_count=len(skeleton_files),
        topics=tuple(plans),
    )


def apply_sync(
    *,
    plan: SyncPlan,
    manifest: dict[str, Any],
    source_root: Path,
    dest_root: Path,
    frozen: FrozenManifest,
    copied_at: str,
) -> SyncResult:
    """Execute *plan*, copying files and recording freezes into *frozen*.

    Mutates *frozen* in place (the caller persists it afterward). A topic with
    no files in the source manifest (e.g. released but not yet built) is logged
    and **not** frozen, so it is retried once it is built.
    """
    by_topic = manifest_files_by_topic(manifest)
    source_commit = manifest.get("source_commit")
    files_copied = 0
    copied: list[str] = []
    refrozen: list[str] = []
    skipped: list[str] = []

    if plan.copy_skeleton:
        files_copied += _copy_files(by_topic.get(None, []), source_root, dest_root)
        frozen.skeleton_frozen = True

    for topic_plan in plan.topics:
        if topic_plan.action == SKIP_FROZEN:
            skipped.append(topic_plan.topic_id)
            continue
        files = by_topic.get(topic_plan.topic_id, [])
        if not files:
            logger.warning(
                "release sync: topic %r has no files in the source manifest; "
                "not freezing (will retry once it is built)",
                topic_plan.topic_id,
            )
            continue
        files_copied += _copy_files(files, source_root, dest_root)
        frozen.freeze(
            topic_plan.topic_id,
            FrozenRecord(
                source_commit=source_commit,
                copied_at=copied_at,
                topic_digest=topic_digest_from_files(files),
            ),
        )
        if topic_plan.action == REFREEZE:
            refrozen.append(topic_plan.topic_id)
        else:
            copied.append(topic_plan.topic_id)

    return SyncResult(
        copied_topics=tuple(copied),
        refrozen_topics=tuple(refrozen),
        skipped_topics=tuple(skipped),
        skeleton_copied=plan.copy_skeleton,
        files_copied=files_copied,
    )


def _copy_files(files: list[dict[str, Any]], source_root: Path, dest_root: Path) -> int:
    copied = 0
    for entry in files:
        rel = entry["path"]
        src = source_root / rel
        if not src.is_file():
            logger.warning("release sync: source file missing, skipping: %s", src)
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied
