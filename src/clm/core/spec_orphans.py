"""Find decks reachable from no spec, and classify them (gap #7).

The inverse of :func:`clm.core.spec_decks.shipping_set`: given every spec in a
course, which decks on disk does *no* spec pull in? Course conversions hit two
flavours of orphan that must not be treated alike — an explicit ``_old`` /
``_bak`` deck is superseded and safe to archive, but a ``_part1``…``_part5``
series or a ``_short`` / ``_long`` length variant is **intentional alternate
content** that blind archiving would delete. This module separates them by
filename intent so the author can act on the right bucket, and surfaces
gitignored ``.ipynb_checkpoints/`` cruft as its own category.

Orphan detection is build-faithful: the shipping set comes from the same
resolver the build uses, and the on-disk walk is extension-complete (``.py``,
``.cpp``, ``.cs``, …) so it cannot miss a non-Python orphan the way a
``*.py``-only walk would.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.spec_decks import shipping_set
from clm.core.topic_resolver import TopicMatch, build_topic_map
from clm.infrastructure.utils.path_utils import (
    is_private_dir_name,
    is_slides_file,
    split_lang_suffix,
)

__all__ = [
    "CHECKPOINT_DIR_NAME",
    "Orphan",
    "OrphanKind",
    "OrphanReport",
    "classify_orphan",
    "find_checkpoint_dirs",
    "find_orphans",
    "render_report",
    "report_to_dict",
]

CHECKPOINT_DIR_NAME = ".ipynb_checkpoints"


class OrphanKind(enum.Enum):
    """Likely intent behind an unreferenced deck."""

    SUPERSEDED = "superseded"  # explicit _old / _bak / numeric dup — safe to archive
    ALTERNATE = "alternate"  # _partN / _short / _long — probably intentional
    UNKNOWN = "unknown"  # no recognizable marker — needs a human look


# Checked before the superseded patterns so a ``_part2`` is an alternate, never
# a numeric-dup. Each entry: (compiled pattern on the lowercased stem, reason).
_ALTERNATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"_part\d+$"), "multi-part series (_partN)"),
    (re.compile(r"_short$"), "length variant (_short)"),
    (re.compile(r"_long$"), "length variant (_long)"),
]

_SUPERSEDED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"_old\d*$"), "explicit _old marker"),
    (re.compile(r"_bak$"), "backup (_bak)"),
    (re.compile(r"_backup$"), "backup (_backup)"),
    (re.compile(r"_orig$"), "original (_orig)"),
    (re.compile(r"_deprecated$"), "deprecated marker"),
    (re.compile(r"_copy$"), "copy (_copy)"),
    (re.compile(r"_v\d+$"), "old version (_vN)"),
    (re.compile(r"_\d+$"), "numeric-suffixed duplicate"),
]


@dataclass
class Orphan:
    """An on-disk deck that no spec references."""

    path: Path
    kind: OrphanKind
    reason: str


@dataclass
class OrphanReport:
    """Outcome of an orphan scan over a course."""

    orphans: list[Orphan] = field(default_factory=list)
    checkpoints: list[Path] = field(default_factory=list)
    total_decks: int = 0
    shipping_count: int = 0

    @property
    def by_kind(self) -> dict[OrphanKind, list[Orphan]]:
        out: dict[OrphanKind, list[Orphan]] = {k: [] for k in OrphanKind}
        for orphan in self.orphans:
            out[orphan.kind].append(orphan)
        return out


def _deck_stem(path: Path) -> str:
    """Lowercased filename stem with the program-language and lang tag removed.

    ``slides_intro_old.de.py`` → ``slides_intro_old`` so the marker patterns see
    the authoring name, not the ``.de`` split tag.
    """
    name = path.name
    suffix = path.suffix
    stem = name[: -len(suffix)] if suffix else name
    lang = split_lang_suffix(path)
    if lang and stem.endswith(f".{lang}"):
        stem = stem[: -(len(lang) + 1)]
    return stem.lower()


def classify_orphan(path: Path) -> tuple[OrphanKind, str]:
    """Classify an orphan deck by filename intent.

    Returns ``(kind, reason)``. Alternate patterns (``_partN`` / ``_short`` /
    ``_long``) win over superseded ones so an intentional series is never
    mislabeled as a numeric duplicate.
    """
    stem = _deck_stem(path)
    for pattern, reason in _ALTERNATE_PATTERNS:
        if pattern.search(stem):
            return OrphanKind.ALTERNATE, reason
    for pattern, reason in _SUPERSEDED_PATTERNS:
        if pattern.search(stem):
            return OrphanKind.SUPERSEDED, reason
    return OrphanKind.UNKNOWN, "no recognizable marker"


def _is_checkpoint(path: Path) -> bool:
    return CHECKPOINT_DIR_NAME in path.parts


def find_checkpoint_dirs(slides_dir: Path) -> list[Path]:
    """Every ``.ipynb_checkpoints/`` directory under *slides_dir* (sorted)."""
    return sorted(p.resolve() for p in slides_dir.rglob(CHECKPOINT_DIR_NAME) if p.is_dir())


def _all_decks(slides_dir: Path) -> set[Path]:
    """Every deck on disk under *slides_dir*, extension-complete, checkpoint-free.

    Unlike ``find_slide_files_recursive`` (a ``*.py``-only rglob) this honours
    every supported program-language extension via :func:`is_slides_file`, so a
    ``.cs`` / ``.cpp`` orphan is not silently missed. Files inside a
    ``.ipynb_checkpoints/`` dir are excluded — they are reported as cruft, not
    as orphan decks. Decks inside underscore-prefixed dirs (``_archive``, …)
    are excluded too: discovery ignores them (issue #318), so they are
    *deliberately* parked, not forgotten — reporting them as orphans would
    drown the real signal.
    """
    return {
        p.resolve()
        for p in slides_dir.rglob("*")
        if p.is_file()
        and is_slides_file(p)
        and not _is_checkpoint(p)
        and not any(is_private_dir_name(part) for part in p.relative_to(slides_dir).parts[:-1])
    }


def find_orphans(
    spec_files: list[Path],
    slides_dir: Path,
    *,
    topic_map: dict[str, list[TopicMatch]] | None = None,
) -> OrphanReport:
    """Scan a course for decks no spec references, plus checkpoint cruft.

    *spec_files* is the **full** spec set (a deck unreferenced by one spec may
    be pulled in by another, so orphans are computed against the union shipping
    set). The returned :class:`OrphanReport` carries every orphan classified by
    intent and every ``.ipynb_checkpoints/`` directory found.
    """
    full_map = topic_map if topic_map is not None else build_topic_map(slides_dir)
    shipping = shipping_set(spec_files, slides_dir, topic_map=full_map)
    decks = _all_decks(slides_dir)

    orphans: list[Orphan] = []
    for path in sorted(decks - shipping, key=str):
        kind, reason = classify_orphan(path)
        orphans.append(Orphan(path=path, kind=kind, reason=reason))

    return OrphanReport(
        orphans=orphans,
        checkpoints=find_checkpoint_dirs(slides_dir),
        total_decks=len(decks),
        shipping_count=len(decks & shipping),
    )


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


_KIND_BLURB = {
    OrphanKind.SUPERSEDED: "superseded (explicit _old / _bak / numeric dup — likely safe to archive)",
    OrphanKind.ALTERNATE: "alternate (_partN / _short / _long — probably intentional content)",
    OrphanKind.UNKNOWN: "unknown (no marker — review before archiving)",
}


def render_report(report: OrphanReport, base: Path) -> str:
    """Human-readable orphan report, grouped by intent."""
    lines: list[str] = []
    by_kind = report.by_kind
    for kind in (OrphanKind.UNKNOWN, OrphanKind.SUPERSEDED, OrphanKind.ALTERNATE):
        group = by_kind[kind]
        if not group:
            continue
        lines.append(f"## {_KIND_BLURB[kind]} — {len(group)}")
        for orphan in group:
            lines.append(f"  {_rel(orphan.path, base)}  ({orphan.reason})")
        lines.append("")

    if report.checkpoints:
        lines.append(f"## .ipynb_checkpoints/ cruft — {len(report.checkpoints)}")
        for ckpt in report.checkpoints:
            lines.append(f"  {_rel(ckpt, base)}")
        lines.append("")

    if not report.orphans and not report.checkpoints:
        return (
            f"No orphans: all {report.total_decks} deck(s) are referenced by a spec, "
            "and no .ipynb_checkpoints/ cruft found."
        )

    counts = {k: len(v) for k, v in by_kind.items()}
    lines.append(
        f"{report.total_decks} deck(s) on disk, {report.shipping_count} shipping, "
        f"{len(report.orphans)} orphan(s) "
        f"({counts[OrphanKind.UNKNOWN]} unknown, {counts[OrphanKind.SUPERSEDED]} superseded, "
        f"{counts[OrphanKind.ALTERNATE]} alternate); "
        f"{len(report.checkpoints)} checkpoint dir(s)."
    )
    return "\n".join(lines)


def report_to_dict(report: OrphanReport) -> dict:
    """JSON-serializable orphan report."""
    return {
        "total_decks": report.total_decks,
        "shipping_count": report.shipping_count,
        "orphan_count": len(report.orphans),
        "by_kind": {kind.value: len(group) for kind, group in report.by_kind.items()},
        "orphans": [
            {
                "path": str(o.path),
                "kind": o.kind.value,
                "reason": o.reason,
            }
            for o in report.orphans
        ],
        "checkpoints": [str(c) for c in report.checkpoints],
    }
