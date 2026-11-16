"""Adopt already-published content into a release channel (issue #1140).

``release sync`` assumes it is the first writer into a cohort destination: a
released-but-unfrozen topic is *copied from the current build*. A course that
used to publish by pushing an output target's repo directly already has
students working in that content, so the first sync would overwrite what they
have with today's build — exactly the change a migration to per-topic freezing
exists to avoid.

Adoption is the bookkeeping-only alternative: for each selected topic it
compares the destination against the current build's (language-restricted)
provenance manifest, file by file, and freezes the topic **as the destination
has it**:

* ``same`` — the destination bytes equal the build's ``content_hash``;
* ``differs`` — present, but the bytes differ (the student keeps theirs;
  the topic is flagged ``differs_from_build``);
* ``missing`` — not in the destination. By default such a topic is refused
  (:data:`REFUSE_MISSING`): missing files usually mean a renumbered deck, and
  delivering silently would leave both names in the students' repo. With
  *deliver_missing* only the missing files are copied — never overwriting —
  and the topic is then frozen.

The ``topic_digest`` of an adopted record is computed from the
**destination's** bytes in the same format :func:`topic_digest_from_files`
produces for a sync, so it equals the build digest exactly when nothing
differs. Apart from ``--deliver-missing`` nothing here writes to the
destination except the caller saving the frozen manifest.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from attrs import frozen

from clm.core.provenance_manifest import (
    hash_file,
    manifest_files_by_topic,
    topic_digest_from_files,
)
from clm.release.frozen_manifest import FrozenManifest, FrozenRecord
from clm.release.sync import is_vcs_path

logger = logging.getLogger(__name__)

# Per-file classifications.
SAME = "same"
DIFFERS = "differs"
MISSING = "missing"

# Per-topic actions.
ADOPT = "adopt"
REFUSE_MISSING = "refuse-missing"
SKIP_FROZEN = "skip-frozen"
# The build has no files for the topic (not built, or built empty): there is
# nothing to compare against, so the topic is not adopted.
SKIP_UNBUILT = "skip-unbuilt"
# The source build errored for the topic (manifest ``failed_topics``, #295):
# its file list is suspect, so it is not used to decide what is published.
SKIP_FAILED = "skip-failed"

# Prefix for an adopted record's ``source_commit``: the commit is the
# *published* repo's HEAD, not a course-source commit (D7).
PUBLISHED_PREFIX = "published:"


@frozen
class FileClass:
    path: str
    status: str


@frozen
class AdoptTopicPlan:
    topic_id: str
    action: str
    files: tuple[FileClass, ...] = ()

    def _paths(self, status: str) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.status == status)

    @property
    def same(self) -> tuple[str, ...]:
        return self._paths(SAME)

    @property
    def differs(self) -> tuple[str, ...]:
        return self._paths(DIFFERS)

    @property
    def missing(self) -> tuple[str, ...]:
        return self._paths(MISSING)

    @property
    def to_deliver(self) -> tuple[str, ...]:
        """Missing files an ``adopt`` delivers (only under --deliver-missing)."""
        return self.missing if self.action == ADOPT else ()


@frozen
class AdoptPlan:
    topics: tuple[AdoptTopicPlan, ...]
    # Skeleton (topic-less) files the destination lacks. Adoption freezes the
    # skeleton without copying, so these reach students only through
    # --refreeze-skeleton or <evergreen> (D4); reported, never delivered.
    skeleton_missing: tuple[str, ...] = ()
    skeleton_already_frozen: bool = False

    @property
    def adopted(self) -> tuple[AdoptTopicPlan, ...]:
        return tuple(t for t in self.topics if t.action == ADOPT)


@frozen
class AdoptResult:
    adopted_topics: tuple[str, ...]
    differs_topics: tuple[str, ...]
    delivered_files: tuple[str, ...]
    skeleton_frozen_now: bool


def classify_files(entries: Iterable[dict[str, Any]], dest_root: Path) -> tuple[FileClass, ...]:
    """Classify manifest *entries* against the destination tree.

    An entry without a ``content_hash`` cannot be proven equal, so a present
    file counts as ``differs``. VCS metadata paths are ignored (issue #302):
    for a topic polluted with such entries the adopted digest therefore
    covers only the real files and differs from the manifest's digest.
    """
    out: list[FileClass] = []
    for entry in entries:
        rel = entry.get("path", "")
        if is_vcs_path(rel):
            continue
        dst = dest_root / rel
        if not dst.is_file():
            status = MISSING
        elif entry.get("content_hash") and hash_file(dst) == entry["content_hash"]:
            status = SAME
        else:
            status = DIFFERS
        out.append(FileClass(rel, status))
    return tuple(out)


def plan_adopt(
    *,
    manifest: dict[str, Any],
    topic_ids: Iterable[str],
    frozen: FrozenManifest,
    dest_root: Path,
    deliver_missing: bool = False,
) -> AdoptPlan:
    """Plan the adoption of *topic_ids* into *dest_root*. Reads, never writes."""
    by_topic = manifest_files_by_topic(manifest)
    failed = set(manifest.get("failed_topics", []))
    plans: list[AdoptTopicPlan] = []
    for topic_id in topic_ids:
        if frozen.is_frozen(topic_id):
            plans.append(AdoptTopicPlan(topic_id, SKIP_FROZEN))
            continue
        if topic_id in failed:
            plans.append(AdoptTopicPlan(topic_id, SKIP_FAILED))
            continue
        files = classify_files(by_topic.get(topic_id, []), dest_root)
        if not files:
            plans.append(AdoptTopicPlan(topic_id, SKIP_UNBUILT))
            continue
        has_missing = any(f.status == MISSING for f in files)
        action = REFUSE_MISSING if has_missing and not deliver_missing else ADOPT
        plans.append(AdoptTopicPlan(topic_id, action, files))
    skeleton_missing = tuple(
        f.path for f in classify_files(by_topic.get(None, []), dest_root) if f.status == MISSING
    )
    return AdoptPlan(
        topics=tuple(plans),
        skeleton_missing=skeleton_missing,
        skeleton_already_frozen=frozen.skeleton_frozen,
    )


def topics_present(manifest: dict[str, Any], dest_root: Path) -> list[str]:
    """Topic ids with at least one built file present in *dest_root*.

    The ``--all-present`` selection: a topic the destination holds none of
    was never published and belongs to the normal ``add`` + ``sync`` flow; a
    *partly* present one is selected so the plan surfaces it (typically
    ``refuse-missing`` after a renumber). Manifest order.
    """
    present: list[str] = []
    for topic_id, entries in manifest_files_by_topic(manifest).items():
        if topic_id is None:
            continue
        if any(
            (dest_root / e.get("path", "")).is_file()
            for e in entries
            if not is_vcs_path(e.get("path", ""))
        ):
            present.append(topic_id)
    return present


def apply_adopt(
    *,
    plan: AdoptPlan,
    manifest: dict[str, Any],
    source_root: Path,
    dest_root: Path,
    frozen: FrozenManifest,
    source_commit: str,
    copied_at: str,
) -> AdoptResult:
    """Record *plan*'s adoptions into *frozen* (mutated; the caller saves it).

    The only destination writes are the missing files of an ``adopt`` topic
    planned under ``--deliver-missing``; each is re-checked immediately
    before copying so an existing file is never overwritten. The digest is
    computed from the destination's bytes after any delivery.
    """
    by_path = {e.get("path"): e for e in manifest.get("files", [])}
    adopted: list[str] = []
    differs: list[str] = []
    delivered: list[str] = []
    for topic_plan in plan.adopted:
        for rel in topic_plan.to_deliver:
            dst = dest_root / rel
            src = source_root / rel
            if dst.exists():
                logger.warning("release adopt: %s appeared since planning; not overwritten", dst)
                continue
            if rel not in by_path or not src.is_file():
                logger.warning("release adopt: build file missing, not delivered: %s", src)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            delivered.append(rel)
        present = [f.path for f in topic_plan.files if (dest_root / f.path).is_file()]
        if len(present) != len(topic_plan.files):
            logger.warning(
                "release adopt: topic %r still has missing files; not freezing",
                topic_plan.topic_id,
            )
            continue
        digest = topic_digest_from_files(
            [{"content_hash": hash_file(dest_root / rel)} for rel in present]
        )
        differs_from_build = bool(topic_plan.differs)
        frozen.freeze(
            topic_plan.topic_id,
            FrozenRecord(
                source_commit=source_commit,
                copied_at=copied_at,
                topic_digest=digest,
                differs_from_build=differs_from_build,
            ),
        )
        adopted.append(topic_plan.topic_id)
        if differs_from_build:
            differs.append(topic_plan.topic_id)
    skeleton_frozen_now = not frozen.skeleton_frozen
    frozen.skeleton_frozen = True
    return AdoptResult(
        adopted_topics=tuple(adopted),
        differs_topics=tuple(differs),
        delivered_files=tuple(delivered),
        skeleton_frozen_now=skeleton_frozen_now,
    )
