"""Category rollup for slide-validation findings (gap #2, ``--summary``).

A corpus validate can emit thousands of findings; a flat list is unreadable.
This module rolls findings up into a compact triage table so an author can see
"what kinds of problems, how many, and which decks are worst" at a glance.

Two axes, both derived from :class:`clm.slides.validator.Finding`:

- **category × severity** — the validator's own ``category`` field
  (``format`` / ``pairing`` / ``tags``) crossed with severity. This is exact
  and always correct.
- **kind** — a finer bucket (missing-slide_id, adjacency, count-mismatch, …)
  derived from the message text via :func:`classify_kind`. This is a *display
  heuristic* over message signatures, with an ``other`` fallback so nothing is
  dropped or miscounted away; treat the category×severity axis as authoritative.

Plus a **per-file** breakdown (decks with the most findings), since "which decks
need work" is the question a conversion gate actually asks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.slides.validator import Finding


# Ordered (kind, predicate-substring) rules. First match wins, so more specific
# signatures come before broader ones. Substrings are matched case-insensitively
# against the finding message. Kept deliberately small and explicit — see the
# module docstring on why this is a supplementary heuristic, not the source of
# truth.
_KIND_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("missing-slide_id", ("missing slide_id",)),
    ("slide_id-slug", ("not a valid kebab-case",)),
    ("slide_id-mismatch", ("slide_id",)),  # remaining slide_id findings (parity/order)
    ("adjacency", ("adjacent", "intervening", "not adjacent")),
    ("count-mismatch", ("count", "number of cells", "mismatched cell")),
    ("start-completed", ("'start'", "'completed'", "start tag", "completed tag")),
    ("malformed-marker", ("malformed", "does not start with")),
    ("unexpected-tag", ("unrecognized tag", "is not expected on")),
    ("shared-cell", ("shared cell", "diverge")),
    ("voiceover", ("voiceover", "for_slide")),
]


def classify_kind(message: str) -> str:
    """Bucket a finding *message* into a coarse kind (heuristic; see module doc)."""
    lowered = message.lower()
    for kind, needles in _KIND_RULES:
        if any(n in lowered for n in needles):
            return kind
    return "other"


@dataclass
class FileRollup:
    """Per-file finding counts."""

    file: str
    errors: int = 0
    warnings: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        return self.errors + self.warnings + self.info


@dataclass
class ValidationSummary:
    """A rolled-up view of a flat finding list."""

    total: int = 0
    by_severity: Counter = field(default_factory=Counter)
    by_category_severity: dict[str, Counter] = field(default_factory=dict)
    by_kind: Counter = field(default_factory=Counter)
    by_file: list[FileRollup] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "by_severity": dict(self.by_severity),
            "by_category_severity": {
                cat: dict(counts) for cat, counts in self.by_category_severity.items()
            },
            "by_kind": dict(self.by_kind),
            "by_file": [
                {
                    "file": fr.file,
                    "errors": fr.errors,
                    "warnings": fr.warnings,
                    "info": fr.info,
                    "total": fr.total,
                }
                for fr in self.by_file
            ],
        }


def summarize_findings(findings: list[Finding]) -> ValidationSummary:
    """Roll *findings* up by severity, category, kind, and file."""
    summary = ValidationSummary(total=len(findings))
    files: dict[str, FileRollup] = {}

    for f in findings:
        summary.by_severity[f.severity] += 1
        summary.by_category_severity.setdefault(f.category, Counter())[f.severity] += 1
        summary.by_kind[classify_kind(f.message)] += 1

        rollup = files.setdefault(f.file or "<unknown>", FileRollup(file=f.file or "<unknown>"))
        if f.severity == "error":
            rollup.errors += 1
        elif f.severity == "warning":
            rollup.warnings += 1
        else:
            rollup.info += 1

    # Worst decks first: errors, then warnings, then total, then path for stability.
    summary.by_file = sorted(
        files.values(),
        key=lambda fr: (-fr.errors, -fr.warnings, -fr.total, fr.file),
    )
    return summary


def render_summary(summary: ValidationSummary, *, top_files: int = 20) -> list[str]:
    """Render *summary* as human-readable lines (no trailing newlines)."""
    lines: list[str] = []
    sev = summary.by_severity
    lines.append(
        f"{summary.total} finding(s): "
        f"{sev.get('error', 0)} error(s), "
        f"{sev.get('warning', 0)} warning(s), "
        f"{sev.get('info', 0)} info"
    )

    if summary.total == 0:
        return lines

    lines.append("")
    lines.append("By category:")
    for category in sorted(summary.by_category_severity):
        counts = summary.by_category_severity[category]
        detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        lines.append(f"  {category}: {detail}")

    lines.append("")
    lines.append("By kind:")
    for kind, n in summary.by_kind.most_common():
        lines.append(f"  {kind}: {n}")

    shown = summary.by_file[:top_files]
    lines.append("")
    label = f"Top {len(shown)} deck(s) by finding count:"
    lines.append(label)
    for fr in shown:
        lines.append(f"  {fr.file}: {fr.errors} error(s), {fr.warnings} warning(s)")
    remaining = len(summary.by_file) - len(shown)
    if remaining > 0:
        lines.append(f"  … and {remaining} more deck(s) with findings")

    return lines
