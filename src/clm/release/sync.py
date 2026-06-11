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
  frozen — **presence-as-frozen** (issue #325): a skeleton file already present
  at the destination is kept, not overwritten, so when several release streams
  share one destination repository the later stream's first sync cannot clobber
  the skeleton the earlier stream froze from a different build;
* **evergreen** skeleton files (glob patterns from ``<evergreen>`` /
  ``--evergreen``) are exempt from the skeleton freeze: every sync re-copies a
  matching file whose built content differs from the destination's (e.g. a
  NEWS file). Evergreen is skeleton-only by design — topic content changes
  only via *refreeze*, keeping the per-topic ``topic_digest`` truthful and
  making it impossible for a pattern to leak files of an unreleased topic.

Promotion copies bytes verbatim from the source tree; it never rebuilds or
re-executes anything, and — because the manifest lists only topic output files —
it never copies ``.clm-*`` build sidecars into the destination. As defense in
depth it also refuses any manifest entry under a VCS metadata directory
(``.git``/``.svn``/``.hg``): a polluted manifest from an older build that
walked a stray ``.git`` into the skeleton (issue #302) must never overwrite
the destination repo's own ``.git``.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from attrs import frozen

from clm.core.provenance_manifest import (
    hash_file,
    manifest_files_by_topic,
    topic_digest_from_files,
)
from clm.release.frozen_manifest import FrozenManifest, FrozenRecord

logger = logging.getLogger(__name__)

# Topic plan actions.
COPY = "copy"
REFREEZE = "refreeze"
SKIP_FROZEN = "skip-frozen"
# The source build errored for this topic (recorded in the manifest's
# failed_topics, issue #295): its on-disk output is suspect, so promotion is
# refused until a build succeeds for it. Never frozen, so it is retried.
SKIP_FAILED = "skip-failed"

# Evergreen plan actions.
REFRESH = "refresh"
UP_TO_DATE = "up-to-date"


@frozen
class TopicPlan:
    topic_id: str
    action: str
    file_count: int


@frozen
class EvergreenPlan:
    """One evergreen skeleton file: re-copy (*refresh*) or already current."""

    path: str
    action: str


@frozen
class EvergreenScan:
    """Result of matching evergreen patterns against a source manifest.

    ``topic_owned_matches`` lists paths a pattern matched but that belong to a
    topic — evergreen is skeleton-only, so they are ignored (the caller warns;
    topic content changes only via ``--refreeze``).
    """

    plans: tuple[EvergreenPlan, ...] = ()
    topic_owned_matches: tuple[str, ...] = ()

    @property
    def to_refresh(self) -> tuple[EvergreenPlan, ...]:
        return tuple(p for p in self.plans if p.action == REFRESH)


@frozen
class SkeletonScan:
    """Presence of the manifest's skeleton files at the destination (issue #325).

    Files already ``present`` are kept, not overwritten — presence-as-frozen:
    in a destination shared by several release streams, the skeleton an
    earlier stream froze for its cohort must not be clobbered by a later
    stream's first sync from a possibly newer build. Only ``missing`` files
    are copied (evergreen patterns still refresh present files by content).
    """

    missing: tuple[str, ...] = ()
    present: tuple[str, ...] = ()


def scan_skeleton(*, manifest: dict[str, Any], dest_root: Path) -> SkeletonScan:
    """Classify the manifest's skeleton entries by presence at *dest_root*."""
    missing: list[str] = []
    present: list[str] = []
    for entry in manifest.get("files", []):
        if entry.get("topic_id") is not None:
            continue
        rel = entry.get("path", "")
        if _is_vcs_path(rel):
            continue
        (present if (dest_root / rel).is_file() else missing).append(rel)
    return SkeletonScan(missing=tuple(missing), present=tuple(present))


@frozen
class TopicOverlap:
    """Topic-owned paths claimed by *both* manifests of a shared destination.

    ``conflicting`` paths carry **different** content in the two builds —
    promoting either stream would clobber the other stream's released bytes,
    so the sync preflight refuses them. ``identical`` paths are byte-equal in
    both builds (a topic's *static* files — project scaffolding, data — are
    copied verbatim into every target that builds the topic, so they
    legitimately appear in both streams): copying them is idempotent
    regardless of sync order, so they are allowed and only surfaced as a
    note. Notebook outputs stay disjoint by kind (Code-Along/Partial vs
    Completed), so a notebook path showing up here at all means colliding
    target kinds — its content differs and it lands in ``conflicting``.
    """

    conflicting: tuple[str, ...] = ()
    identical: tuple[str, ...] = ()


def topic_path_overlap(manifest_a: dict[str, Any], manifest_b: dict[str, Any]) -> TopicOverlap:
    """Classify the topic-owned paths claimed by both manifests (issue #325).

    A shared path counts as ``identical`` only when **both** entries carry a
    ``content_hash`` and they match; an absent hash cannot prove anything, so
    it is treated as ``conflicting``. Hash-different paths mean the streams'
    builds disagree about the file's content — typically the two source
    targets were built from different states; rebuild both and re-sync.
    """
    a = {
        e["path"]: e.get("content_hash")
        for e in manifest_a.get("files", [])
        if e.get("topic_id") is not None
    }
    b = {
        e["path"]: e.get("content_hash")
        for e in manifest_b.get("files", [])
        if e.get("topic_id") is not None
    }
    shared = sorted(set(a) & set(b))
    identical = tuple(p for p in shared if a[p] and a[p] == b[p])
    conflicting = tuple(p for p in shared if not (a[p] and a[p] == b[p]))
    return TopicOverlap(conflicting=conflicting, identical=identical)


@frozen
class SyncPlan:
    copy_skeleton: bool
    skeleton_file_count: int
    topics: tuple[TopicPlan, ...]
    evergreen: tuple[EvergreenPlan, ...] = ()
    # Skeleton paths the copy is restricted to (presence-as-frozen, issue
    # #325). None = copy every skeleton file (no scan was taken — the
    # pre-#325 contract, kept for callers that plan without a destination).
    skeleton_to_copy: tuple[str, ...] | None = None
    # Skeleton files kept because the destination already has them.
    skeleton_present_count: int = 0

    @property
    def to_copy(self) -> tuple[TopicPlan, ...]:
        return tuple(t for t in self.topics if t.action in (COPY, REFREEZE))

    @property
    def evergreen_refresh(self) -> tuple[EvergreenPlan, ...]:
        """The REFRESH plans the apply step executes.

        A refresh already satisfied by this sync's own skeleton copy is
        dropped: with a full copy (``skeleton_to_copy is None``) that is every
        evergreen file; with a presence-restricted copy only the missing ones
        — a *present* evergreen file whose content differs still refreshes,
        even on a first sync (issue #325).
        """
        if self.copy_skeleton and self.skeleton_to_copy is None:
            return ()
        covered = frozenset(self.skeleton_to_copy or ()) if self.copy_skeleton else frozenset()
        return tuple(p for p in self.evergreen if p.action == REFRESH and p.path not in covered)

    @property
    def skipped(self) -> tuple[TopicPlan, ...]:
        return tuple(t for t in self.topics if t.action == SKIP_FROZEN)

    @property
    def failed(self) -> tuple[TopicPlan, ...]:
        return tuple(t for t in self.topics if t.action == SKIP_FAILED)


@frozen
class SyncResult:
    copied_topics: tuple[str, ...]
    refrozen_topics: tuple[str, ...]
    skipped_topics: tuple[str, ...]
    skeleton_copied: bool
    files_copied: int
    # Released topics refused because the source build errored for them
    # (issue #295). Not frozen — they promote once a build succeeds.
    failed_topics: tuple[str, ...] = ()
    # Evergreen skeleton files re-copied because their built content differed
    # from the destination's.
    refreshed_files: tuple[str, ...] = ()


def scan_evergreen(
    *,
    manifest: dict[str, Any],
    patterns: Iterable[str],
    dest_root: Path,
) -> EvergreenScan:
    """Match evergreen *patterns* against *manifest* and the destination state.

    Patterns are matched (``fnmatch.fnmatchcase``) against the manifest's
    destination-relative POSIX paths — for a language-scoped channel that is
    the path *after* re-rooting, i.e. exactly the path inside the cohort repo.

    A matching **skeleton** entry (``topic_id: null``) plans :data:`REFRESH`
    when the destination file is missing or its content hash differs from the
    manifest's ``content_hash``, else :data:`UP_TO_DATE`. The comparison is
    stateless — the destination *is* the record — which is sound because
    promotion copies bytes verbatim (after a copy, dest hash == manifest
    hash). Matching topic-owned entries are collected separately and never
    planned; VCS metadata paths are refused outright (issue #302).
    """
    pattern_list = [p for p in patterns if p]
    if not pattern_list:
        return EvergreenScan()
    plans: list[EvergreenPlan] = []
    topic_owned: list[str] = []
    for entry in manifest.get("files", []):
        rel = entry.get("path", "")
        if not any(fnmatchcase(rel, pattern) for pattern in pattern_list):
            continue
        if _is_vcs_path(rel):
            logger.debug("evergreen: refusing VCS metadata path: %s", rel)
            continue
        if entry.get("topic_id") is not None:
            topic_owned.append(rel)
            continue
        dst = dest_root / rel
        current = dst.is_file() and hash_file(dst) == entry.get("content_hash")
        plans.append(EvergreenPlan(rel, UP_TO_DATE if current else REFRESH))
    return EvergreenScan(plans=tuple(plans), topic_owned_matches=tuple(topic_owned))


def plan_sync(
    *,
    manifest: dict[str, Any],
    ledger_released: Iterable[str],
    frozen: FrozenManifest,
    refreeze: Iterable[str] = (),
    evergreen: Iterable[EvergreenPlan] = (),
    skeleton: SkeletonScan | None = None,
) -> SyncPlan:
    """Compute what a sync would do, without touching the filesystem.

    A released topic listed in the manifest's ``failed_topics`` (a partial
    manifest from an errored build, issue #295) is refused with
    :data:`SKIP_FAILED` rather than copied — unless it is already frozen and
    not being refrozen, in which case the ordinary :data:`SKIP_FROZEN` applies
    (the cohort already has it; nothing would be copied anyway).

    *evergreen* and *skeleton* carry the results of prior
    :func:`scan_evergreen` / :func:`scan_skeleton` calls (they read the
    destination, so they stay out of this pure planning step). Without a
    *skeleton* scan a needed skeleton copy covers every skeleton file; with
    one it is restricted to the files missing at the destination
    (presence-as-frozen, issue #325).
    """
    refreeze_set = set(refreeze)
    failed_topics = set(manifest.get("failed_topics", []))
    by_topic = manifest_files_by_topic(manifest)
    plans: list[TopicPlan] = []
    for topic_id in ledger_released:
        file_count = len(by_topic.get(topic_id, []))
        if frozen.is_frozen(topic_id) and topic_id not in refreeze_set:
            action = SKIP_FROZEN
        elif topic_id in failed_topics:
            action = SKIP_FAILED
        elif frozen.is_frozen(topic_id):
            action = REFREEZE
        else:
            action = COPY
        plans.append(TopicPlan(topic_id, action, file_count))
    skeleton_files = by_topic.get(None, [])
    copy_skeleton = not frozen.skeleton_frozen
    if copy_skeleton and skeleton is not None:
        skeleton_to_copy: tuple[str, ...] | None = skeleton.missing
        skeleton_file_count = len(skeleton.missing)
        skeleton_present_count = len(skeleton.present)
    else:
        skeleton_to_copy = None
        skeleton_file_count = len(skeleton_files)
        skeleton_present_count = 0
    return SyncPlan(
        copy_skeleton=copy_skeleton,
        skeleton_file_count=skeleton_file_count,
        topics=tuple(plans),
        evergreen=tuple(evergreen),
        skeleton_to_copy=skeleton_to_copy,
        skeleton_present_count=skeleton_present_count,
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

    The evergreen pass executes ``plan.evergreen_refresh`` — the refreshes not
    already satisfied by this sync's own skeleton copy. On a plain first sync
    the skeleton copy delivers every skeleton file, evergreen ones included;
    with a presence-restricted copy (issue #325) a *present* evergreen file
    whose content differs still refreshes.
    """
    by_topic = manifest_files_by_topic(manifest)
    source_commit = manifest.get("source_commit")
    files_copied = 0
    copied: list[str] = []
    refrozen: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    refreshed: list[str] = []
    skeleton_entries = by_topic.get(None, [])

    if plan.copy_skeleton:
        entries = skeleton_entries
        if plan.skeleton_to_copy is not None:
            wanted = set(plan.skeleton_to_copy)
            entries = [e for e in skeleton_entries if e["path"] in wanted]
        files_copied += _copy_files(entries, source_root, dest_root)
        frozen.skeleton_frozen = True

    skeleton_by_path = {e["path"]: e for e in skeleton_entries}
    for evergreen_plan in plan.evergreen_refresh:
        entry = skeleton_by_path.get(evergreen_plan.path)
        if entry is None:
            # The scan only plans skeleton entries; a miss means the plan
            # and manifest went out of sync — skip rather than guess.
            logger.warning(
                "evergreen: %r is not a skeleton file in the manifest; skipped",
                evergreen_plan.path,
            )
            continue
        if _copy_files([entry], source_root, dest_root):
            files_copied += 1
            refreshed.append(evergreen_plan.path)

    for topic_plan in plan.topics:
        if topic_plan.action == SKIP_FROZEN:
            skipped.append(topic_plan.topic_id)
            continue
        if topic_plan.action == SKIP_FAILED:
            logger.warning(
                "release sync: topic %r failed in the source build; refusing to "
                "promote it until a build succeeds (issue #295)",
                topic_plan.topic_id,
            )
            failed.append(topic_plan.topic_id)
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
        failed_topics=tuple(failed),
        refreshed_files=tuple(refreshed),
    )


# Never promote VCS metadata, no matter what the manifest claims (issue #302):
# copying e.g. ``.git/index`` into the destination working tree would corrupt
# the cohort repo the sync is populating.
_VCS_DIR_NAMES = frozenset({".git", ".svn", ".hg"})


def _is_vcs_path(rel: str) -> bool:
    return any(part in _VCS_DIR_NAMES for part in PurePosixPath(rel).parts)


def _copy_files(files: list[dict[str, Any]], source_root: Path, dest_root: Path) -> int:
    copied = 0
    refused_vcs = 0
    for entry in files:
        rel = entry["path"]
        if _is_vcs_path(rel):
            refused_vcs += 1
            logger.debug("release sync: refusing VCS metadata path from manifest: %s", rel)
            continue
        src = source_root / rel
        if not src.is_file():
            logger.warning("release sync: source file missing, skipping: %s", src)
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    if refused_vcs:
        logger.warning(
            "release sync: refused to copy %d VCS metadata file(s) (.git/.svn/.hg) "
            "listed in the source manifest — the manifest is polluted (issue #302); "
            "rebuild the source target to clean it",
            refused_vcs,
        )
    return copied
