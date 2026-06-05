"""Flag low-quality ``slide_id`` slugs for review (gap #6).

Bulk minting with ``assign-ids --accept-content-derived`` can produce
thousands of ids, most fine but a minority low-information: single generic
tokens (``data`` / ``true`` / ``value``), very short code-identifier-shaped
slugs (``cp`` / ``df`` / ``os``), or slugs that hit the 30-char cap and lost
their trailing words. Scanning all 3,000 by hand to find those is the chore
this module removes: it classifies each slug by cheap, source-independent
heuristics so the author reviews only the flagged minority.

The heuristics judge the slug *string* — they do not re-run extraction or
need the cell. A flag is a "worth a look", not a verdict: ``introduction``
is a single token and perfectly good, so :data:`SlugIssue.SINGLE_TOKEN` is
``low`` severity. The high-confidence signals are the very-short and generic
ones. ``title`` (the reserved id for ``header()`` macro slides) is never
flagged.
"""

from __future__ import annotations

import enum
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from clm.slides.pairing import TITLE_SLIDE_ID
from clm.slides.raw_cells import split_cells
from clm.slides.slug import MAX_SLUG_LENGTH, strip_preserve_marker

__all__ = [
    "ISSUE_SEVERITY",
    "SEVERITY_ORDER",
    "SlugFinding",
    "SlugIssue",
    "SlugReport",
    "classify_slug",
    "render_report",
    "report_to_dict",
    "scan_slug_quality",
]

# A single token at or below this length reads as an abbreviation / code
# identifier (``cp``, ``df``, ``os``, ``np``) rather than a descriptive title.
VERY_SHORT_MAX = 3

# A slug this close to the 30-char cap was almost certainly trimmed: the
# slugifier drops trailing words (or hard-truncates a long first token) only
# when the joined form would exceed MAX_SLUG_LENGTH, so trailing context was
# likely lost.
TRUNCATION_THRESHOLD = MAX_SLUG_LENGTH - 2  # 28

# Single-token slugs that carry no topical information. Deliberately curated
# and conservative — these are flagged ``high`` because a lone one of them is
# almost never the title an author would choose.
GENERIC_WORDS = frozenset(
    {
        "data",
        "value",
        "values",
        "true",
        "false",
        "none",
        "null",
        "foo",
        "bar",
        "baz",
        "tmp",
        "temp",
        "output",
        "input",
        "result",
        "results",
        "code",
        "example",
        "examples",
        "demo",
        "test",
        "todo",
        "stuff",
        "thing",
        "things",
        "item",
        "items",
        "misc",
        "other",
        "untitled",
    }
)


class SlugIssue(enum.Enum):
    """A reviewable quality signal on a slug."""

    VERY_SHORT = "very_short"
    GENERIC = "generic"
    POSSIBLY_TRUNCATED = "possibly_truncated"
    SINGLE_TOKEN = "single_token"


ISSUE_SEVERITY: dict[SlugIssue, str] = {
    SlugIssue.VERY_SHORT: "high",
    SlugIssue.GENERIC: "high",
    SlugIssue.POSSIBLY_TRUNCATED: "medium",
    SlugIssue.SINGLE_TOKEN: "low",
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def classify_slug(slide_id: str) -> list[SlugIssue]:
    """Return the quality issues a slug exhibits (empty == looks fine).

    Heuristics on the bare (preserve-marker-stripped) slug:

    - length ``>= TRUNCATION_THRESHOLD`` → ``POSSIBLY_TRUNCATED``;
    - exactly one token of length ``<= VERY_SHORT_MAX`` → ``VERY_SHORT``;
    - exactly one token in :data:`GENERIC_WORDS` → ``GENERIC``;
    - any other single-token slug → ``SINGLE_TOKEN`` (informational).

    The reserved ``title`` id and the empty string are never flagged.
    """
    bare = strip_preserve_marker(slide_id)
    if not bare or bare == TITLE_SLIDE_ID:
        return []

    issues: list[SlugIssue] = []
    if len(bare) >= TRUNCATION_THRESHOLD:
        issues.append(SlugIssue.POSSIBLY_TRUNCATED)

    tokens = bare.split("-")
    if len(tokens) == 1:
        tok = tokens[0]
        if len(tok) <= VERY_SHORT_MAX:
            issues.append(SlugIssue.VERY_SHORT)
        elif tok in GENERIC_WORDS:
            issues.append(SlugIssue.GENERIC)
        else:
            issues.append(SlugIssue.SINGLE_TOKEN)

    return issues


@dataclass
class SlugFinding:
    """One slide_id flagged for review."""

    file: str
    line: int
    slide_id: str
    issues: list[SlugIssue]

    @property
    def severity(self) -> str:
        """The worst (highest-confidence) severity among this slug's issues."""
        return min(
            (ISSUE_SEVERITY[i] for i in self.issues),
            key=lambda s: SEVERITY_ORDER[s],
            default="low",
        )


@dataclass
class SlugReport:
    """Outcome of scanning a deck set for low-quality slugs."""

    findings: list[SlugFinding] = field(default_factory=list)
    total_ids: int = 0
    files_scanned: int = 0

    @property
    def by_issue(self) -> dict[SlugIssue, int]:
        counts: dict[SlugIssue, int] = defaultdict(int)
        for finding in self.findings:
            for issue in finding.issues:
                counts[issue] += 1
        return dict(counts)

    @property
    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for finding in self.findings:
            counts[finding.severity] += 1
        return dict(counts)

    def at_or_above(self, min_severity: str) -> list[SlugFinding]:
        ceiling = SEVERITY_ORDER[min_severity]
        return [f for f in self.findings if SEVERITY_ORDER[f.severity] <= ceiling]


def scan_slug_quality(files: list[Path]) -> SlugReport:
    """Classify every slide_id on the given decks; collect the flagged ones.

    Only slide/subslide *start* cells are inspected — narrative cells inherit
    their slide's id, so counting them would double-report. Within one file a
    given bare id is reported once (at its first occurrence), so a bilingual
    deck's DE/EN twin cells (which share an id) yield a single finding.
    """
    report = SlugReport()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        report.files_scanned += 1
        _, cells = split_cells(text)
        file_str = str(path)
        seen_in_file: set[str] = set()
        for cell in cells:
            if not cell.metadata.is_slide_start:
                continue
            raw = cell.metadata.slide_id
            if not raw:
                continue
            bare = strip_preserve_marker(raw)
            if bare in seen_in_file:
                continue
            seen_in_file.add(bare)
            report.total_ids += 1
            issues = classify_slug(raw)
            if issues:
                report.findings.append(
                    SlugFinding(
                        file=file_str,
                        line=cell.line_number,
                        slide_id=bare,
                        issues=issues,
                    )
                )
    return report


def render_report(report: SlugReport, *, min_severity: str = "low") -> str:
    """Human-readable slug-quality report at or above ``min_severity``."""
    shown = report.at_or_above(min_severity)
    shown.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line))

    if not report.findings:
        return f"Scanned {report.total_ids} slide_id(s) in {report.files_scanned} file(s) — all look fine."

    lines: list[str] = []
    for finding in shown:
        tags = ", ".join(i.value for i in finding.issues)
        lines.append(
            f"[{finding.severity:<6}] {finding.file}:{finding.line} "
            f'slide_id="{finding.slide_id}" — {tags}'
        )
    lines.append("")
    sev = report.by_severity
    lines.append(
        f"Scanned {report.total_ids} slide_id(s) in {report.files_scanned} file(s): "
        f"{len(report.findings)} flagged "
        f"({sev.get('high', 0)} high, {sev.get('medium', 0)} medium, {sev.get('low', 0)} low)."
    )
    if min_severity != "low" and len(shown) != len(report.findings):
        lines.append(f"Showing {len(shown)} at severity >= {min_severity}.")
    return "\n".join(lines)


def report_to_dict(report: SlugReport) -> dict:
    """JSON-serializable slug-quality report."""
    return {
        "total_ids": report.total_ids,
        "files_scanned": report.files_scanned,
        "flagged": len(report.findings),
        "by_severity": report.by_severity,
        "by_issue": {issue.value: count for issue, count in report.by_issue.items()},
        "findings": [
            {
                "file": f.file,
                "line": f.line,
                "slide_id": f.slide_id,
                "severity": f.severity,
                "issues": [i.value for i in f.issues],
            }
            for f in report.findings
        ],
    }
