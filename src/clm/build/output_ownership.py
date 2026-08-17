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
  ``.clm-manifest.json`` provenance index from an earlier build.

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
from collections.abc import Iterable, Sequence
from pathlib import Path

from attrs import frozen

from clm.build.errors import UnownedOutputRootError
from clm.core.provenance_manifest import MANIFEST_FILENAME

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


def snapshot_output_ownership(
    root_dirs: Iterable[Path],
    *,
    manifest_roots: Iterable[Path] = (),
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
            ``.clm-manifest.json`` into.
    """
    manifest_root_list = list(manifest_roots)
    entries: list[RootOwnership] = []
    seen: set[Path] = set()

    for root in root_dirs:
        if root in seen:
            continue
        seen.add(root)

        if not root.exists():
            entries.append(RootOwnership(root, evidence="did not exist at build start"))
            continue
        if not root.is_dir():
            entries.append(
                RootOwnership(
                    root,
                    evidence=None,
                    reason="exists but is not a directory",
                )
            )
            continue
        if _is_empty(root):
            entries.append(RootOwnership(root, evidence="was empty at build start"))
            continue

        manifest_dir = next(
            (
                candidate
                for candidate in _manifest_dirs_for(root, manifest_root_list)
                if (candidate / MANIFEST_FILENAME).exists()
            ),
            None,
        )
        if manifest_dir is not None:
            entries.append(
                RootOwnership(root, evidence=f"{manifest_dir / MANIFEST_FILENAME} is present")
            )
            continue

        entries.append(
            RootOwnership(
                root,
                evidence=None,
                reason=(
                    f"the directory already holds files and carries no "
                    f"{MANIFEST_FILENAME} from a previous clm build"
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
    lines.append(
        "Point <output-target><path> at a directory clm owns, empty the "
        "directory yourself, or re-run a normal build first so it writes "
        f"its {MANIFEST_FILENAME} provenance index. To delete anyway, pass "
        f"{OVERRIDE_FLAG}."
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
