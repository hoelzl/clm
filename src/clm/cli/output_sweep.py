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


@define
class _SweepState:
    """Mutable counters threaded through the recursive walk."""

    deleted_files: list[Path] = field(factory=list)
    removed_dirs: list[Path] = field(factory=list)
    skipped_subtrees: list[Path] = field(factory=list)
    kept_due_to_pattern: int = 0


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
) -> SweepReport:
    """Walk each root and delete files not in the registries' tracked sets.

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
    """
    if skip_reason is not None:
        return SweepReport(skipped=True, skip_reason=skip_reason, dry_run=dry_run)

    expected: set[Path] = set(output_write_registry.entries.keys())
    if image_registry is not None:
        expected.update(image_registry.tracked_paths)

    state = _SweepState()

    for root in root_dirs:
        if not root.exists():
            continue
        if not root.is_dir():
            logger.warning("Sweep: skipping non-directory root %s", root)
            continue
        _sweep_directory(
            root,
            root,
            expected,
            keep_patterns=tuple(keep_patterns),
            dry_run=dry_run,
            state=state,
        )

    return SweepReport(
        deleted_files=state.deleted_files,
        removed_dirs=state.removed_dirs,
        skipped_subtrees=state.skipped_subtrees,
        kept_due_to_pattern=state.kept_due_to_pattern,
        dry_run=dry_run,
    )


def _sweep_directory(
    directory: Path,
    root: Path,
    expected: set[Path],
    *,
    keep_patterns: tuple[str, ...],
    dry_run: bool,
    state: _SweepState,
) -> bool:
    """Recursively sweep ``directory``. Returns True iff it became empty.

    A directory becomes empty if all entries it contains were deleted
    by the sweep (or it was empty to begin with). The caller may then
    remove it.

    Subtrees containing a nested ``.git/`` are skipped entirely.
    """
    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        logger.warning("Sweep: cannot scan %s: %s", directory, exc)
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
            state.kept_due_to_pattern += 1
            became_empty = False
            continue

        if is_dir:
            if _has_nested_git(entry_path):
                logger.debug("Sweep: skipping nested git repo at %s", entry_path)
                state.skipped_subtrees.append(entry_path)
                became_empty = False
                continue
            child_empty = _sweep_directory(
                entry_path,
                root,
                expected,
                keep_patterns=keep_patterns,
                dry_run=dry_run,
                state=state,
            )
            if child_empty:
                if not dry_run:
                    try:
                        entry_path.rmdir()
                    except OSError as exc:
                        logger.warning("Sweep: cannot remove empty dir %s: %s", entry_path, exc)
                        became_empty = False
                        continue
                state.removed_dirs.append(entry_path)
            else:
                became_empty = False
            continue

        if is_file_or_symlink:
            if entry_path in expected:
                became_empty = False
                continue
            if not dry_run:
                try:
                    entry_path.unlink()
                except OSError as exc:
                    logger.warning("Sweep: cannot remove %s: %s", entry_path, exc)
                    became_empty = False
                    continue
            state.deleted_files.append(entry_path)
            continue

        # Anything else (block device, fifo, …) — leave alone.
        became_empty = False

    return became_empty
