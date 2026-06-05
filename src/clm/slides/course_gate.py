"""Course readiness gate (course-conversion tooling gap #3).

Runs the mechanical normalization passes a course conversion always needs — tag
migration, DE/EN interleaving, and ``slide_id`` minting (content-derived) — over
a spec's shipping decks (or a directory), then reports what was cleared
mechanically versus what still needs a human.

The split that matters: after the mechanical passes, the remaining work is either

- **mechanically-fixable** — would be cleared by re-running the passes (it isn't,
  in a dry run, because dry runs don't write); or
- **needs-author** — the normalizer *refused* to touch it because doing so safely
  needs a human: a ``slide_id`` with no derivable heading (hard refusal), a DE/EN
  pair whose code diverged too far to auto-interleave (similarity failure), or a
  DE/EN cell-count mismatch (a missing translation).

This is the report a conversion agent hand-built as
``docs/v18-remaining-validation-work.md``; making it a command turns every future
validator bump into ``clm course gate <spec> --apply``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clm.slides.assign_ids import AssignOptions
from clm.slides.normalizer import (
    ALL_OPERATIONS,
    Change,
    NormalizationResult,
    ReviewItem,
    normalize_file,
)
from clm.slides.validation_summary import ValidationSummary, summarize_findings
from clm.slides.validator import (
    find_slide_files_recursive,
    validate_files,
)

# The mechanical passes the gate runs by default, in normalizer order. Excludes
# nothing safe: each either edits deterministically or refuses to a review item.
DEFAULT_GATE_OPERATIONS: tuple[str, ...] = (
    "tag_migration",
    "workshop_tags",
    "interleaving",
    "slide_ids",
)

# ReviewItem.issue values that mean "a human must do this" — i.e. the normalizer
# declined to act because a safe automatic fix does not exist.
_NEEDS_AUTHOR_ISSUES = {
    "slide_id_hard_refusal",
    "count_mismatch",
    "similarity_failure",
}


@dataclass
class GateReport:
    """The outcome of a course-gate run."""

    scope: str
    deck_count: int
    applied: bool
    operations: list[str]
    baseline: ValidationSummary
    changes: list[Change] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)
    residual: ValidationSummary | None = None
    """Post-apply validation rollup. ``None`` in a dry run (nothing was written)."""

    @property
    def changes_by_operation(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.changes:
            counts[c.operation] = counts.get(c.operation, 0) + 1
        return counts

    @property
    def needs_author(self) -> list[ReviewItem]:
        """Review items that genuinely require a human (not soft, derivable cases)."""
        return [
            ri
            for ri in self.review_items
            if ri.issue in _NEEDS_AUTHOR_ISSUES or ri.issue.endswith("_hard_refusal")
        ]

    @property
    def is_clean(self) -> bool:
        """No author work and (if applied) no residual errors remain."""
        if self.needs_author:
            return False
        if self.residual is not None and self.residual.by_severity.get("error", 0) > 0:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "deck_count": self.deck_count,
            "applied": self.applied,
            "operations": self.operations,
            "baseline": self.baseline.to_dict(),
            "mechanical": {
                "total": len(self.changes),
                "by_operation": self.changes_by_operation,
            },
            "needs_author": [
                {
                    "file": ri.file,
                    "issue": ri.issue,
                    "suggestion": ri.suggestion,
                    "line": ri.details.get("line"),
                }
                for ri in self.needs_author
            ],
            "residual": self.residual.to_dict() if self.residual is not None else None,
            "is_clean": self.is_clean,
        }


def scope_decks(target: Path, slides_dir: Path) -> list[Path]:
    """The decks a gate run covers: a spec's shipping set, or a directory walk."""
    if target.is_file() and target.suffix.lower() == ".xml":
        from clm.core.course_spec import CourseSpec
        from clm.core.spec_decks import resolve_spec_decks

        spec = CourseSpec.from_file(target)
        return resolve_spec_decks(spec, slides_dir).deck_files
    if target.is_dir():
        return find_slide_files_recursive(target)
    if target.is_file():
        return [target]
    return []


def run_course_gate(
    target: Path,
    slides_dir: Path,
    *,
    operations: list[str] | None = None,
    apply: bool = False,
    checks: list[str] | None = None,
) -> GateReport:
    """Run the mechanical passes over *target* and report readiness.

    Args:
        target: A course spec ``.xml`` (uses its shipping set), a directory, or a
            single deck file.
        slides_dir: The course ``slides/`` directory.
        operations: Mechanical passes to run (normalizer operation names).
            Defaults to :data:`DEFAULT_GATE_OPERATIONS`.
        apply: When ``True``, write the mechanical fixes and re-validate; when
            ``False`` (default), run every pass in dry-run mode and report what
            *would* change without touching disk.
        checks: Validation checks to run (forwarded to the validator).

    Returns:
        A :class:`GateReport`.
    """
    ops = list(operations) if operations else list(DEFAULT_GATE_OPERATIONS)
    invalid = set(ops) - set(ALL_OPERATIONS)
    if invalid:
        raise ValueError(
            f"Unknown operation(s): {', '.join(sorted(invalid))}. "
            f"Valid: {', '.join(sorted(ALL_OPERATIONS))}"
        )

    decks = scope_decks(target, slides_dir)
    baseline = summarize_findings(validate_files(decks, checks=checks).findings)

    # Content-derived minting is the whole point: a conversion accepts the
    # heading-derived slug rather than refusing every id-less slide.
    assign_options = AssignOptions(accept_content_derived=True)

    combined = NormalizationResult()
    for deck in decks:
        result = normalize_file(
            deck,
            operations=ops,
            dry_run=not apply,
            assign_options=assign_options,
        )
        combined.files_modified += result.files_modified
        combined.changes.extend(result.changes)
        combined.review_items.extend(result.review_items)

    residual = None
    if apply:
        residual = summarize_findings(validate_files(decks, checks=checks).findings)

    return GateReport(
        scope=str(target),
        deck_count=len(decks),
        applied=apply,
        operations=ops,
        baseline=baseline,
        changes=combined.changes,
        review_items=combined.review_items,
        residual=residual,
    )


def render_report(report: GateReport, *, top_author: int = 40) -> list[str]:
    """Render a :class:`GateReport` as human-readable lines."""
    lines: list[str] = []
    mode = "applied" if report.applied else "dry-run"
    lines.append(f"Course gate ({mode}): {report.scope}")
    lines.append(f"Scope: {report.deck_count} deck(s); operations: {', '.join(report.operations)}")

    base = report.baseline.by_severity
    lines.append("")
    lines.append(f"Baseline: {base.get('error', 0)} error(s), {base.get('warning', 0)} warning(s)")

    verb = "Applied" if report.applied else "Would apply"
    lines.append("")
    lines.append(f"Mechanical passes — {verb} {len(report.changes)} change(s):")
    by_op = report.changes_by_operation
    if by_op:
        for op in sorted(by_op):
            lines.append(f"  {op}: {by_op[op]}")
    else:
        lines.append("  (none)")

    author = report.needs_author
    lines.append("")
    if author:
        lines.append(f"Needs author — {len(author)} item(s):")
        for ri in author[:top_author]:
            line_no = ri.details.get("line")
            loc = f"{ri.file}:{line_no}" if line_no else ri.file
            lines.append(f"  [{ri.issue}] {loc}")
            if ri.suggestion:
                lines.append(f"      {ri.suggestion}")
        remaining = len(author) - min(len(author), top_author)
        if remaining > 0:
            lines.append(f"  … and {remaining} more author item(s)")
    else:
        lines.append("Needs author — none.")

    if report.residual is not None:
        res = report.residual.by_severity
        lines.append("")
        lines.append(
            f"Residual after apply: {res.get('error', 0)} error(s), "
            f"{res.get('warning', 0)} warning(s)"
        )

    lines.append("")
    if report.is_clean:
        lines.append("Readiness: MECHANICALLY CLEAN — no author work detected.")
    else:
        n = len(author)
        suffix = f"{n} author item(s)" if n else "residual errors remain"
        lines.append(f"Readiness: NEEDS AUTHOR — {suffix}.")

    return lines
