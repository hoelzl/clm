"""Ownership evidence for the build's destructive output operations.

``clm build`` deletes files it did not create: the post-build stray-file
sweep removes everything under an output root that the write registries
do not account for, and ``--clean`` wipes each root outright. Both are
correct *when the root belongs to CLM* — the design principle is that
"everything in an output directory is owned by ``clm build``" (see
:mod:`clm.build.output_sweep`).

Nothing checked that premise. A spec whose ``<output-target><path>``
pointed at a directory full of unrelated files — the one-character
``<path>.</path>`` typo being the sharpest case — got the whole tree
deleted (adversarial review finding S11, tracked in #798).
:func:`snapshot_output_ownership` records, **before the build creates
anything**, which roots CLM can prove it owns:

* the root did not exist, or was empty, at build start;
* the root (or a declared output-target root above it) carries a
  provenance manifest from an earlier build — this spec's
  ``.clm-manifest.<spec>.json``, another spec's sharing the root, or a
  legacy ``.clm-manifest.json`` (issue #1095; see
  :func:`snapshot_output_ownership` for why another spec's counts).

The sweep adds a third, registry-based line of evidence that only it can
produce — if the walk finds nothing unexpected, there is nothing to
refuse (see ``unowned_roots`` in :func:`clm.build.output_sweep.
sweep_stray_files`).

The snapshot must be taken before the output directories are
pre-created, which is why it lives at the top of
``process_course_with_backend`` rather than inside either consumer.
"""

from __future__ import annotations

import logging
import stat
from collections.abc import Iterable, Sequence
from pathlib import Path

from attrs import frozen

from clm.build.errors import OutputDirCollisionError, UnownedOutputRootError
from clm.core.provenance_manifest import (
    find_spec_manifest,
    foreign_manifests,
    load_manifest,
    manifest_claims_dir,
    manifest_files_in,
)

logger = logging.getLogger(__name__)

OVERRIDE_FLAG = "--allow-unowned-output"
"""The deliberate operator escape hatch.

Deliberately *not* ``--clean``: ``--clean`` is the dangerous operation
being gated, so overloading it as its own override would gate nothing.
"""


@frozen
class RootOwnership:
    """Whether one output root is CLM's to delete from, and why."""

    root: Path
    evidence: str | None
    """Human-readable proof of ownership, or ``None`` when unproven."""

    reason: str | None = None
    """Why ownership could not be proven; ``None`` when it could."""

    @property
    def owned(self) -> bool:
        return self.evidence is not None


@frozen
class OutputOwnership:
    """Ownership evidence for every output root of one build."""

    roots: tuple[RootOwnership, ...] = ()

    def _entry(self, root: Path) -> RootOwnership | None:
        for entry in self.roots:
            if entry.root == root:
                return entry
        return None

    def is_owned(self, root: Path) -> bool:
        """True when *root* is CLM's to delete from.

        An unknown root (one not in the snapshot) is reported as owned:
        the gate only speaks about roots it actually examined, and a
        caller passing something else has not been given a refusal.
        """
        entry = self._entry(root)
        return True if entry is None else entry.owned

    def evidence_for(self, root: Path) -> str | None:
        entry = self._entry(root)
        return entry.evidence if entry else None

    def reason_for(self, root: Path) -> str | None:
        """Why *root* is not owned, or ``None`` when it is."""
        entry = self._entry(root)
        return entry.reason if entry else None

    @property
    def unowned_roots(self) -> tuple[Path, ...]:
        return tuple(entry.root for entry in self.roots if not entry.owned)


def _is_empty(directory: Path) -> bool:
    try:
        next(directory.iterdir())
    except StopIteration:
        return True
    except OSError as exc:  # unreadable → cannot prove ownership
        logger.warning("Output ownership: cannot list %s: %s", directory, exc)
        return False
    return False


def _manifest_dirs_for(root: Path, manifest_roots: Sequence[Path]) -> list[Path]:
    """Directories whose manifest would prove ownership of *root*.

    The root itself, plus any declared output-target root that contains
    it — the provenance manifest is written at the *target* root while
    the swept/wiped roots are the per-language directories below it.
    Ancestors that are not declared target roots are deliberately not
    consulted: an unbounded walk upwards would let a manifest in some
    unrelated parent authorize deleting a sibling tree.
    """
    candidates = [root]
    for manifest_root in manifest_roots:
        if manifest_root == root or manifest_root in root.parents:
            candidates.append(manifest_root)
    return candidates


def _manifest_evidence(directory: Path, spec_name: str | None) -> Path | None:
    """A manifest in *directory* that marks it as a clm output root, if any.

    This spec's own manifest is preferred (it makes the clearest evidence
    message); any other provenance manifest qualifies too. ``is_file``, not
    ``exists``: a *directory* named like a manifest is not one and must not
    authorize deletion.
    """
    own = find_spec_manifest(directory, spec_name)
    if own is not None:
        return own
    present = manifest_files_in(directory)
    return present[0] if present else None


def snapshot_output_ownership(
    root_dirs: Iterable[Path],
    *,
    manifest_roots: Iterable[Path] = (),
    spec_name: str | None = None,
) -> OutputOwnership:
    """Record ownership evidence for *root_dirs* as they are right now.

    Call this **before** the build creates or wipes anything: the
    empty-at-start rule is meaningless once ``precreate_output_directories``
    has run and populated the tree.

    Args:
        root_dirs: The roots the build will sweep (and, under
            ``--clean``, wipe).
        manifest_roots: Declared output-target roots, i.e. the
            directories a previous build would have written its
            provenance manifest into.
        spec_name: The spec being built; its own manifest is the
            preferred evidence.

    Any provenance manifest in a covering target root counts — this
    spec's, a legacy ``.clm-manifest.json``, or *another* spec's that
    shares the root (issue #1095). That keeps the trust level of the
    single shared manifest that preceded per-spec manifests: it proves
    the target root is a clm output tree, and *root_dirs* are always this
    spec's own course directories below it, so the sweep and ``--clean``
    never reach into another spec's directory. A course directory that
    another spec's manifest claims is refused before the build starts by
    :func:`find_course_dir_collisions` instead. Accepting only this
    spec's manifest would strand every spec but the last one rebuilt in a
    root migrated from the legacy single manifest.
    """
    manifest_root_list = list(manifest_roots)
    entries: list[RootOwnership] = []
    seen: set[Path] = set()

    for root in root_dirs:
        if root in seen:
            continue
        seen.add(root)

        try:
            stat_result = root.stat()
        except FileNotFoundError:
            entries.append(RootOwnership(root, evidence="did not exist at build start"))
            continue
        except OSError as exc:
            # ``Path.exists()`` would swallow this and report "absent",
            # i.e. owned. An unreadable root is exactly the case to fail
            # closed on.
            entries.append(
                RootOwnership(
                    root,
                    evidence=None,
                    reason=f"could not be inspected at build start ({exc})",
                )
            )
            continue

        if not stat.S_ISDIR(stat_result.st_mode):
            # Neither destructive operation touches a non-directory root:
            # the sweep skips it with a warning and ``shutil.rmtree(...,
            # ignore_errors=True)`` is a no-op on a file. Refusing here
            # would turn a pre-existing broken setup into a new hard
            # build failure with an unrelated-sounding message.
            entries.append(
                RootOwnership(root, evidence="is not a directory; nothing is deleted there")
            )
            continue
        if _is_empty(root):
            entries.append(RootOwnership(root, evidence="was empty at build start"))
            continue

        evidence = next(
            (
                found
                for candidate in _manifest_dirs_for(root, manifest_root_list)
                if (found := _manifest_evidence(candidate, spec_name)) is not None
            ),
            None,
        )
        if evidence is not None:
            entries.append(RootOwnership(root, evidence=f"{evidence} is present"))
            continue

        entries.append(
            RootOwnership(
                root,
                evidence=None,
                reason=(
                    "the directory already holds files and carries no "
                    "provenance manifest from a previous clm build"
                ),
            )
        )

    return OutputOwnership(tuple(entries))


def describe_refusal(
    ownership: OutputOwnership,
    *,
    operation: str,
    roots: Sequence[Path] | None = None,
) -> str:
    """Render the refusal message for unowned roots.

    *roots* narrows the message to the directories actually refused —
    the sweep proceeds in an unowned root whose contents the registries
    fully account for, so its refusal list can be shorter than
    ``ownership.unowned_roots``. Defaults to all unowned roots.
    """
    refused = list(ownership.unowned_roots if roots is None else roots)
    lines = [
        f"{operation} deletes everything under the build's output roots, but "
        f"clm cannot verify it owns the following director"
        f"{'y' if len(refused) == 1 else 'ies'}:"
    ]
    for root in refused:
        lines.append(f"  {root} — {ownership.reason_for(root)}")
    # Deliberately *not* "re-run a normal build so it writes the
    # manifest": a refused root gets no manifest, precisely so the
    # refusal cannot authorize the next build. Re-running changes
    # nothing until the directory or the spec does.
    lines.append(
        "Nothing was deleted, and re-running the build will refuse again. "
        "Move the files out of the way (or empty the directory), point "
        "<output-target><path> at a directory clm owns, or — if this tree "
        f"really is clm's — pass {OVERRIDE_FLAG} once, which deletes the "
        "unaccounted-for files and writes the provenance manifest "
        "(.clm-manifest.<spec>.json) that marks the tree as "
        "clm's from then on."
    )
    return "\n".join(lines)


def enforce_owned_roots(
    ownership: OutputOwnership,
    *,
    operation: str,
    allow_unowned: bool,
) -> None:
    """Refuse *operation* unless every root in *ownership* is proven CLM's.

    Fails closed and fails *first*: the caller must invoke this before
    deleting anything, so a refusal leaves the whole tree untouched
    rather than half-wiped.

    Raises:
        UnownedOutputRootError: When any root lacks ownership evidence
            and *allow_unowned* is False.
    """
    unowned = ownership.unowned_roots
    if not unowned:
        return
    if allow_unowned:
        logger.warning(
            "%s: %s given; proceeding in %d unverified output root(s): %s",
            operation,
            OVERRIDE_FLAG,
            len(unowned),
            ", ".join(str(root) for root in unowned),
        )
        return
    raise UnownedOutputRootError(describe_refusal(ownership, operation=operation))


@frozen
class CourseDirCollision:
    """A course directory this build writes that another spec's manifest claims."""

    course_dir: Path
    manifest: Path
    other_spec: str


def find_course_dir_collisions(
    root_dirs: Iterable[Path],
    *,
    manifest_roots: Iterable[Path],
    spec_name: str | None,
) -> list[CourseDirCollision]:
    """Course directories of this build that another spec already builds into.

    Specs may share an output-target root (issue #1095): each keeps its own
    ``.clm-manifest.<spec>.json`` there, and each build only writes, sweeps
    and wipes its own course directories (*root_dirs*). Two specs resolving
    to the *same* course directory — same ``<project-slug>`` (or localized
    directory name) under the same root — would silently overwrite each
    other's output and both claim it. This finds that case from the other
    specs' manifests, so the build can refuse before it writes anything.

    Without a known spec nothing is foreign, so nothing is reported.
    """
    if spec_name is None:
        return []
    targets = list(manifest_roots)
    loaded: dict[Path, dict | None] = {}
    collisions: list[CourseDirCollision] = []
    for root in dict.fromkeys(root_dirs):
        for target_root in targets:
            if not (target_root == root or target_root in root.parents):
                continue
            rel = root.relative_to(target_root).as_posix()
            for path, owner in foreign_manifests(target_root, spec_name):
                if path not in loaded:
                    try:
                        loaded[path] = load_manifest(path)
                    except (OSError, ValueError) as exc:
                        logger.warning("Cannot read provenance manifest %s: %s", path, exc)
                        loaded[path] = None
                manifest = loaded[path]
                if manifest is not None and manifest_claims_dir(manifest, rel):
                    collisions.append(CourseDirCollision(root, path, owner or "?"))
    return collisions


def enforce_no_course_dir_collisions(
    root_dirs: Iterable[Path],
    *,
    manifest_roots: Iterable[Path],
    spec_name: str | None,
) -> None:
    """Refuse the build when another spec already builds into one of its course dirs.

    Deliberately not overridable by ``--allow-unowned-output``: that flag
    adopts a tree nobody can prove is clm's, while here another spec
    demonstrably owns the directory. The remedies are structural (a
    distinct ``<project-slug>`` or output-target ``<path>``), or deleting
    the other spec's manifest when that spec no longer builds there.

    Raises:
        OutputDirCollisionError: When any course directory is claimed.
    """
    collisions = find_course_dir_collisions(
        root_dirs, manifest_roots=manifest_roots, spec_name=spec_name
    )
    if not collisions:
        return
    lines = [
        f"{spec_name} would build into course director"
        f"{'y' if len(collisions) == 1 else 'ies'} that another spec already owns:"
    ]
    for collision in collisions:
        lines.append(
            f"  {collision.course_dir} — claimed by {collision.other_spec} ({collision.manifest})"
        )
    lines.append(
        "Specs may share an output root, but each needs its own course directory. "
        "Nothing was written. Give the specs different <project-slug>s or point their "
        "<output-target><path>s at different roots. If the other spec no longer builds "
        "there (it was renamed or retired), delete its manifest file named above."
    )
    raise OutputDirCollisionError("\n".join(lines))
