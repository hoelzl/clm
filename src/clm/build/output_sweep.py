"""End-of-build stray-file sweep.

After all build stages complete, the :class:`OutputWriteRegistry`
plus the :class:`ImageRegistry` together describe the complete set of
files the build *intended* to populate under each root directory.
Anything else in the tree is a leftover from a previous build (renamed
section, removed topic) or a hand-placed file. This module's
:func:`sweep_stray_files` walks each root and removes those leftovers
so that subsequent ``git status`` calls do not see a mix of current
and stale artifacts.

The sweep is intentionally strict — the design principle is that
**everything in an output directory is owned by ``clm build``**. The
only path the sweep refuses to touch is ``.git/`` (so a course-output
git repo survives across builds) and any subtree that contains its
own ``.git/`` directory (nested repos are treated as opaque). Other
files — even ``.gitignore``, ``README.md``, editor caches — are
swept; if a course genuinely needs an auxiliary file at the root of
its output, the right answer is for ``clm`` to generate it.

This module has no dependency on the build pipeline; the caller
(``build.py``) decides when to invoke the sweep (e.g. skipping it in
``--only-sections`` mode, in watch mode, or when stages have errored).
"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import Factory, define, field, frozen

if TYPE_CHECKING:
    from clm.core.image_registry import ImageRegistry
    from clm.core.output_write_registry import OutputWriteRegistry

logger = logging.getLogger(__name__)

DEFAULT_KEEP_PATTERNS: tuple[str, ...] = (".git/**",)
"""Only ``.git/**`` is protected from the sweep by default.

The output tree is exclusively CLM's: authors should never hand-place
auxiliary files (``.gitignore``, ``README.md``, editor caches) there.
``SKIP_DIRS_FOR_OUTPUT``/``SKIP_DIRS_PATTERNS``/``SKIP_OUTPUT_FILE_GLOBS``
from ``path_utils.py`` are **deliberately not included** here — those
patterns mark auto-generated junk or content withheld from students,
and the sweep should remove them if they appear under an output root.
"""


@frozen
class SweepReport:
    """Outcome of a single sweep run."""

    deleted_files: list[Path] = Factory(list)
    """Absolute paths of files deleted from the output tree."""

    removed_dirs: list[Path] = Factory(list)
    """Absolute paths of directories removed because they became empty."""

    kept_due_to_pattern: int = 0
    """Number of files kept solely because they matched ``keep_patterns``."""

    skipped_subtrees: list[Path] = Factory(list)
    """Subtrees skipped entirely because they contained a nested ``.git/``."""

    skipped: bool = False
    """Set to ``True`` when the sweep was a no-op (e.g. stage errors)."""

    skip_reason: str | None = None
    """Human-readable reason ``skipped`` is ``True``."""

    dry_run: bool = False
    """Set to ``True`` when no filesystem changes were performed."""

    protected_dirs: list[Path] = Factory(list)
    """Directories left untouched — with everything below them — because
    they hold a failed job's output.

    Populated from ``failed_outputs`` (issue #923): a job that failed this
    build wrote nothing, and may have companion files next to its output,
    so the exact set of missing writes is unknowable — the directory tree
    is skipped instead. Only directories the walk actually reached are
    listed.
    """

    unverified_roots: list[Path] = Factory(list)
    """Unowned roots whose plan was empty only because a protected
    directory was skipped (#923).

    An empty plan is ownership evidence only when the walk saw the whole
    tree. These roots are not *refused* — nothing would have been deleted
    — but they yield no evidence either, so the caller keeps treating them
    as unowned instead of clearing them.
    """

    refused_roots: list[Path] = Factory(list)
    """Roots left completely untouched because CLM cannot prove it owns them.

    A root listed here was passed in ``unowned_roots`` *and* the walk
    found something to delete; nothing under it was removed (finding
    S11, #798). A root with no ownership evidence whose contents are
    fully accounted for by the registries is not refused — there was
    nothing to delete in the first place.
    """


@define
class _SweepState:
    """Mutable counters threaded through the recursive walk."""

    deleted_files: list[Path] = field(factory=list)
    removed_dirs: list[Path] = field(factory=list)
    skipped_subtrees: list[Path] = field(factory=list)
    kept_due_to_pattern: int = 0
    protected_dirs: list[Path] = field(factory=list)


@define
class _SweepPlan:
    """What a walk of one root would delete, before anything is deleted.

    The sweep plans a whole root before touching it so an unowned root
    can be refused *without* a half-finished deletion — the same
    plan-then-execute shape the ``.clm-include`` removal uses (S4).
    """

    files: list[Path] = field(factory=list)
    """Files (and symlinks) to unlink."""

    dirs: list[Path] = field(factory=list)
    """Directories to remove, deepest first."""

    skipped_subtrees: list[Path] = field(factory=list)
    kept_due_to_pattern: int = 0
    protected_dirs: list[Path] = field(factory=list)
    """Directory trees skipped whole because a failed job's output lives there."""

    scan_failed: bool = False
    """Set when some directory in the walk could not be listed.

    An unreadable directory yields the same empty plan as a clean one, so
    the ownership gate must not read "nothing to delete" as evidence
    without knowing the walk actually saw the tree.
    """

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.dirs


def _matches_keep_pattern(rel_path: str, patterns: Iterable[str]) -> bool:
    """Return True iff ``rel_path`` matches any of ``patterns``.

    Uses :func:`fnmatch.fnmatchcase` on POSIX-style paths. Both
    ``.git/**`` and ``.git/*`` will match files anywhere under a
    top-level ``.git`` directory.
    """
    # Normalize to POSIX-style separators so patterns like ".git/**"
    # match on Windows where Path.relative_to yields backslashes.
    posix_rel = rel_path.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatchcase(posix_rel, pattern):
            return True
        # fnmatch's "**" does not span path segments the way glob does,
        # so we also accept the prefix-match interpretation: a pattern
        # ending in "/**" matches anything under that prefix.
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if posix_rel == prefix or posix_rel.startswith(prefix + "/"):
                return True
    return False


def _has_nested_git(directory: Path) -> bool:
    """Return True iff ``directory`` itself contains a ``.git`` entry.

    Treats both ``.git/`` directories and ``.git`` worktree files (used
    for nested git worktrees) as nested-repo markers.
    """
    try:
        return (directory / ".git").exists()
    except OSError:
        return False


def sweep_stray_files(
    root_dirs: Iterable[Path],
    output_write_registry: OutputWriteRegistry,
    image_registry: ImageRegistry | None = None,
    *,
    keep_patterns: Iterable[str] = DEFAULT_KEEP_PATTERNS,
    dry_run: bool = False,
    skip_reason: str | None = None,
    unowned_roots: Iterable[Path] = (),
    failed_outputs: Iterable[Path] = (),
) -> SweepReport:
    """Walk each root and delete files not in the registries' tracked sets.

    Each root is planned in full before anything is deleted, so a root
    CLM cannot prove it owns is refused whole rather than half-swept.

    Args:
        root_dirs: Output roots to walk. Each is treated independently;
            symlinks are not followed across roots.
        output_write_registry: Registry whose ``entries`` keys are the
            absolute output paths the build wrote.
        image_registry: Optional sibling registry whose ``tracked_paths``
            covers ``img/`` outputs (excluded from the
            ``OutputWriteRegistry``). When ``None``, image paths under
            any root will be treated as stray — pass the build's
            ``ImageRegistry`` to avoid that.
        keep_patterns: POSIX-style fnmatch patterns the sweep will not
            touch even if absent from the registries. Defaults to
            ``.git/**`` only.
        dry_run: When ``True``, no deletions occur; the returned report
            still lists what *would* be removed.
        skip_reason: When non-``None``, the sweep is a no-op and the
            reason is surfaced in the report. Used by callers that
            detect stage errors or other guard conditions and want a
            uniform "no-op" report.
        unowned_roots: Roots for which the caller could not establish
            ownership evidence (see
            :func:`clm.build.output_ownership.snapshot_output_ownership`).
            Such a root is swept only if the walk completed and found
            nothing to delete — the registries then account for
            everything on disk, which is itself the missing evidence.
            Otherwise the root is left untouched and listed in
            ``refused_roots``. Empty by default, so callers with no
            snapshot get the pre-gate behavior.
        failed_outputs: Output paths of jobs that failed this build
            (issue #923). The registry is missing exactly these writes,
            so instead of skipping the whole sweep the caller passes them
            here and the sweep protects them: the directory holding each
            failed output is left untouched **with everything below it**
            (a job may write companion files beside its output, so the
            missing set is not knowable file-by-file). Nothing else is
            special-cased — in particular a failed deck's previous copy
            at an *old* location is an ordinary stray and is swept; the
            deck regenerates there once it builds again. A protected
            directory withholds the ownership evidence an empty plan would
            otherwise give an unowned root (see ``unverified_roots``).
    """
    if skip_reason is not None:
        return SweepReport(skipped=True, skip_reason=skip_reason, dry_run=dry_run)

    expected: set[Path] = set(output_write_registry.entries.keys())
    if image_registry is not None:
        expected.update(image_registry.tracked_paths)

    protected_dirs = {Path(p).parent for p in failed_outputs}

    unowned = set(unowned_roots)
    state = _SweepState()
    refused: list[Path] = []
    unverified: list[Path] = []

    # The caller's roots legitimately repeat: an explicit target whose
    # kinds span both the public and the private branch derives the same
    # directory twice (``engine._compute_root_dirs``). Walking it twice
    # is wasted work, and refusing it twice would report one directory as
    # two.
    for root in dict.fromkeys(root_dirs):
        if not root.exists():
            continue
        if not root.is_dir():
            logger.warning("Sweep: skipping non-directory root %s", root)
            continue

        plan = _SweepPlan()
        _plan_directory(
            root,
            root,
            expected,
            keep_patterns=tuple(keep_patterns),
            plan=plan,
            protected_dirs=protected_dirs,
        )

        if root in unowned and (not plan.is_empty or plan.scan_failed):
            # ``plan.scan_failed`` matters as much as a non-empty plan: an
            # empty plan is only ownership evidence when the walk actually
            # saw the tree. A directory the sweep could not read produces
            # the same empty plan as a clean one, and clearing the root on
            # that basis would mark it as clm's without anything ever
            # having looked.
            logger.error(
                "Sweep refused in %s: clm cannot verify it owns this "
                "directory, and the sweep would have deleted %d file(s) and "
                "%d directory/ies%s. Nothing was removed.",
                root,
                len(plan.files),
                len(plan.dirs),
                " (part of the tree could not be read)" if plan.scan_failed else "",
            )
            refused.append(root)
            continue
        if root in unowned and plan.protected_dirs:
            # Nothing to delete, but part of the tree was skipped for a
            # failed job's output (#923), so the walk did not see all of
            # it: no ownership evidence either way. Not a refusal — the
            # sweep would have removed nothing — but the root must stay
            # unowned rather than be cleared on an unseen tree.
            unverified.append(root)

        state.kept_due_to_pattern += plan.kept_due_to_pattern
        state.skipped_subtrees.extend(plan.skipped_subtrees)
        state.protected_dirs.extend(plan.protected_dirs)
        _execute_plan(plan, dry_run=dry_run, state=state)

    return SweepReport(
        deleted_files=state.deleted_files,
        removed_dirs=state.removed_dirs,
        skipped_subtrees=state.skipped_subtrees,
        kept_due_to_pattern=state.kept_due_to_pattern,
        dry_run=dry_run,
        refused_roots=refused,
        protected_dirs=state.protected_dirs,
        unverified_roots=unverified,
    )


def _execute_plan(plan: _SweepPlan, *, dry_run: bool, state: _SweepState) -> None:
    """Carry out a planned sweep, recording what was actually removed.

    Files go first, then directories deepest-first (the order
    :func:`_plan_directory` produced them in). A deletion that fails is
    logged once and blocks the removal of every directory above it — the
    same outcome the pre-plan implementation reached by marking the
    parent non-empty, and without the cascade of "directory is not
    empty" warnings a blind ``rmdir`` per ancestor would produce (a
    single file held open by an editor or a virus scanner is routine on
    Windows).
    """
    if dry_run:
        state.deleted_files.extend(plan.files)
        state.removed_dirs.extend(plan.dirs)
        return

    blocked: set[Path] = set()

    for file_path in plan.files:
        try:
            file_path.unlink()
        except OSError as exc:
            logger.warning("Sweep: cannot remove %s: %s", file_path, exc)
            blocked.add(file_path.parent)
            continue
        state.deleted_files.append(file_path)

    for dir_path in plan.dirs:
        if dir_path in blocked:
            logger.debug("Sweep: not removing %s — something inside it survived", dir_path)
            blocked.add(dir_path.parent)
            continue
        try:
            dir_path.rmdir()
        except OSError as exc:
            logger.warning("Sweep: cannot remove empty dir %s: %s", dir_path, exc)
            blocked.add(dir_path.parent)
            continue
        state.removed_dirs.append(dir_path)


def _plan_directory(
    directory: Path,
    root: Path,
    expected: set[Path],
    *,
    keep_patterns: tuple[str, ...],
    plan: _SweepPlan,
    protected_dirs: set[Path] = frozenset(),  # type: ignore[assignment]
) -> bool:
    """Recursively plan the sweep of ``directory``. True iff it will empty.

    A directory will become empty if every entry it contains is planned
    for deletion (or it was empty to begin with). The caller may then
    plan its removal — appended after its children, so ``plan.dirs`` is
    in deepest-first order.

    Subtrees containing a nested ``.git/`` are skipped entirely, and so is
    a directory in ``protected_dirs`` (a failed job's output lives there,
    issue #923) — recorded in ``plan.protected_dirs``, never emptied.
    """
    if directory in protected_dirs:
        # The registry is missing exactly this job's writes, and companion
        # files may sit beside its output — leave the directory, and
        # everything below it, exactly as it is.
        logger.debug("Sweep: leaving %s untouched (failed job output)", directory)
        plan.protected_dirs.append(directory)
        return False

    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        logger.warning("Sweep: cannot scan %s: %s", directory, exc)
        plan.scan_failed = True
        return False

    became_empty = True

    for entry in entries:
        entry_path = Path(entry.path)
        try:
            rel = entry_path.relative_to(root)
        except ValueError:
            became_empty = False
            continue
        rel_posix = rel.as_posix()

        is_dir = entry.is_dir(follow_symlinks=False)
        is_file_or_symlink = entry.is_file(follow_symlinks=False) or entry.is_symlink()

        if is_dir and entry.name == ".git":
            # Top-level (relative to root) or nested .git directory: leave entirely alone.
            became_empty = False
            continue

        if _matches_keep_pattern(rel_posix, keep_patterns):
            plan.kept_due_to_pattern += 1
            became_empty = False
            continue

        if is_dir:
            if _has_nested_git(entry_path):
                logger.debug("Sweep: skipping nested git repo at %s", entry_path)
                plan.skipped_subtrees.append(entry_path)
                became_empty = False
                continue
            child_empty = _plan_directory(
                entry_path,
                root,
                expected,
                keep_patterns=keep_patterns,
                plan=plan,
                protected_dirs=protected_dirs,
            )
            if child_empty:
                plan.dirs.append(entry_path)
            else:
                became_empty = False
            continue

        if is_file_or_symlink:
            if entry_path in expected:
                became_empty = False
                continue
            plan.files.append(entry_path)
            continue

        # Anything else (block device, fifo, …) — leave alone.
        became_empty = False

    return became_empty
