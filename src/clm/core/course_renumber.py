"""Plan and apply spec-order renumbering of ``topic_NNN_`` directories.

``clm course renumber`` (issue #589; design:
``docs/claude/design/course-restructure-move-rename.md`` §5.1) renames the
topic directories of a module so their ordinal prefixes ascend in the course
spec's topic order. The ``NNN`` prefix is a sort key only — topic identity is
the suffix (``simplify_ordered_name``) — so a renumber changes no identity,
no spec reference, no output path, and no sync-ledger key. The only derived
state that must follow the rename is the input-path columns of
``clm_cache.db``, handled by
:mod:`clm.infrastructure.database.cache_path_migration`.

This module is deliberately CLI-free: :func:`plan_renumber` is pure planning
(fail-closed validation, no filesystem writes), :func:`apply_renumber`
performs the two-phase move. The CLI wires them to the cache migrator and
the active-build guard.

Hard guards (issue #589):

- Only canonical ``topic_<digits>_<suffix>`` names are renamed.
  ``simplify_ordered_name`` blindly drops the first two ``_``-parts, so
  renumbering a non-canonical name like ``topic_extras_bonus`` (id ``bonus``)
  would silently *change its topic id*. Such topics are skipped and reported.
- Orphan entries (on disk, not referenced by the spec) are never touched, and
  a planned name that collides with one fails the whole plan.
- Ambiguous topic resolution (same id in several modules without a
  ``module=`` binding) fails the plan; renumbering a tree the build cannot
  resolve deterministically would guess.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import frozen

from clm.core.topic_resolver import build_topic_map, matches_for_binding

if TYPE_CHECKING:
    from clm.core.course_spec import CourseSpec

logger = logging.getLogger(__name__)

__all__ = [
    "ModulePlan",
    "RenumberError",
    "RenumberPlan",
    "SkippedTopic",
    "TopicRenameOp",
    "apply_renumber",
    "plan_renumber",
]

#: A rename only ever swaps the digits of this pattern; the suffix — the
#: identity — is preserved verbatim (including any file extension).
_CANONICAL_TOPIC_RE = re.compile(r"topic_(\d+)_(.+)", re.DOTALL)

#: Interim names used by the two-phase move so a batch whose target names
#: overlap its source names can never collide mid-move.
_TMP_SUFFIX = ".clm-renumber-tmp-"


class RenumberError(Exception):
    """A fail-closed validation error: nothing has been touched."""


@frozen
class TopicRenameOp:
    """One topic directory (or file topic) changing its ordinal prefix."""

    topic_id: str
    old_path: Path
    new_path: Path


@frozen
class SkippedTopic:
    """A spec-referenced topic the plan refuses to rename, with the reason."""

    topic_id: str
    path: Path
    reason: str


@frozen
class ModulePlan:
    """The renumber outcome for one module directory."""

    module: str
    renames: tuple[TopicRenameOp, ...]
    unchanged: tuple[Path, ...]
    skipped: tuple[SkippedTopic, ...]


@frozen
class RenumberPlan:
    """A validated, ready-to-apply renumber across one or more modules."""

    spec_name: str
    slides_dir: Path
    modules: tuple[ModulePlan, ...]
    #: Spec-referenced topic ids that resolve to nothing on disk. Not an
    #: error here — ``clm validate`` owns dangling-spec reporting — but the
    #: report surfaces them so a renumber never *silently* covers less than
    #: the spec.
    missing: tuple[str, ...]

    @property
    def renames(self) -> tuple[TopicRenameOp, ...]:
        return tuple(op for m in self.modules for op in m.renames)


def plan_renumber(
    spec: CourseSpec,
    slides_dir: Path,
    *,
    spec_name: str,
    module: str | None = None,
    start: int = 10,
    step: int = 10,
    width: int | None = None,
) -> RenumberPlan:
    """Compute the spec-order renumbering of every (or one) module's topics.

    Topics are numbered ``start, start+step, …`` in the order their bindings
    appear in the spec (the same ``iter_topic_bindings`` order the build,
    validate, and normalize share). ``width`` zero-pads the ordinal; when
    ``None`` the widest existing ordinal of the module is preserved.

    Raises :class:`RenumberError` (fail closed, nothing touched) on ambiguous
    topic resolution, an unknown ``module`` filter, an ordinal that does not
    fit ``width``, or a planned name colliding with an entry that is not
    itself being renamed.
    """
    if step <= 0 or start < 0:
        raise RenumberError(f"invalid numbering scheme: start={start}, step={step}")

    ordered, missing = _spec_ordered_topics(spec, slides_dir)

    if module is not None:
        if module not in ordered:
            known = ", ".join(sorted(ordered)) or "none found"
            raise RenumberError(
                f"module '{module}' has no spec-referenced topics (modules with topics: {known})"
            )
        ordered = {module: ordered[module]}

    modules = tuple(
        _plan_module(name, matches, start=start, step=step, width=width)
        for name, matches in ordered.items()
    )
    _validate_collisions(modules)

    return RenumberPlan(
        spec_name=spec_name,
        slides_dir=slides_dir,
        modules=modules,
        missing=tuple(missing),
    )


def _spec_ordered_topics(spec: CourseSpec, slides_dir: Path):
    """Resolve the spec's topics against the tree, grouped by module in spec
    order. Ambiguity is a hard error: a tree the build cannot resolve
    deterministically must be fixed before it is renumbered."""
    topic_map = build_topic_map(slides_dir)

    ordered: dict[str, list] = {}
    seen_paths: set[Path] = set()
    missing: list[str] = []
    for binding in spec.iter_topic_bindings():
        matches = matches_for_binding(topic_map, binding.topic_id, binding.effective_module)
        if not matches:
            missing.append(binding.topic_id)
            continue
        if len(matches) > 1:
            locations = ", ".join(str(m.path) for m in matches)
            raise RenumberError(
                f"topic '{binding.topic_id}' is ambiguous ({locations}); "
                f"fix the duplicate or bind it with module= in the spec before renumbering"
            )
        match = matches[0]
        if match.path in seen_paths:  # same topic referenced twice: first wins
            continue
        seen_paths.add(match.path)
        ordered.setdefault(match.module, []).append(match)
    return ordered, missing


def _plan_module(
    module: str,
    matches: list,
    *,
    start: int,
    step: int,
    width: int | None,
) -> ModulePlan:
    canonical: list[tuple[str, Path, str, str]] = []  # (topic_id, path, digits, suffix)
    skipped: list[SkippedTopic] = []
    for match in matches:
        m = _CANONICAL_TOPIC_RE.fullmatch(match.path.name)
        if m is None:
            skipped.append(
                SkippedTopic(
                    topic_id=match.topic_id,
                    path=match.path,
                    reason=(
                        "non-canonical name (not topic_<digits>_<suffix>) — "
                        "renumbering it would change its topic id"
                    ),
                )
            )
            continue
        canonical.append((match.topic_id, match.path, m.group(1), m.group(2)))

    pad = (
        width
        if width is not None
        else max((len(digits) for _, _, digits, _ in canonical), default=3)
    )

    renames: list[TopicRenameOp] = []
    unchanged: list[Path] = []
    for index, (topic_id, path, _digits, suffix) in enumerate(canonical):
        number = start + index * step
        if len(str(number)) > pad:
            raise RenumberError(
                f"module '{module}': ordinal {number} does not fit width {pad} "
                f"(pass --width {len(str(number))} or adjust --start/--step)"
            )
        new_name = f"topic_{number:0{pad}d}_{suffix}"
        if new_name == path.name:
            unchanged.append(path)
        else:
            renames.append(
                TopicRenameOp(topic_id=topic_id, old_path=path, new_path=path.with_name(new_name))
            )

    return ModulePlan(
        module=module,
        renames=tuple(renames),
        unchanged=tuple(unchanged),
        skipped=tuple(skipped),
    )


def _validate_collisions(modules: tuple[ModulePlan, ...]) -> None:
    """No planned name may collide — with another planned name, or with any
    on-disk entry that is not itself renamed away (orphans, skipped topics)."""
    for plan in modules:
        vacated = {os.path.normcase(str(op.old_path)) for op in plan.renames}
        targets: dict[str, TopicRenameOp] = {}
        for op in plan.renames:
            key = os.path.normcase(str(op.new_path))
            if key in targets:
                raise RenumberError(
                    f"module '{plan.module}': '{op.new_path.name}' is the target of both "
                    f"'{targets[key].old_path.name}' and '{op.old_path.name}'"
                )
            targets[key] = op
            if op.new_path.exists() and key not in vacated:
                raise RenumberError(
                    f"module '{plan.module}': target '{op.new_path.name}' already exists and is "
                    f"not being renamed away (orphan or skipped entry) — resolve it first"
                )


def apply_renumber(plan: RenumberPlan, *, use_git: bool | None = None) -> bool:
    """Perform the planned moves; returns whether ``git mv`` was used.

    Two-phase: every source first moves to a unique interim name, then to its
    final name — so a batch whose targets overlap its sources (impossible for
    a legal topic renumber, cheap insurance regardless) cannot collide
    mid-move. Moves go through ``git mv`` when the slides tree is inside a
    git work tree (history-preserving; the physical rename also carries
    untracked sidecars like ``.clm/`` ledgers), else ``Path.rename``.
    """
    ops = plan.renames
    if not ops:
        return False
    git = _in_git_work_tree(plan.slides_dir) if use_git is None else use_git

    interim = [
        op.old_path.with_name(f"{op.old_path.name}{_TMP_SUFFIX}{i}") for i, op in enumerate(ops)
    ]
    for op, tmp in zip(ops, interim, strict=True):
        _move(op.old_path, tmp, git=git)
    for op, tmp in zip(ops, interim, strict=True):
        _move(tmp, op.new_path, git=git)
    return git


def _in_git_work_tree(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _move(src: Path, dst: Path, *, git: bool) -> None:
    if git:
        result = subprocess.run(
            ["git", "-C", str(src.parent), "mv", str(src), str(dst)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        # E.g. an untracked topic dir: fall back to a plain rename (the user
        # sees a delete+add instead of a rename in git status — still correct).
        logger.warning(
            "git mv %s -> %s failed (%s); falling back to rename", src, dst, result.stderr.strip()
        )
    src.rename(dst)
