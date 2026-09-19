"""Model-free comparison artifacts shared by agent acceptance and autopilot."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clm.voiceover.bullet_schema import BulletOutcome, BulletStatus
from clm.voiceover.slide_matcher import MatchKind


@dataclass
class SlideComparison:
    """Per-slide comparison verdicts, including unmatched and failed pairs."""

    key: str
    kind: MatchKind
    target_index: int | None
    source_index: int | None
    content_similarity: float = 0.0
    outcomes: list[BulletOutcome] = field(default_factory=list)
    notes: str | None = None
    error: str | None = None

    def status_counts(self) -> dict[BulletStatus, int]:
        tot: dict[BulletStatus, int] = dict.fromkeys(BulletStatus, 0)
        for outcome in self.outcomes:
            tot[outcome.status] = tot.get(outcome.status, 0) + 1
        return tot

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "target_index": self.target_index,
            "source_index": self.source_index,
            "content_similarity": self.content_similarity,
            "outcomes": [o.to_json() for o in self.outcomes],
            "notes": self.notes,
            "error": self.error,
        }


@dataclass
class CompareReport:
    """Canonical comparison artifact, independent of who supplied judgment."""

    source: Path
    target: Path
    language: str
    slides: list[SlideComparison] = field(default_factory=list)

    def status_totals(self) -> dict[BulletStatus, int]:
        tot: dict[BulletStatus, int] = dict.fromkeys(BulletStatus, 0)
        for comp in self.slides:
            for outcome in comp.outcomes:
                tot[outcome.status] = tot.get(outcome.status, 0) + 1
        return tot

    def kind_totals(self) -> dict[MatchKind, int]:
        tot: dict[MatchKind, int] = dict.fromkeys(MatchKind, 0)
        for comp in self.slides:
            tot[comp.kind] = tot.get(comp.kind, 0) + 1
        return tot

    def to_json(self) -> dict:
        totals = self.status_totals()
        kinds = self.kind_totals()
        return {
            "source": str(self.source),
            "target": str(self.target),
            "language": self.language,
            "slide_count": len(self.slides),
            "status_totals": {s.value: totals[s] for s in BulletStatus},
            "kind_totals": {k.value: kinds[k] for k in MatchKind},
            "slides": [c.to_json() for c in self.slides],
        }


def render_markdown(report_json: dict) -> str:
    """Render a canonical comparison JSON payload as Markdown."""

    def _escape(s: str | None) -> str:
        if s is None:
            return ""
        return s.replace("|", r"\|").replace("\n", " ")

    source = report_json.get("source", "")
    target = report_json.get("target", "")
    language = report_json.get("language", "")
    status_totals = report_json.get("status_totals", {})
    kind_totals = report_json.get("kind_totals", {})
    slides = report_json.get("slides", [])

    lines: list[str] = []
    title = Path(target).stem if target else "compare report"
    lines.append(f"# Voiceover Comparison — {title}")
    lines.append("")
    lines.append(f"- Source: `{source}`")
    lines.append(f"- Target: `{target}`")
    lines.append(f"- Language: `{language}`")
    lines.append(f"- Slides: {report_json.get('slide_count', len(slides))}")

    totals_cells = [
        f"{status}={status_totals[status]}" for status in status_totals if status_totals[status]
    ]
    if totals_cells:
        lines.append(f"- Bullet totals: {', '.join(totals_cells)}")
    kind_cells = [f"{kind}={kind_totals[kind]}" for kind in kind_totals if kind_totals[kind]]
    if kind_cells:
        lines.append(f"- Slide buckets: {', '.join(kind_cells)}")
    lines.append("")
    lines.append("## Summary per slide")
    lines.append("")
    lines.append("| # | Slide | Kind | Covered | Rewritten | Added | Dropped | Review |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    for idx, slide in enumerate(slides, start=1):
        counts: dict[str, int] = dict.fromkeys(
            ("covered", "rewritten", "added", "dropped", "manual_review"), 0
        )
        for outcome in slide.get("outcomes", []):
            status = outcome.get("status")
            if status in counts:
                counts[status] += 1
        lines.append(
            "| {i} | `{key}` | {kind} | {c} | {rw} | {a} | {d} | {mr} |".format(
                i=idx,
                key=_escape(slide.get("key", "")),
                kind=slide.get("kind", ""),
                c=counts["covered"] or "",
                rw=counts["rewritten"] or "",
                a=counts["added"] or "",
                d=counts["dropped"] or "",
                mr=counts["manual_review"] or "",
            )
        )
    lines.append("")
    bucket_order = [
        ("dropped", "Dropped in current slides (present in source, absent in target)"),
        ("added", "Added in current slides (absent in source)"),
        ("rewritten", "Rewritten (same concept, substantively different wording)"),
        ("manual_review", "Manual review (judge could not classify confidently)"),
    ]
    for status, heading in bucket_order:
        matching_outcomes: list[tuple[dict, dict]] = []
        for slide in slides:
            for outcome in slide.get("outcomes", []):
                if outcome.get("status") == status:
                    matching_outcomes.append((slide, outcome))
        if not matching_outcomes:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for slide, outcome in matching_outcomes:
            lines.append(f"### `{slide.get('key', '?')}`")
            if outcome.get("source"):
                lines.append(f"- **source:** {outcome['source']}")
            if outcome.get("target"):
                lines.append(f"- **target:** {outcome['target']}")
            if outcome.get("note"):
                lines.append(f"- _{outcome['note']}_")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
