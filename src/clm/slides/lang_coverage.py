"""DE/EN completeness report for slide decks (gap #8).

Among count-mismatch validation errors, two very different situations hide:
a deck that exists in only one language (needs *translation* — a big job) and
a deck that is bilingual but off by a cell or two (a small *alignment* fix).
Telling them apart at corpus scale means counting ``lang="de"`` vs ``lang="en"``
slide cells per deck, which course conversions did with throwaway scripts.

This module makes that a report. It handles both deck shapes:

- **bilingual** decks (no ``.de``/``.en`` filename tag) carry both languages'
  cells interleaved — we count each language within the file;
- **split** decks (``*.de.py`` / ``*.en.py``) hold one language per file — we
  count the pair's two halves together, and a half whose twin is absent counts
  the missing side as zero (i.e. that language is untranslated).

Only slide/subslide *start* cells are counted; narrative (voiceover/notes)
cells inherit their slide and are excluded so a deck with one-language speaker
notes is not misreported as imbalanced.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

from clm.slides.pairing import iter_split_pairs, split_lang_tag
from clm.slides.raw_cells import split_cells

__all__ = [
    "CoverageEntry",
    "CoverageReport",
    "CoverageStatus",
    "classify_counts",
    "count_languages",
    "render_report",
    "report_to_dict",
    "scan_coverage",
]


class CoverageStatus(enum.Enum):
    """DE/EN completeness of one deck unit."""

    BALANCED = "balanced"  # equal DE and EN slide counts (incl. 0/0)
    DE_ONLY = "de_only"  # DE present, EN missing — needs EN translation
    EN_ONLY = "en_only"  # EN present, DE missing — needs DE translation
    IMBALANCED = "imbalanced"  # both present, counts differ — alignment fix


def count_languages(text: str) -> tuple[int, int]:
    """Return ``(de_cells, en_cells)`` — slide/subslide start cells by language.

    Narrative cells and language-neutral cells (``lang`` unset) are not counted.
    """
    de = en = 0
    _, cells = split_cells(text)
    for cell in cells:
        if not cell.metadata.is_slide_start:
            continue
        if cell.metadata.lang == "de":
            de += 1
        elif cell.metadata.lang == "en":
            en += 1
    return de, en


def classify_counts(de: int, en: int) -> CoverageStatus:
    """Classify a ``(de, en)`` slide-count pair."""
    if de == en:
        return CoverageStatus.BALANCED
    if en == 0:
        return CoverageStatus.DE_ONLY
    if de == 0:
        return CoverageStatus.EN_ONLY
    return CoverageStatus.IMBALANCED


@dataclass
class CoverageEntry:
    """DE/EN completeness of one deck unit (a file or a split pair)."""

    label: str
    kind: str  # "bilingual" / "split-pair" / "split-half"
    de_cells: int
    en_cells: int
    status: CoverageStatus

    @property
    def delta(self) -> int:
        return abs(self.de_cells - self.en_cells)


@dataclass
class CoverageReport:
    """Outcome of a DE/EN coverage scan."""

    entries: list[CoverageEntry] = field(default_factory=list)

    @property
    def by_status(self) -> dict[CoverageStatus, list[CoverageEntry]]:
        out: dict[CoverageStatus, list[CoverageEntry]] = {s: [] for s in CoverageStatus}
        for entry in self.entries:
            out[entry.status].append(entry)
        return out

    @property
    def incomplete(self) -> list[CoverageEntry]:
        """Everything that is not balanced (needs translation or alignment)."""
        return [e for e in self.entries if e.status != CoverageStatus.BALANCED]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def scan_coverage(files: list[Path]) -> CoverageReport:
    """Build a DE/EN coverage report over a set of slide files.

    Split ``.de.py`` / ``.en.py`` halves present in *files* are scored as one
    pair; a half whose twin is absent from *files* is scored alone (its missing
    language counts as zero). Bilingual files are scored in place.
    """
    pairs, solos = iter_split_pairs(files)
    report = CoverageReport()

    for de_path, en_path in pairs:
        de_text, en_text = _read(de_path), _read(en_path)
        if de_text is None or en_text is None:
            continue
        de_cells = count_languages(de_text)[0]
        en_cells = count_languages(en_text)[1]
        report.entries.append(
            CoverageEntry(
                label=str(de_path),
                kind="split-pair",
                de_cells=de_cells,
                en_cells=en_cells,
                status=classify_counts(de_cells, en_cells),
            )
        )

    for solo in solos:
        text = _read(solo)
        if text is None:
            continue
        tag = split_lang_tag(solo)
        de_cells, en_cells = count_languages(text)
        if tag is None:
            kind = "bilingual"
        else:
            # A lone split half: only its own language is present on disk.
            kind = "split-half"
            de_cells, en_cells = (de_cells, 0) if tag == "de" else (0, en_cells)
        report.entries.append(
            CoverageEntry(
                label=str(solo),
                kind=kind,
                de_cells=de_cells,
                en_cells=en_cells,
                status=classify_counts(de_cells, en_cells),
            )
        )

    report.entries.sort(key=lambda e: e.label)
    return report


_STATUS_BLURB = {
    CoverageStatus.EN_ONLY: "EN-only (needs DE translation)",
    CoverageStatus.DE_ONLY: "DE-only (needs EN translation)",
    CoverageStatus.IMBALANCED: "imbalanced (both languages present, counts differ)",
}


def _rel(label: str, base: Path | None) -> str:
    if base is None:
        return label
    try:
        return str(Path(label).relative_to(base))
    except ValueError:
        return label


def render_report(report: CoverageReport, *, base: Path | None = None) -> str:
    """Human-readable coverage report; balanced decks are summarized, not listed."""
    by_status = report.by_status
    if not report.incomplete:
        return f"All {len(report.entries)} deck(s) are DE/EN balanced."

    lines: list[str] = []
    for status in (CoverageStatus.EN_ONLY, CoverageStatus.DE_ONLY, CoverageStatus.IMBALANCED):
        group = by_status[status]
        if not group:
            continue
        lines.append(f"## {_STATUS_BLURB[status]} — {len(group)}")
        for entry in group:
            extra = f", Δ{entry.delta}" if status == CoverageStatus.IMBALANCED else ""
            lines.append(
                f"  {_rel(entry.label, base)}  "
                f"[{entry.kind}] de={entry.de_cells} en={entry.en_cells}{extra}"
            )
        lines.append("")

    counts = {s: len(g) for s, g in by_status.items()}
    lines.append(
        f"{len(report.entries)} deck(s): {counts[CoverageStatus.BALANCED]} balanced, "
        f"{counts[CoverageStatus.EN_ONLY]} EN-only, {counts[CoverageStatus.DE_ONLY]} DE-only, "
        f"{counts[CoverageStatus.IMBALANCED]} imbalanced."
    )
    return "\n".join(lines)


def report_to_dict(report: CoverageReport) -> dict:
    """JSON-serializable coverage report."""
    return {
        "total": len(report.entries),
        "by_status": {s.value: len(g) for s, g in report.by_status.items()},
        "decks": [
            {
                "label": e.label,
                "kind": e.kind,
                "de_cells": e.de_cells,
                "en_cells": e.en_cells,
                "delta": e.delta,
                "status": e.status.value,
            }
            for e in report.entries
        ],
    }
